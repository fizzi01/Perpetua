#!/usr/bin/env bash
#
# Bundle the Nuitka standalone dist + the Tauri GUI into an AppImage.
#
# Everything not in scripts/appimage-excludelist.txt must be carried. Three
# kinds of dependency, only the first visible to ldd: DT_NEEDED (collected to
# a fixpoint), dlopen'd by soname (DLOPEN_SONAMES), external executables
# (EXTERNAL_BINARIES).
#
# Requires: patchelf, appimagetool (fetched into .build/ if absent), and the
# dist already built (``poetry run python build.py --skip-gui``).
#
set -euo pipefail
umask 022

APP_NAME="perpetua"
APP_DISPLAY_NAME="Perpetua"
BINARY_NAME="Perpetua"
HOMEPAGE="https://github.com/fizzi01/Perpetua"
DESCRIPTION_SHORT="Cross-platform mouse, keyboard, and clipboard sharing"

# Class 2: dlopen'd by soname, so no ELF names them. libei/liboeffis come
# from snegg's ctypes bindings, libayatana from libappindicator-sys.
DLOPEN_SONAMES=(libei.so.1 liboeffis.so.1 libayatana-appindicator3.so.1)
# Class 3: looked up on PATH at runtime.
EXTERNAL_BINARIES=(dumpkeys)
# Installed at runtime, not at install time: an AppImage has no postinst.
SETUP_SCRIPT="enable_uinput.sh"

VERSION="$(grep -m1 '^version\s*=\s*"' pyproject.toml | sed -E 's/^version\s*=\s*"([^"]+)"/\1/')"
# The window's identity on both X11 (WM_CLASS) and Wayland (app_id). The
# desktop entry must be named after it or neither the dock nor the launcher
# can match a running window to its icon. Read from the Tauri config so the
# two cannot drift apart.
APP_ID="$(grep -m1 '"identifier"' src-gui/src-tauri/tauri.conf.json |
          sed -E 's/.*"identifier"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/')"
ARCH_DEB="$(dpkg --print-architecture 2>/dev/null || true)"
case "${ARCH_DEB:-$(uname -m)}" in
  amd64|x86_64)   ARCH="x86_64"; RUST_TARGET="x86_64-unknown-linux-gnu" ;;
  arm64|aarch64)  ARCH="aarch64"; RUST_TARGET="aarch64-unknown-linux-gnu" ;;
  *)
    echo "error: unsupported architecture '${ARCH_DEB:-$(uname -m)}'" >&2
    exit 1
    ;;
esac

BUILD_DIR=".build/${RUST_TARGET}/release"
DIST_DIR="${BUILD_DIR}/${APP_DISPLAY_NAME}.dist"
APPDIR=".build/${APP_NAME}-${VERSION}-${ARCH}.AppDir"
APPIMAGE_OUT=".build/${APP_DISPLAY_NAME}-${VERSION}-${ARCH}.AppImage"
EXCLUDELIST="scripts/appimage-excludelist.txt"

ICON_SRC="src-gui/src-tauri/icons/icon.png"

# ── Preflight ─────────────────────────────────────────────────────────────────
[ -f "pyproject.toml" ] || { echo "error: run from project root" >&2; exit 1; }
[ -n "$VERSION" ] || { echo "error: could not parse version" >&2; exit 1; }
[ -n "$APP_ID" ] || { echo "error: could not parse the Tauri identifier" >&2; exit 1; }
[ -d "$DIST_DIR" ] || { echo "error: $DIST_DIR not found (build the daemon first)" >&2; exit 1; }
[ -f "$EXCLUDELIST" ] || { echo "error: $EXCLUDELIST not found" >&2; exit 1; }
command -v patchelf >/dev/null || { echo "error: patchelf not installed" >&2; exit 1; }
command -v ldd >/dev/null || { echo "error: ldd not available" >&2; exit 1; }

# Build-host property: the same script must produce both triplets.
MULTIARCH="$(ls -d /usr/lib/*-linux-gnu* 2>/dev/null | head -1 | xargs -r basename || true)"
: "${MULTIARCH:=$(uname -m)-linux-gnu}"

# Pinned to "continuous": there is no stable tag stream.
APPIMAGETOOL=".build/appimagetool-${ARCH}.AppImage"
if [ ! -x "$APPIMAGETOOL" ]; then
  echo "Fetching appimagetool…"
  mkdir -p .build
  curl -fL --retry 3 -o "$APPIMAGETOOL" \
    "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-${ARCH}.AppImage"
  chmod +x "$APPIMAGETOOL"
fi

# ── Clean slate ───────────────────────────────────────────────────────────────
rm -rf "$APPDIR" "$APPIMAGE_OUT"

mkdir -p \
  "$APPDIR/usr/bin" \
  "$APPDIR/usr/lib/${APP_DISPLAY_NAME}" \
  "$APPDIR/usr/share/applications" \
  "$APPDIR/usr/share/icons/hicolor/256x256/apps" \
  "$APPDIR/usr/share/metainfo"

BUNDLE="$APPDIR/usr/lib/${APP_DISPLAY_NAME}"

# ── Payload ───────────────────────────────────────────────────────────────────
cp -a "$DIST_DIR/." "$BUNDLE/"

find "$APPDIR" -type d -exec chmod 755 {} +
find "$APPDIR" -type f -exec chmod 644 {} +
find "$BUNDLE" -type f \( -name "$BINARY_NAME" -o -name "_perpetua" \) -exec chmod 755 {} +

# ── Privileged setup script ───────────────────────────────────────────────────
# No postinst in an AppImage: the rules are installed at runtime, so the
# script must sit next to the daemon, where the permission check looks.
[ -f "scripts/${SETUP_SCRIPT}" ] || {
  echo "error: scripts/${SETUP_SCRIPT} not found" >&2; exit 1; }
install -m 0755 "scripts/${SETUP_SCRIPT}" "$BUNDLE/${SETUP_SCRIPT}"

# GIO scans this directory instead of the host's: the host's gvfs modules are
# built against a newer glib than the one bundled here and fail to load.
mkdir -p "$BUNDLE/gio/modules"

# Matches the .deb layout, so an extracted AppDir drops into /opt unchanged.
ln -sf "../lib/${APP_DISPLAY_NAME}/${BINARY_NAME}" "$APPDIR/usr/bin/${APP_NAME}"

# ── Helpers ───────────────────────────────────────────────────────────────────
is_elf() {
  [ -f "$1" ] && [ "$(head -c4 "$1" 2>/dev/null | od -An -tx1 | tr -d ' \n')" = "7f454c46" ]
}
excluded() {
  grep -qx -- "$1" "$EXCLUDELIST" && return 0
  case "$1" in ld-linux*|linux-vdso*) return 0 ;; esac
  return 1
}
have() { find "$APPDIR" -name "$1" -print -quit | grep -q .; }
soname_path() { ldconfig -p | sed -n 's/.*=> //p' | grep -E "/$1\$" | head -1 || true; }

# ── WebKit helper processes ───────────────────────────────────────────────────
# Separate processes, not linked into libwebkit2gtk: the collector cannot see
# them. Search only existing dirs - /usr/lib64 is absent on arm64, and find's
# exit 1 would die silently through the command substitution.
WK_SEARCH_DIRS=()
for d in /usr/lib /usr/lib64 /usr/libexec; do
  if [ -d "$d" ]; then
    WK_SEARCH_DIRS+=("$d")
  fi
done
if [ "${#WK_SEARCH_DIRS[@]}" -eq 0 ]; then
  echo "error: none of /usr/lib /usr/lib64 /usr/libexec exist" >&2
  exit 1
fi
WK_SRC="$(find "${WK_SEARCH_DIRS[@]}" -maxdepth 3 -type d -name 'webkit2gtk-4.1' 2>/dev/null | head -1 || true)"
[ -n "$WK_SRC" ] || { echo "error: webkit2gtk-4.1 helper dir not found" >&2; exit 1; }
WK_DEST="$APPDIR/usr/lib/${MULTIARCH}/webkit2gtk-4.1"
mkdir -p "$WK_DEST/injected-bundle"
cp "$WK_SRC/WebKitNetworkProcess" "$WK_SRC/WebKitWebProcess" "$WK_DEST/"
cp "$WK_SRC/injected-bundle/libwebkit2gtkinjectedbundle.so" "$WK_DEST/injected-bundle/"
chmod 755 "$WK_DEST/WebKitNetworkProcess" "$WK_DEST/WebKitWebProcess"
echo "WebKit helpers staged from $WK_SRC"

# ── GStreamer plugins ─────────────────────────────────────────────────────────
# WebKit's media backend dlopen's these by directory, so ldd never sees them.
# Carrying the core library without its plugins is the worst of both worlds:
# the bundled core would scan the host's plugin directory, whose plugins are
# built against the host's GStreamer ("element appsink not found").
GST_SRC="$(find /usr/lib /usr/lib64 -maxdepth 2 -type d -name 'gstreamer-1.0' 2>/dev/null |
           grep -v '/include/' | head -1 || true)"
[ -n "$GST_SRC" ] || {
  echo "error: gstreamer-1.0 plugin dir not found on the build host" >&2; exit 1; }
mkdir -p "$BUNDLE/gstreamer-1.0"
cp "$GST_SRC"/*.so "$BUNDLE/gstreamer-1.0/" 2>/dev/null || true
gst_n="$(find "$BUNDLE/gstreamer-1.0" -name '*.so' | wc -l)"
[ "$gst_n" -gt 0 ] || { echo "error: no GStreamer plugins copied from $GST_SRC" >&2; exit 1; }
echo "gstreamer plugins: ${gst_n} <- $GST_SRC"

# ── Class 2: dlopen'd sonames ─────────────────────────────────────────────────
for soname in "${DLOPEN_SONAMES[@]}"; do
  src="$(soname_path "$soname")"
  [ -n "$src" ] || { echo "error: $soname not found on the build host" >&2; exit 1; }
  cp "$(readlink -f "$src")" "$BUNDLE/$soname"
  echo "dlopen dep: $soname <- $src"
done

# ── Class 3: external executables ─────────────────────────────────────────────
for exe in "${EXTERNAL_BINARIES[@]}"; do
  src="$(command -v "$exe" || true)"
  [ -n "$src" ] || { echo "error: $exe not found on the build host (install kbd)" >&2; exit 1; }
  cp "$(readlink -f "$src")" "$BUNDLE/$exe"
  chmod 755 "$BUNDLE/$exe"
  echo "external binary: $exe <- $src"
done

# ── Class 1: transitive closure, to a fixpoint ────────────────────────────────
# A collected library brings its own deps, so rescan until a pass adds nothing.
echo "Collecting link-time dependencies…"
for iter in $(seq 1 10); do
  find "$APPDIR" -type f > .build/.appimage-elflist
  while read -r elf; do
    is_elf "$elf" || continue
    ldd "$elf" 2>/dev/null | awk '$2=="=>" && $3 ~ /^\// {print $1" "$3}' |
      while read -r soname path; do
        excluded "$soname" && continue
        have "$soname" && continue
        cp "$(readlink -f "$path")" "$BUNDLE/$soname" 2>/dev/null && echo "ADDED $soname"
        : # the loop's status must not depend on the last cp
      done
  done < .build/.appimage-elflist | { grep '^ADDED' || true; } | sort -u > .build/.appimage-added
  added="$(wc -l < .build/.appimage-added)"
  echo "  pass ${iter}: +${added}"
  if [ "$added" -eq 0 ]; then
    break
  fi
done
rm -f .build/.appimage-elflist .build/.appimage-added

# ── rpath ─────────────────────────────────────────────────────────────────────
# --force-rpath writes DT_RPATH, not DT_RUNPATH: only RPATH is inherited by
# transitive deps, without which the collected libraries cannot find each other.
patchelf --force-rpath --set-rpath '$ORIGIN' "$BUNDLE/$BINARY_NAME"
rpath_n=0
for so in "$BUNDLE"/*.so*; do
  [ -f "$so" ] || continue
  patchelf --force-rpath --set-rpath '$ORIGIN' "$so" 2>/dev/null && rpath_n=$((rpath_n + 1))
done
for plugin in "$BUNDLE"/gstreamer-1.0/*.so; do
  [ -f "$plugin" ] || continue
  patchelf --force-rpath --set-rpath '$ORIGIN/..' "$plugin" 2>/dev/null || true
done
for helper in "$WK_DEST/WebKitNetworkProcess" "$WK_DEST/WebKitWebProcess"; do
  patchelf --force-rpath --set-rpath "\$ORIGIN/../../${APP_DISPLAY_NAME}" "$helper"
done
patchelf --force-rpath --set-rpath "\$ORIGIN/../../../${APP_DISPLAY_NAME}" \
  "$WK_DEST/injected-bundle/libwebkit2gtkinjectedbundle.so"
echo "rpath set on ${rpath_n} bundled libraries"

# ── WebKit's compile-time absolute paths ──────────────────────────────────────
# WebKit hardcodes its helper path and ignores WEBKIT_EXEC_PATH. "/usr" ->
# "././" is the same length (no offsets shift) and relative to the CWD AppRun sets.
for so in "$BUNDLE"/libwebkit2gtk-4.1.so* "$BUNDLE"/libjavascriptcoregtk-4.1.so*; do
  [ -f "$so" ] || continue
  before="$(stat -c%s "$so")"
  sed -i -e 's|/usr|././|g' "$so"
  after="$(stat -c%s "$so")"
  [ "$before" = "$after" ] || { echo "error: $(basename "$so") changed size" >&2; exit 1; }
done

# ── Icon ──────────────────────────────────────────────────────────────────────
if [ ! -f "$ICON_SRC" ]; then
  echo "error: icon not found at $ICON_SRC" >&2
  exit 1
fi
cp "$ICON_SRC" "$APPDIR/usr/share/icons/hicolor/256x256/apps/${APP_NAME}.png"
# appimagetool requires the icon at the AppDir root, alongside the .desktop.
cp "$ICON_SRC" "$APPDIR/${APP_NAME}.png"
# Diagnostic symlink consumed by some launchers.
ln -sf "${APP_NAME}.png" "$APPDIR/.DirIcon"

# ── .desktop (one copy at root, one under usr/share) ─────────────────────────
cat > "$APPDIR/${APP_ID}.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=${APP_DISPLAY_NAME}
GenericName=Input Sharing
Comment=${DESCRIPTION_SHORT}
Exec=${APP_NAME}
Icon=${APP_NAME}
Terminal=false
StartupNotify=true
StartupWMClass=${APP_ID}
Categories=Network;Utility;
Keywords=mouse;keyboard;clipboard;sharing;kvm;
EOF
cp "$APPDIR/${APP_ID}.desktop" "$APPDIR/usr/share/applications/${APP_ID}.desktop"

# ── AppStream metainfo ────────────────────────────────────────────────────────
cat > "$APPDIR/usr/share/metainfo/${APP_NAME}.metainfo.xml" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<component type="desktop-application">
  <id>${APP_ID}</id>
  <name>${APP_DISPLAY_NAME}</name>
  <summary>${DESCRIPTION_SHORT}</summary>
  <metadata_license>MIT</metadata_license>
  <project_license>GPL-3.0-or-later</project_license>
  <description><p>${DESCRIPTION_SHORT}.</p></description>
  <launchable type="desktop-id">${APP_ID}.desktop</launchable>
  <url type="homepage">${HOMEPAGE}</url>
  <provides><binary>${BINARY_NAME}</binary></provides>
  <releases>
    <release version="${VERSION}" date="$(date +%Y-%m-%d)"/>
  </releases>
  <content_rating type="oars-1.1"/>
</component>
EOF

# ── AppRun ────────────────────────────────────────────────────────────────────
# APP_ID is interpolated; everything below is literal.
{
  echo '#!/usr/bin/env bash'
  echo "APP_ID=\"${APP_ID}\""
  cat <<'EOF'
HERE="$(dirname "$(readlink -f "${0}")")"
export APPDIR="${HERE}"
BUNDLE="${HERE}/usr/lib/Perpetua"

# pynput finds the bundled dumpkeys here; without it input/keyboard won't import.
export PATH="${BUNDLE}:${PATH}"

# Our own module directory, empty on purpose: GIO would otherwise load the
# host's gvfs modules, which are built against a newer glib than the bundled
# one and abort with an undefined symbol.
export GIO_MODULE_DIR="${BUNDLE}/gio/modules"

# Same reason: our GStreamer core must load our plugins, never the host's.
export GST_PLUGIN_SYSTEM_PATH_1_0="${BUNDLE}/gstreamer-1.0"
export GST_PLUGIN_PATH_1_0="${BUNDLE}/gstreamer-1.0"

# No postinst in an AppImage: the udev rules are installed on demand. The
# script elevates itself and copies itself out of this FUSE mount.
if [ "${1:-}" = "--install-udev-rules" ]; then
  exec "${BUNDLE}/enable_uinput.sh" configure
fi

# Desktop integration. Without an entry on the host there is nothing for the
# launcher and the dock to take the name and icon from; the entry is named
# after APP_ID because that is what both X11 (WM_CLASS) and Wayland (app_id)
# match a running window against.
data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
desktop_entry="${data_home}/applications/${APP_ID}.desktop"
installed_icon="${data_home}/icons/hicolor/256x256/apps/${APP_ID}.png"

remove_desktop_integration() {
  rm -f "$desktop_entry" "$installed_icon"
  command -v update-desktop-database >/dev/null 2>&1 &&
    update-desktop-database "${data_home}/applications" >/dev/null 2>&1
  echo "perpetua: desktop integration removed."
}

install_desktop_integration() {
  # $APPIMAGE is the only path that outlives the process; an extracted AppDir
  # has none, so there is nothing stable to point an entry at.
  [ -n "${APPIMAGE:-}" ] || return 0
  mkdir -p "$(dirname "$desktop_entry")" "$(dirname "$installed_icon")" || return 0
  cp -f "${HERE}/usr/share/icons/hicolor/256x256/apps/perpetua.png" \
        "$installed_icon" 2>/dev/null || return 0

  # Absolute Icon= on purpose: it skips icon-theme lookup, which needs a cache
  # refresh the desktop may not have done yet.
  new_entry="$(cat <<DESKTOP
[Desktop Entry]
Type=Application
Name=Perpetua
GenericName=Input Sharing
Comment=Cross-platform mouse, keyboard, and clipboard sharing
Exec="${APPIMAGE}"
Icon=${installed_icon}
Terminal=false
StartupNotify=true
StartupWMClass=${APP_ID}
Categories=Network;Utility;
Keywords=mouse;keyboard;clipboard;sharing;kvm;
DESKTOP
)"
  # Rewrite only on a change, so a moved or updated AppImage is picked up
  # without touching the file on every single launch.
  if [ "$(cat "$desktop_entry" 2>/dev/null)" != "$new_entry" ]; then
    printf '%s\n' "$new_entry" > "$desktop_entry" || return 0
    command -v update-desktop-database >/dev/null 2>&1 &&
      update-desktop-database "${data_home}/applications" >/dev/null 2>&1
  fi
}

case "${1:-}" in
  --remove-desktop-integration) remove_desktop_integration; exit 0 ;;
  --no-desktop-integration)     shift ;;
  *) [ -n "${PERPETUA_NO_DESKTOP_INTEGRATION:-}" ] || install_desktop_integration ;;
esac

# WebKit's helper path was rewritten to "././...", relative to the CWD.
cd "${HERE}/usr" || exit 1

# exec -a sets argv[0], which is where GTK takes g_get_prgname() from, and
# with it the Wayland app_id and the X11 WM_CLASS. Without it the window
# advertises a name no desktop entry is called, and the dock shows the
# generic icon.
# No LD_LIBRARY_PATH: DT_RPATH handles it, and child processes stay clean.
exec -a "${APP_ID}" "${BUNDLE}/Perpetua" "$@"
EOF
} > "$APPDIR/AppRun"
chmod 755 "$APPDIR/AppRun"

# ── Build ─────────────────────────────────────────────────────────────────────
echo "Building ${APPIMAGE_OUT}…"
# appimagetool is itself an AppImage; --appimage-extract-and-run avoids FUSE.
ARCH="$ARCH" "$APPIMAGETOOL" --appimage-extract-and-run "$APPDIR" "$APPIMAGE_OUT"

echo ""
echo "  AppImage ready: $APPIMAGE_OUT"
echo "  Bundled libs:   $(find "$BUNDLE" -name '*.so*' | wc -l)"
echo "  Size:           $(du -sh "$APPIMAGE_OUT" | cut -f1)"
echo "  Run with:       chmod +x $APPIMAGE_OUT && ./$APPIMAGE_OUT"
echo "  First run:      keyboard input needs udev rules. Either click"
echo "                  \"Install permissions\" in the app, or run once:"
echo "                    ./$(basename "$APPIMAGE_OUT") --install-udev-rules"
