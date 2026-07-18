#!/usr/bin/env bash
#
# Run the same smoke-test the CI does, but locally via docker.
#
# Spins up each target distro in a container, installs the corresponding
# Perpetua artifact (.deb / .rpm / .AppImage), and verifies that ``ldd``
# resolves all shared libs of both binaries (the Tauri GUI ``Perpetua``
# and the Nuitka daemon ``_perpetua``). Pure read-only against the local
# filesystem; the only state mutated is short-lived docker containers.
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
#                     opensuse,arch}. Default: all five.
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
DISTROS_RAW="ubuntu,debian,fedora"
ARTIFACTS_DIR=""
KEEP=0

while [ $# -gt 0 ]; do
  case "$1" in
    --arch)       ARCH="$2"; shift 2 ;;
    --distros)    DISTROS_RAW="$2"; shift 2 ;;
    --artifacts)  ARTIFACTS_DIR="$2"; shift 2 ;;
    --keep)       KEEP=1; shift ;;
    -h|--help)
      sed -n '2,30p' "$0"; exit 0 ;;
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
# Each entry: ``<label>|<image>|<kind>|<install-cmd>|<prep-cmd>``
# Kind selects which artifact to feed (deb / rpm / appimage).
# Prep-cmd installs minimal inspection tools and (for the AppImage case)
# the runtime libs that ``apt`` / ``dnf`` would otherwise pull via Depends.
declare -A DISTRO_IMAGE  DISTRO_KIND  DISTRO_PREP  DISTRO_INSTALL
DISTRO_IMAGE[ubuntu]="ubuntu:24.04"
DISTRO_KIND[ubuntu]="deb"
DISTRO_PREP[ubuntu]='apt-get update -qq && apt-get install -y --no-install-recommends ca-certificates file libc-bin'
DISTRO_INSTALL[ubuntu]='apt-get install -y --no-install-recommends /pkg.deb'

DISTRO_IMAGE[debian]="debian:trixie-slim"
DISTRO_KIND[debian]="deb"
DISTRO_PREP[debian]='apt-get update -qq && apt-get install -y --no-install-recommends ca-certificates file libc-bin'
DISTRO_INSTALL[debian]='apt-get install -y --no-install-recommends /pkg.deb'

DISTRO_IMAGE[fedora]="fedora:latest"
DISTRO_KIND[fedora]="rpm"
DISTRO_PREP[fedora]='dnf install -y file glibc'
DISTRO_INSTALL[fedora]='dnf install -y /pkg.rpm'

DISTRO_IMAGE[opensuse]="opensuse/tumbleweed:latest"
DISTRO_KIND[opensuse]="rpm"
DISTRO_PREP[opensuse]='zypper -n install file glibc'
# zypper refuses local rpms by default unless we point at the file
# explicitly with --no-gpg-checks.
DISTRO_INSTALL[opensuse]='zypper -n --no-gpg-checks install --allow-unsigned-rpm /pkg.rpm'

DISTRO_IMAGE[arch]="archlinux:latest"
DISTRO_KIND[arch]="appimage"
DISTRO_PREP[arch]='pacman -Syu --noconfirm file fuse2 libei webkit2gtk-4.1 libsoup3 gtk3 cairo pango glib2 gdk-pixbuf2 libayatana-appindicator openssl'
DISTRO_INSTALL[arch]=':'   # AppImage is self-contained; no install step

# ── Verification script that runs inside each container ──────────────────────
# Stored once and mounted in; no escaping nightmares.
TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

cat > "$TMPDIR/verify.sh" <<'INNER'
#!/bin/sh
set -eu
KIND="$1"

if [ "$KIND" = "appimage" ]; then
  chmod +x /pkg.AppImage
  cd /tmp
  /pkg.AppImage --appimage-extract >/dev/null
  ROOT=/tmp/squashfs-root/usr/lib/Perpetua
else
  ROOT=/opt/Perpetua
fi

fail=0
for bin in "$ROOT/Perpetua" "$ROOT/_perpetua"; do
  if [ ! -f "$bin" ]; then
    echo "  MISSING binary: $bin"
    fail=1
    continue
  fi
  if ldd "$bin" 2>&1 | grep -E 'not found'; then
    echo "  ::error:: Unresolved libs in $bin"
    ldd "$bin" | sed 's/^/    /'
    fail=1
  fi
done
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
  printf "  %-10s %s\n" "$d" "${RESULT[$d]:-(not run)}"
  case "${RESULT[$d]:-}" in
    PASS|SKIP*) : ;;
    *) overall=1 ;;
  esac
done
exit "$overall"
