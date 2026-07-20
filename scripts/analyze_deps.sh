#!/usr/bin/env bash
#
# Walk a Nuitka standalone bundle (or any directory containing ELF binaries
# and shared objects) and report every *external* shared library it pulls
# in at runtime — i.e. everything ``ldd`` resolves outside the bundle dir
# itself.
#
# Usage:
#   scripts/analyze_deps.sh [PATH] [--map] [--quiet]
#
#   PATH     directory to scan (default: the Perpetua.dist matching the
#            host arch under .build/<rust-target>/release/Perpetua.dist).
#   --map    additionally print the system package providing each SONAME
#            (uses ``dpkg -S`` on Debian/Ubuntu, ``rpm -qf`` on Fedora,
#            ``pacman -Qo`` on Arch). Skipped silently when none works.
#   --quiet  drop the per-binary breakdown; only print the final unique
#            sorted SONAME list.
#
# Exit status: 0 if no unresolved (``not found``) entries are seen, 1 if
# any binary has at least one missing lib — useful in CI to fail the build
# before packaging.
#
set -euo pipefail

# ── Args ──────────────────────────────────────────────────────────────────────
SCAN_DIR=""
DO_MAP=0
QUIET=0
for arg in "$@"; do
  case "$arg" in
    --map)   DO_MAP=1 ;;
    --quiet) QUIET=1 ;;
    -*)
      echo "error: unknown flag $arg" >&2
      exit 2
      ;;
    *)
      SCAN_DIR="$arg"
      ;;
  esac
done

# Default to the per-arch Nuitka output. Keeps the script usable from a fresh
# checkout without arguments.
if [ -z "$SCAN_DIR" ]; then
  case "$(uname -m)" in
    x86_64|amd64)  TARGET="x86_64-unknown-linux-gnu" ;;
    aarch64|arm64) TARGET="aarch64-unknown-linux-gnu" ;;
    *)
      echo "error: unsupported architecture '$(uname -m)' - pass PATH explicitly" >&2
      exit 2
      ;;
  esac
  SCAN_DIR=".build/${TARGET}/release/Perpetua.dist"
fi

[ -d "$SCAN_DIR" ] || { echo "error: $SCAN_DIR is not a directory" >&2; exit 2; }
command -v ldd  >/dev/null || { echo "error: ldd not found" >&2; exit 2; }
command -v file >/dev/null || { echo "error: file not found" >&2; exit 2; }

# Resolve to absolute path so the "internal" check below works regardless of
# what the user passed.
SCAN_DIR="$(cd "$SCAN_DIR" && pwd)"

# ── Distro detection for --map ────────────────────────────────────────────────
PKG_LOOKUP=""
if [ "$DO_MAP" -eq 1 ]; then
  if command -v dpkg >/dev/null 2>&1; then
    PKG_LOOKUP="dpkg"
  elif command -v rpm >/dev/null 2>&1; then
    PKG_LOOKUP="rpm"
  elif command -v pacman >/dev/null 2>&1; then
    PKG_LOOKUP="pacman"
  else
    echo "warning: --map requested but no supported package manager (dpkg/rpm/pacman); skipping" >&2
    DO_MAP=0
  fi
fi

lookup_package() {
  # Find the package owning the file at the resolved path of an SONAME.
  # ldd may report a path that traverses a symlink farm (most notoriously
  # the merged-/usr layout on modern Debian/Ubuntu: ``/lib`` is a symlink
  # to ``/usr/lib`` and dpkg knows only the canonical entry), so we
  # consult the distro tool against both the raw and the canonical path
  # and return the first hit.
  local resolved="$1"
  [ -e "$resolved" ] || { echo "?"; return; }
  local canonical
  canonical="$(readlink -f -- "$resolved" 2>/dev/null || true)"

  local out=""
  case "$PKG_LOOKUP" in
    dpkg)
      out="$(dpkg -S -- "$resolved"  2>/dev/null | awk -F': ' '{print $1; exit}')"
      [ -z "$out" ] && [ -n "$canonical" ] && \
        out="$(dpkg -S -- "$canonical" 2>/dev/null | awk -F': ' '{print $1; exit}')"
      ;;
    rpm)
      out="$(rpm -qf --queryformat '%{NAME}\n' -- "$resolved"  2>/dev/null | head -n1)"
      [ -z "$out" ] && [ -n "$canonical" ] && \
        out="$(rpm -qf --queryformat '%{NAME}\n' -- "$canonical" 2>/dev/null | head -n1)"
      ;;
    pacman)
      out="$(pacman -Qo -- "$resolved"  2>/dev/null | awk '{print $5"-"$6}' | head -n1)"
      [ -z "$out" ] && [ -n "$canonical" ] && \
        out="$(pacman -Qo -- "$canonical" 2>/dev/null | awk '{print $5"-"$6}' | head -n1)"
      ;;
  esac
  printf '%s' "$out"
}

# ── Scan ──────────────────────────────────────────────────────────────────────
# Two associative arrays:
#   externals[soname]     = absolute path the linker would load
#   missing[soname]       = 1 if any binary reports it "not found"
declare -A externals=()
declare -A missing=()
fail=0

log() { [ "$QUIET" -eq 1 ] || printf '%s\n' "$*"; }

# ``find -print0`` + ``read -d ''`` to survive paths with spaces.
while IFS= read -r -d '' bin; do
  # Only consider dynamically-linked ELF objects. ``file`` is more reliable
  # than checking extensions (Nuitka emits suffixless executables and
  # ``.so.N`` files alike).
  if ! file -L -- "$bin" | grep -q 'ELF.*dynamically linked'; then
    continue
  fi
  log ""
  log "## $bin"

  # ldd may exit non-zero on statically-linked binaries or weird cases; we
  # still want its stderr for the "not found" markers, so capture both.
  ldd_out="$(ldd -- "$bin" 2>&1 || true)"
  while IFS= read -r line; do
    # Lines look like one of:
    #   libfoo.so.1 => /lib/x86_64-linux-gnu/libfoo.so.1 (0x...)
    #   libfoo.so.1 => not found
    #   /lib64/ld-linux-x86-64.so.2 (0x...)
    #   linux-vdso.so.1 (0x...)
    case "$line" in
      *"=>"*)
        soname="$(echo "$line" | awk '{print $1}')"
        target="$(echo "$line" | awk -F' => ' '{print $2}' | awk '{print $1}')"
        if [ "$target" = "not" ]; then
          # "=> not found"
          log "    MISSING  $soname"
          missing["$soname"]=1
          fail=1
          continue
        fi
        # Skip libs the bundle resolves to itself ($ORIGIN-rpath case).
        case "$target" in
          "$SCAN_DIR"/*)
            log "    bundled  $soname  ($target)"
            continue
            ;;
        esac
        externals["$soname"]="$target"
        log "    external $soname  -> $target"
        ;;
      *linux-vdso*|*"ld-linux"*|*"ld-musl"*)
        # Loader and VDSO are always present; not a real dependency.
        ;;
    esac
  done <<EOF
$ldd_out
EOF
done < <(find "$SCAN_DIR" -type f \( -name '*.so' -o -name '*.so.*' -o -perm -u+x \) -print0)

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "=== External shared libraries (unique) ==="
if [ "${#externals[@]}" -eq 0 ] && [ "${#missing[@]}" -eq 0 ]; then
  echo "(none)"
else
  if [ "$DO_MAP" -eq 1 ]; then
    # Print as: ``SONAME  ->  PATH  [package]``
    for s in "${!externals[@]}"; do
      printf '%s\t%s\t[%s]\n' "$s" "${externals[$s]}" "$(lookup_package "${externals[$s]}")"
    done | sort
  else
    printf '%s\n' "${!externals[@]}" | sort -u
  fi
fi

if [ "${#missing[@]}" -gt 0 ]; then
  echo ""
  echo "=== UNRESOLVED (these will fail at runtime) ==="
  printf '%s\n' "${!missing[@]}" | sort -u
fi

exit "$fail"
