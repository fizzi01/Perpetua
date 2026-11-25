#!/usr/bin/env bash
#
# Exercise enable_uinput.sh and the daemon-side installer in a container.
#
# The script is the privileged half of the permission flow: it elevates
# itself and, inside an AppImage, copies itself out of a FUSE mount that root
# cannot read. Both are reproduced here — the squashfs is mounted over FUSE by
# an unprivileged user, and a two-line pkexec shim stands in for polkit (the
# container is already root, so the shim elevates through sudo). What this
# proves is the escalation path and the copy-out, not polkit itself.
#
# Needs: docker, and /dev/fuse on the host (Docker Desktop provides it).
#
# Read-only against the local filesystem; only docker containers are created.
#
# Usage:
#   scripts/test_udev_setup.sh [--image IMAGE] [--keep]
#
# Exit status: 0 if every assertion passed, 1 otherwise.
#
set -euo pipefail

IMAGE="ubuntu:24.04"
KEEP=0
while [ $# -gt 0 ]; do
  case "$1" in
    --image) IMAGE="$2"; shift 2 ;;
    --keep)  KEEP=1; shift ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "error: unknown flag $1" >&2; exit 2 ;;
  esac
done

[ -f "pyproject.toml" ] || { echo "error: run from the project root" >&2; exit 1; }
command -v docker >/dev/null || { echo "error: docker not available" >&2; exit 1; }

TMPDIR_HOST="$(mktemp -d)"
[ "$KEEP" -eq 1 ] || trap 'rm -rf "$TMPDIR_HOST"' EXIT

cat > "$TMPDIR_HOST/inner.sh" <<'INNER'
#!/bin/bash
set -u
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq squashfs-tools squashfuse fuse3 sudo udev kbd python3 \
  python3-pip >/dev/null 2>&1
# structlog is the only third-party import on the utils.permissions chain.
pip3 install --break-system-packages --no-deps -q structlog >/dev/null 2>&1

RULES=/etc/udev/rules.d
MODULES=/etc/modules-load.d/perpetua-uinput.conf
pass=0; fail=0
# eval, not "$@": a pipe in the expression would otherwise swallow the
# PASS/FAIL line itself and the check would vanish unnoticed.
check() {
  local label="$1"; shift
  if eval "$*" >/dev/null 2>&1; then echo "  PASS  $label"; pass=$((pass+1))
  else echo "  FAIL  $label"; fail=$((fail+1)); fi
}
clean() { rm -f "$RULES/01-perpetua-keyboard.rules" "$RULES/12-input.rules" "$MODULES"; }

useradd -m tester
echo "tester ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/tester
# Mirror the repo layout: _resolve_setup_script() walks up from
# src/utils/permissions/ to the project root to find scripts/.
mkdir -p /work && cp -r /src/src /src/scripts /work/
chmod -R a+rX /work

# The bundle layout, inside a squashfs the user mounts over FUSE.
mkdir -p /build/usr/lib/Perpetua
cp /work/scripts/enable_uinput.sh /build/usr/lib/Perpetua/
mksquashfs /build /bundle.squashfs -noappend >/dev/null
chmod 666 /dev/fuse
MNT=/tmp/.mount_perpetua; mkdir -p "$MNT"; chown tester:tester "$MNT"
su tester -s /bin/sh -c "squashfuse /bundle.squashfs $MNT" || {
  echo "::error:: could not mount the squashfs over FUSE"; exit 1; }
SCRIPT="$MNT/usr/lib/Perpetua/enable_uinput.sh"

echo "== A. root: the script proceeds without elevating =="
clean
sh /work/scripts/enable_uinput.sh configure >/tmp/a.log 2>&1
check "uinput rule written"        "test -f $RULES/01-perpetua-keyboard.rules"
check "tty rule written"           "test -f $RULES/12-input.rules"
check "rule owned by root"         "test \"\$(stat -c %U $RULES/01-perpetua-keyboard.rules)\" = root"
check "rule mode 644"              "test \"\$(stat -c %a $RULES/01-perpetua-keyboard.rules)\" = 644"
check "modules-load.d written"     "grep -qx uinput $MODULES"
# Plain root, no SUDO_USER and no PKEXEC_UID: there is no caller to add.
check "no user to add is reported"  "grep -q 'could not detect the installing user' /tmp/a.log"

echo "== B. idempotency =="
sh /work/scripts/enable_uinput.sh configure >/tmp/b.log 2>&1
check "reports already up to date" "grep -q 'already up to date' /tmp/b.log"
check "no backup created"          "test -z \"\$(ls $RULES/*.bak.* 2>/dev/null)\""

echo "== C. a different rule is backed up =="
echo garbage > "$RULES/01-perpetua-keyboard.rules"
sh /work/scripts/enable_uinput.sh configure >/dev/null 2>&1
check "backup created"             "ls $RULES/01-perpetua-keyboard.rules.bak.* >/dev/null"

echo "== D. unprivileged user, through the pkexec shim, from the FUSE mount =="
printf '#!/bin/sh\nexec sudo -n "$@"\n' > /usr/local/bin/pkexec
chmod 755 /usr/local/bin/pkexec
clean; gpasswd -d tester input >/dev/null 2>&1
su tester -s /bin/sh -c "DISPLAY=:0 sh $SCRIPT configure" </dev/null >/tmp/d.log 2>&1
check "rules written from the mount" "test -f $RULES/01-perpetua-keyboard.rules"
check "caller added to 'input'"      "id -nG tester | grep -qw input"
# No /dev/uinput in a container, so the group fallback is what grants access
# and the logout note has to be shown.
check "logout note shown"            "grep -q 'log out and back in' /tmp/d.log"
check "staging cleaned up"           "test -z \"\$(ls -d /tmp/perpetua-udev-* 2>/dev/null)\""

echo "== E. the script really copies itself out of the mount =="
# Root cannot read the FUSE mount, so passing $0 straight to pkexec fails.
# argv reaching pkexec is: /bin/sh <script> configure -> $2 is the script.
printf '#!/bin/sh\necho "$2" > /tmp/staged-path\nexec sudo -n "$@"\n' > /usr/local/bin/pkexec
clean
su tester -s /bin/sh -c "DISPLAY=:0 sh $SCRIPT configure" </dev/null >/dev/null 2>&1
check "elevated path is a staging copy" "grep -q '^/tmp/perpetua-udev-' /tmp/staged-path"
check "elevated path is not the source" "! grep -q '^$MNT' /tmp/staged-path"

echo "== F. no escalation path: fails cleanly, writes nothing =="
printf '#!/bin/sh\nexec sudo -n "$@"\n' > /usr/local/bin/pkexec
mv /usr/local/bin/pkexec /usr/local/bin/pkexec.off; mv /usr/bin/sudo /usr/bin/sudo.off
clean
su tester -s /bin/sh -c "env -u DISPLAY -u WAYLAND_DISPLAY sh $SCRIPT configure" \
  </dev/null >/tmp/f.log 2>&1
rc=$?
check "exits non-zero"               "test $rc -ne 0"
check "writes nothing"               "test ! -f $RULES/01-perpetua-keyboard.rules"
check "prints a manual fallback"     "grep -q 'sudo sh' /tmp/f.log"
# $0 points inside the mount, which disappears with the process and which
# root cannot read: the suggested path has to outlive both.
check "fallback path is outside the mount" "! grep -qE 'sudo sh +$MNT' /tmp/f.log"
check "fallback path exists"         "test -f \"\$(sed -n 's/.*sudo sh  *//p' /tmp/f.log | tail -1)\""
mv /usr/local/bin/pkexec.off /usr/local/bin/pkexec; mv /usr/bin/sudo.off /usr/bin/sudo

echo "== G. the daemon-side installer runs the script =="
clean

su tester -s /bin/sh -c "DISPLAY=:0 PYTHONPATH=/work/src python3 -c \"
import sys
from utils.permissions._linux import install_udev_rules
ok, detail = install_udev_rules()
print(detail)
sys.exit(0 if ok else 1)\"" </dev/null >/tmp/g.log 2>&1
g_rc=$?
check "install_udev_rules() succeeded" "test $g_rc -eq 0"
check "rules written by the daemon path" "test -f $RULES/12-input.rules"

echo
echo "== $pass passed, $fail failed =="
exit $((fail > 0))
INNER
chmod +x "$TMPDIR_HOST/inner.sh"

echo "Running the udev setup suite in $IMAGE…"
docker run --rm \
  --device /dev/fuse --cap-add SYS_ADMIN --security-opt apparmor:unconfined \
  -v "$(pwd):/src:ro" -v "$TMPDIR_HOST/inner.sh:/inner.sh:ro" \
  "$IMAGE" bash /inner.sh
