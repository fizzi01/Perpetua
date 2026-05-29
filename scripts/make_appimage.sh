#!/usr/bin/env bash
#
# Bundle the Nuitka standalone dist into an AppImage.
#
# The AppImage is a portable cousin of the .deb produced by ``make_deb.sh``:
# same Perpetua.dist payload, same install dir layout under ``usr/``, but
# packaged as a single relocatable .AppImage file instead of a system
# package. The bundle does NOT carry the udev rules — first-launch tells the
# user to run ``scripts/enable_uinput.sh`` once with sudo.
#
# Requires:
#   - appimagetool (downloaded automatically into .build/ if missing)
#   - the Nuitka dist already built (run ``poetry run python build.py
#     --skip-gui`` first)
#
set -euo pipefail
umask 022

APP_NAME="perpetua"
APP_DISPLAY_NAME="Perpetua"
BINARY_NAME="Perpetua"
HOMEPAGE="https://github.com/fizzi01/Perpetua"
DESCRIPTION_SHORT="Cross-platform mouse, keyboard, and clipboard sharing"

VERSION="$(grep -m1 '^version\s*=\s*"' pyproject.toml | sed -E 's/^version\s*=\s*"([^"]+)"/\1/')"
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

ICON_SRC="src-gui/src-tauri/icons/icon.png"

# ── Preflight ─────────────────────────────────────────────────────────────────
[ -f "pyproject.toml" ] || { echo "error: run from project root" >&2; exit 1; }
[ -n "$VERSION" ] || { echo "error: could not parse version" >&2; exit 1; }
[ -d "$DIST_DIR" ] || { echo "error: $DIST_DIR not found (build the daemon first)" >&2; exit 1; }

# Acquire appimagetool. We pin to the continuous release because there's no
# stable tag stream; the binary is statically linked so the architecture
# parameter only affects squashfs metadata, not portability of the produced
# AppImage payload.
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

# ── Payload ───────────────────────────────────────────────────────────────────
# Perpetua.dist is already self-contained (Nuitka --standalone). We stage it
# under usr/lib/Perpetua/ and expose the binary via a thin AppRun wrapper.
cp -a "$DIST_DIR/." "$APPDIR/usr/lib/${APP_DISPLAY_NAME}/"

find "$APPDIR" -type d -exec chmod 755 {} +
find "$APPDIR" -type f -exec chmod 644 {} +
find "$APPDIR/usr/lib/${APP_DISPLAY_NAME}" \
  -type f \( -name "$BINARY_NAME" -o -name "_perpetua" \) \
  -exec chmod 755 {} +

# Provide a /usr/bin entry so the AppImage works the same as the .deb when
# extracted via ``--appimage-extract`` and dropped into ``/opt``.
ln -sf "../lib/${APP_DISPLAY_NAME}/${BINARY_NAME}" "$APPDIR/usr/bin/${APP_NAME}"

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
cat > "$APPDIR/${APP_NAME}.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=${APP_DISPLAY_NAME}
GenericName=Input Sharing
Comment=${DESCRIPTION_SHORT}
Exec=${APP_NAME}
Icon=${APP_NAME}
Terminal=false
StartupNotify=true
StartupWMClass=${APP_DISPLAY_NAME}
Categories=Network;Utility;
Keywords=mouse;keyboard;clipboard;sharing;kvm;
EOF
cp "$APPDIR/${APP_NAME}.desktop" "$APPDIR/usr/share/applications/${APP_NAME}.desktop"

# ── AppStream metainfo ────────────────────────────────────────────────────────
cat > "$APPDIR/usr/share/metainfo/${APP_NAME}.metainfo.xml" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<component type="desktop-application">
  <id>${APP_NAME}</id>
  <name>${APP_DISPLAY_NAME}</name>
  <summary>${DESCRIPTION_SHORT}</summary>
  <metadata_license>MIT</metadata_license>
  <project_license>MIT</project_license>
  <description><p>${DESCRIPTION_SHORT}.</p></description>
  <launchable type="desktop-id">${APP_NAME}.desktop</launchable>
  <url type="homepage">${HOMEPAGE}</url>
  <provides><binary>${BINARY_NAME}</binary></provides>
  <releases>
    <release version="${VERSION}" date="$(date +%Y-%m-%d)"/>
  </releases>
  <content_rating type="oars-1.1"/>
</component>
EOF

# ── AppRun ────────────────────────────────────────────────────────────────────
# Nuitka --standalone resolves bundled .so via ``$ORIGIN`` rpath, so the
# binary works as-is from any path. We still ``cd`` to the bundle dir before
# exec so any relative resource lookup keeps working.
cat > "$APPDIR/AppRun" <<'EOF'
#!/usr/bin/env bash
HERE="$(dirname "$(readlink -f "${0}")")"
BUNDLE="${HERE}/usr/lib/Perpetua"
export PATH="${BUNDLE}:${PATH}"
# Don't override LD_LIBRARY_PATH: the Nuitka rpath already handles it and
# leaking $ORIGIN-derived paths breaks child processes (e.g. xdg-open).
exec "${BUNDLE}/Perpetua" "$@"
EOF
chmod 755 "$APPDIR/AppRun"

# ── Build ─────────────────────────────────────────────────────────────────────
echo "Building ${APPIMAGE_OUT}…"
# appimagetool is itself an AppImage and tries to self-mount via FUSE, 
# ``--appimage-extract-and-run`` makes it unpack to a temp dir
# and run the AppRun directly.
# ARCH env var is honoured by appimagetool to set runtime image arch.
ARCH="$ARCH" "$APPIMAGETOOL" --appimage-extract-and-run --no-appstream "$APPDIR" "$APPIMAGE_OUT"

echo ""
echo "  AppImage ready: $APPIMAGE_OUT"
echo "  Size:           $(du -sh "$APPIMAGE_OUT" | cut -f1)"
echo "  Run with:       chmod +x $APPIMAGE_OUT && ./$APPIMAGE_OUT"
echo "  First run note: keyboard input needs udev rules; run"
echo "                  scripts/enable_uinput.sh once with sudo."
