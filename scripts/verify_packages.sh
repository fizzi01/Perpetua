#!/usr/bin/env bash
#
# Run the same smoke-test the CI does, but locally via docker.
#
# Spins up each target distro in a container, installs the artifact, and
# checks that every bundled ELF resolves, that the dlopen'd libraries and
# helper binaries are present, and that the app launches under Xvfb.
#
# For the AppImage the container gets ONLY what the excludelist covers;
# pre-installing gtk3/webkit2gtk/libei would make the test tautological.
#
# Read-only against the local filesystem; only docker containers are created.
#
# Usage:
#   scripts/verify_packages.sh [--arch ARCH] [--distros LIST] [--keep]
#                              [--artifacts DIR]
#
#   --arch ARCH       x86_64 (default) | aarch64. Picks which artifact
#                     variant to feed into the containers; aarch64 needs
#                     a host that can run aarch64 containers natively
#                     (Apple Silicon, ARM workstation, qemu-user
#                     installed).
#   --distros LIST    Comma-separated subset of {ubuntu,debian,fedora,
#                     opensuse,ubuntu-appimage,fedora-appimage,arch}.
#                     Default: every one of them except opensuse.
#   --artifacts DIR   Where to find the .deb/.rpm/.AppImage. Default:
#                     scans both per-arch build dirs and the .build/ root
#                     for renamed CI artifacts.
#   --keep            Don't remove the containers on exit (useful for
#                     post-mortem ``docker exec``).
#
# Exit status: 0 if every selected distro passed, 1 otherwise. A per-
# distro one-line summary is printed at the end.
#
set -euo pipefail

# ── Args ──────────────────────────────────────────────────────────────────────
ARCH="x86_64"
DISTROS_RAW="ubuntu,debian,fedora,ubuntu-appimage,fedora-appimage,arch"
ARTIFACTS_DIR=""
KEEP=0

while [ $# -gt 0 ]; do
  case "$1" in
    --arch)       ARCH="$2"; shift 2 ;;
    --distros)    DISTROS_RAW="$2"; shift 2 ;;
    --artifacts)  ARTIFACTS_DIR="$2"; shift 2 ;;
    --keep)       KEEP=1; shift ;;
    -h|--help)
      sed -n '2,45p' "$0"; exit 0 ;;
    *)
      echo "error: unknown flag $1" >&2; exit 2 ;;
  esac
done

case "$ARCH" in
  x86_64|amd64)   ARCH="x86_64";  DOCKER_PLATFORM="linux/amd64" ;;
  aarch64|arm64) ARCH="aarch64"; DOCKER_PLATFORM="linux/arm64" ;;
  *) echo "error: unsupported --arch '$ARCH'" >&2; exit 2 ;;
esac

command -v docker >/dev/null || {
  echo "error: docker is required" >&2; exit 2;
}

# ── Cross-arch emulation preflight ────────────────────────────────────────────
# ``exec format error`` from inside containers means docker pulled an image
# for the requested arch but the host can't execute it (no qemu-user
# binfmt registration). Detect this up front and tell the user what to do
# instead of leaving them with a cryptic error.
HOST_ARCH="$(uname -m)"
case "$HOST_ARCH" in
  x86_64|amd64)   HOST_NORMALISED="x86_64" ;;
  aarch64|arm64)  HOST_NORMALISED="aarch64" ;;
  *)              HOST_NORMALISED="$HOST_ARCH" ;;
esac

if [ "$HOST_NORMALISED" != "$ARCH" ]; then
  echo "Cross-arch run: host=$HOST_NORMALISED, target=$ARCH"
  # The presence of a binfmt registration for the qemu interpreter of the
  # target arch is the canonical "we can run this" signal on Linux. Docker
  # Desktop on macOS bakes its own translation in, so we whitelist that
  # case too.
  binfmt_ok=0
  case "$ARCH" in
    aarch64) marker=/proc/sys/fs/binfmt_misc/qemu-aarch64 ;;
    x86_64)  marker=/proc/sys/fs/binfmt_misc/qemu-x86_64 ;;
  esac
  if [ -r "$marker" ] && grep -q '^enabled' "$marker"; then
    binfmt_ok=1
  fi
  # Docker Desktop (macOS, Windows WSL) handles cross-arch via its own
  # virtualization stack; ``docker info`` advertises the supported
  # platforms in those builds.
  if docker info --format '{{println .OSType .Architecture}}' 2>/dev/null \
       | grep -qiE "darwin|wsl"; then
    binfmt_ok=1
  fi
  if [ "$binfmt_ok" -ne 1 ]; then
    cat >&2 <<EOF
warning: no qemu binfmt registration found for $ARCH.
Cross-arch containers will fail with "exec format error".

Fix on Linux (one-shot, persists across reboots if the host uses
systemd-binfmt):
  docker run --privileged --rm tonistiigi/binfmt --install all

Or via the distro package:
  Debian/Ubuntu: sudo apt-get install qemu-user-static binfmt-support
  Fedora:        sudo dnf install qemu-user-static
  Arch:          sudo pacman -S qemu-user-static qemu-user-static-binfmt

Then re-run this script.
EOF
    echo ""
    echo "Proceeding anyway in case docker has another translation source…"
    echo ""
  fi
fi

# ── Locate artifacts ──────────────────────────────────────────────────────────
# Try the user-provided dir first, then the per-arch Nuitka build output, then
# the .build/ root (where the make_*.sh scripts deposit their final names).
DEB="" ; RPM="" ; APPIMAGE=""

find_first() {
  # find_first PATTERN [DIRS...]: print the first matching file, if any.
  local pattern="$1"; shift
  for dir in "$@"; do
    [ -d "$dir" ] || continue
    local hit
    hit="$(find "$dir" -maxdepth 2 -type f -name "$pattern" 2>/dev/null | head -n1)"
    if [ -n "$hit" ]; then printf '%s' "$hit"; return; fi
  done
}

SEARCH_DIRS=()
if [ -n "$ARTIFACTS_DIR" ]; then
  SEARCH_DIRS+=("$ARTIFACTS_DIR")
fi
SEARCH_DIRS+=(
  ".build/${ARCH}-unknown-linux-gnu/release"
  ".build/$( [ "$ARCH" = "x86_64" ] && echo "x86_64-unknown-linux-gnu" || echo "aarch64-unknown-linux-gnu" )/release"
  ".build"
)

# Match both the raw script outputs (``perpetua_1.5.0_amd64.deb``) and the
# CI-renamed artifacts (``Perpetua-Linux-x86_64.deb``).
DEB="$(find_first "*.deb" "${SEARCH_DIRS[@]}")"
RPM="$(find_first "*.rpm" "${SEARCH_DIRS[@]}")"
APPIMAGE="$(find_first "*.AppImage" "${SEARCH_DIRS[@]}")"

echo "Artifacts for $ARCH:"
echo "  .deb       = ${DEB:-(missing)}"
echo "  .rpm       = ${RPM:-(missing)}"
echo "  .AppImage  = ${APPIMAGE:-(missing)}"
echo ""

# ── Distro matrix ─────────────────────────────────────────────────────────────
# Kind selects which artifact to feed (deb / rpm / appimage).
# Inspection tools + Xvfb for the launch check.
APT_BASE_PREP='apt-get update -qq && apt-get install -y --no-install-recommends ca-certificates file libc-bin xvfb xauth'
DNF_BASE_PREP='dnf install -y file glibc xorg-x11-server-Xvfb xorg-x11-xauth'
PACMAN_BASE_PREP='pacman -Syu --noconfirm file glibc xorg-server-xvfb xorg-xauth'

# What the excludelist assumes every host provides. libGLESv2 is here though
# absent from that list: libglvnd dlopens it, so no ELF in the bundle names it.
APT_BASELINE_DESKTOP='apt-get install -y --no-install-recommends libgl1 libegl1 libgles2 libglx-mesa0 libfontconfig1 libfreetype6 libharfbuzz0b libwayland-client0 libx11-6 libxext6 libxrender1 zlib1g'
DNF_BASELINE_DESKTOP='dnf install -y mesa-libGL libglvnd-egl libglvnd-gles fontconfig freetype harfbuzz libwayland-client libX11 libXext libXrender zlib'
PACMAN_BASELINE_DESKTOP='pacman -S --noconfirm libglvnd fontconfig freetype2 harfbuzz wayland libx11 libxext libxrender zlib'

declare -A DISTRO_IMAGE  DISTRO_KIND  DISTRO_PREP  DISTRO_INSTALL
DISTRO_IMAGE[ubuntu]="ubuntu:24.04"
DISTRO_KIND[ubuntu]="deb"
DISTRO_PREP[ubuntu]="$APT_BASE_PREP"
DISTRO_INSTALL[ubuntu]='apt-get install -y --no-install-recommends /pkg.deb'

DISTRO_IMAGE[debian]="debian:trixie-slim"
DISTRO_KIND[debian]="deb"
DISTRO_PREP[debian]="$APT_BASE_PREP"
DISTRO_INSTALL[debian]='apt-get install -y --no-install-recommends /pkg.deb'

DISTRO_IMAGE[fedora]="fedora:latest"
DISTRO_KIND[fedora]="rpm"
DISTRO_PREP[fedora]="$DNF_BASE_PREP"
DISTRO_INSTALL[fedora]='dnf install -y /pkg.rpm'

DISTRO_IMAGE[opensuse]="opensuse/tumbleweed:latest"
DISTRO_KIND[opensuse]="rpm"
DISTRO_PREP[opensuse]='zypper -n install file glibc xorg-x11-server-Xvfb xauth'
# zypper refuses local rpms by default unless we point at the file
# explicitly with --no-gpg-checks.
DISTRO_INSTALL[opensuse]='zypper -n --no-gpg-checks install --allow-unsigned-rpm /pkg.rpm'

DISTRO_IMAGE[ubuntu-appimage]="ubuntu:24.04"
DISTRO_KIND[ubuntu-appimage]="appimage"
DISTRO_PREP[ubuntu-appimage]="$APT_BASE_PREP && $APT_BASELINE_DESKTOP"
DISTRO_INSTALL[ubuntu-appimage]=':'   # AppImage is self-contained

DISTRO_IMAGE[fedora-appimage]="fedora:latest"
DISTRO_KIND[fedora-appimage]="appimage"
DISTRO_PREP[fedora-appimage]="$DNF_BASE_PREP && $DNF_BASELINE_DESKTOP"
DISTRO_INSTALL[fedora-appimage]=':'

DISTRO_IMAGE[arch]="archlinux:latest"
DISTRO_KIND[arch]="appimage"
DISTRO_PREP[arch]="$PACMAN_BASE_PREP && $PACMAN_BASELINE_DESKTOP"
DISTRO_INSTALL[arch]=':'

# ── Verification script that runs inside each container ──────────────────────
# Stored once and mounted in.
TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

cat > "$TMPDIR/verify.sh" <<'INNER'
#!/bin/sh
set -eu
KIND="$1"
EXCL=/excludelist.txt

if [ "$KIND" = "appimage" ]; then
  chmod +x /pkg.AppImage
  cd /tmp
  # --appimage-extract works without FUSE.
  /pkg.AppImage --appimage-extract >/dev/null
  ROOT=/tmp/squashfs-root
  TARGET=/tmp/squashfs-root/AppRun
else
  ROOT=/opt/Perpetua
  TARGET=/opt/Perpetua/Perpetua
fi

fail=0

# ── 1. library resolution over every bundled ELF ──────────────────────────────
# Any Nuitka extension or WebKit helper can be the one that fails to load.
echo "  [1/4] resolving libraries…"
: > /tmp/unresolved.txt
find "$ROOT" -type f | while read -r f; do
  [ "$(head -c4 "$f" | od -An -tx1 | tr -d ' \n')" = "7f454c46" ] || continue
  for soname in $(ldd "$f" 2>/dev/null | awk '/not found/ {print $1}'); do
    grep -qx -- "$soname" "$EXCL" && continue
    echo "$f: $soname" >> /tmp/unresolved.txt
  done
done
if [ -s /tmp/unresolved.txt ]; then
  sort -u -o /tmp/unresolved.txt /tmp/unresolved.txt
  echo "  ::error:: unresolved libraries:"
  sed 's/^/    /' /tmp/unresolved.txt
  fail=1
else
  echo "        OK"
fi

# ── 2. dlopen'd libraries and helper binaries ─────────────────────────────────
# Invisible to ldd, and running the app can't catch them either. The list is
# written here rather than read from make_appimage.sh: a test must not source
# its own expectations.
if [ "$KIND" = "appimage" ]; then
  echo "  [2/4] dlopen'd libraries + helper binaries…"
  BUNDLE="$ROOT/usr/lib/Perpetua"
  manifest_fail=0
  for soname in libei.so.1 liboeffis.so.1 libayatana-appindicator3.so.1; do
    if [ ! -f "$BUNDLE/$soname" ]; then
      echo "  ::error:: $soname is dlopen'd at runtime but not bundled"
      manifest_fail=1
    elif ldd "$BUNDLE/$soname" 2>&1 | grep -q 'not found'; then
      echo "  ::error:: $soname is bundled but does not resolve"
      manifest_fail=1
    fi
  done
  for exe in dumpkeys; do
    if [ ! -x "$BUNDLE/$exe" ]; then
      echo "  ::error:: $exe is executed by pynput but not bundled"
      manifest_fail=1
    fi
  done
  # The AppImage has no postinst: without this the udev rules cannot be
  # installed at all.
  if [ ! -x "$BUNDLE/enable_uinput.sh" ]; then
    echo "  ::error:: enable_uinput.sh is needed at runtime but not bundled"
    manifest_fail=1
  fi
  [ "$manifest_fail" -eq 0 ] && echo "        OK" || fail=1
else
  echo "  [2/4] dlopen'd libraries + helper binaries… n/a (package manager owns them)"
fi

# ── 3. the udev rules install from the AppImage's own entry point ────────────
# A 2-line pkexec shim stands in for polkit; the container is already root.
# This also catches AppRun running the GUI instead of handling the flag.
if [ "$KIND" = "appimage" ]; then
  echo "  [3/4] AppRun --install-udev-rules…"
  printf '#!/bin/sh\nexec "$@"\n' > /usr/local/bin/pkexec
  chmod 755 /usr/local/bin/pkexec
  rm -f /etc/udev/rules.d/01-perpetua-keyboard.rules /etc/udev/rules.d/12-input.rules
  set +e
  timeout 60 "$ROOT/AppRun" --install-udev-rules > /tmp/udev.log 2>&1
  udev_rc=$?
  set -e
  if [ "$udev_rc" -ne 0 ] || [ ! -f /etc/udev/rules.d/01-perpetua-keyboard.rules ]; then
    echo "  ::error:: --install-udev-rules failed (exit $udev_rc)"
    sed 's/^/    /' /tmp/udev.log
    fail=1
  else
    echo "        OK"
  fi
else
  echo "  [3/4] udev rules… n/a (the postinst already ran them)"
fi

# ── 4. it has to actually start ───────────────────────────────────────────────
echo "  [4/4] launching…"
set +e
timeout --signal=TERM 30 xvfb-run -a "$TARGET" > /tmp/launch.log 2>&1
rc=$?
set -e
# 124 = still running at the timeout, i.e. healthy.
if [ "$rc" -ne 124 ]; then
  echo "  ::error:: exited with $rc"
  sed 's/^/    /' /tmp/launch.log
  fail=1
else
  # The last two are version-skew between bundled libraries and host modules:
  # both were real, both are fixed by GIO_MODULE_DIR / bundled gst plugins.
  for marker in "cannot open shared object file" "is not installed" \
                "Backend import failed" "g_variant_builder_init_static" \
                "appsink not found"; do
    if grep -qi "$marker" /tmp/launch.log; then
      echo "  ::error:: missing runtime dependency: $marker"
      sed 's/^/    /' /tmp/launch.log
      fail=1
    fi
  done
  [ "$fail" -eq 0 ] && echo "        OK"
fi

exit "$fail"
INNER
chmod +x "$TMPDIR/verify.sh"

# ── Run the matrix ────────────────────────────────────────────────────────────
declare -A RESULT=()
IFS=',' read -r -a DISTROS <<< "$DISTROS_RAW"

run_one() {
  local label="$1"
  local image="${DISTRO_IMAGE[$label]:-}"
  local kind="${DISTRO_KIND[$label]:-}"
  if [ -z "$image" ] || [ -z "$kind" ]; then
    echo ">> $label: unknown distro label"
    RESULT[$label]="ERROR(unknown)"
    return
  fi

  # Pick artifact
  local pkg=""
  case "$kind" in
    deb)      pkg="$DEB" ;;
    rpm)      pkg="$RPM" ;;
    appimage) pkg="$APPIMAGE" ;;
  esac
  if [ -z "$pkg" ] || [ ! -f "$pkg" ]; then
    echo ">> $label: no $kind artifact found; skipping"
    RESULT[$label]="SKIP(no $kind)"
    return
  fi

  local target="/pkg.$kind"
  local cname
  cname="perpetua-verify-${label}-$$"

  echo ""
  echo "================================================================"
  echo " $label  ($image, kind=$kind)"
  echo "    artifact: $pkg"
  echo "================================================================"

  local docker_args=(
    run --rm --platform "$DOCKER_PLATFORM"
    --name "$cname"
    -v "$(realpath "$pkg"):${target}:ro"
    -v "$TMPDIR/verify.sh:/verify.sh:ro"
    -v "$(realpath scripts/appimage-excludelist.txt):/excludelist.txt:ro"
    "$image"
    sh -c "set -e
      ${DISTRO_PREP[$label]}
      ${DISTRO_INSTALL[$label]}
      /verify.sh ${kind}
    "
  )
  [ "$KEEP" -eq 1 ] && docker_args=("${docker_args[@]/--rm/}")

  if docker "${docker_args[@]}"; then
    RESULT[$label]="PASS"
  else
    RESULT[$label]="FAIL"
  fi
}

for d in "${DISTROS[@]}"; do
  d="$(echo "$d" | tr -d ' ')"
  [ -n "$d" ] && run_one "$d"
done

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "================================================================"
echo " Summary (arch=$ARCH)"
echo "================================================================"
overall=0
for d in "${DISTROS[@]}"; do
  d="$(echo "$d" | tr -d ' ')"
  [ -z "$d" ] && continue
  printf "  %-18s %s\n" "$d" "${RESULT[$d]:-(not run)}"
  case "${RESULT[$d]:-}" in
    PASS|SKIP*) : ;;
    *) overall=1 ;;
  esac
done
exit "$overall"
