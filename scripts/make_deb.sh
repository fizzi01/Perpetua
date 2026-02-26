#!/bin/bash
set -e

APP_NAME="Perpetua"
BINARY_NAME="Perpetua"
VERSION=$(grep -m1 '^version\s*=\s*"' pyproject.toml | sed -E 's/^version\s*=\s*"([^"]+)"/\1/')
ARCH="amd64"
BUILD_DIR=".build/x86_64-unknown-linux-gnu/release"
DEB_DIR=".build/perpetua_${VERSION}_${ARCH}"
INSTALL_PREFIX="/opt/${APP_NAME}"
DESKTOP_FILE="${APP_NAME}.desktop"

rm -rf "$DEB_DIR" "${DEB_DIR}.deb"

mkdir -p "$DEB_DIR/DEBIAN"
mkdir -p "$DEB_DIR${INSTALL_PREFIX}"
mkdir -p "$DEB_DIR/usr/share/applications"


DIST_DIR="$BUILD_DIR/${APP_NAME}.dist"
cp -a "$DIST_DIR/." "$DEB_DIR${INSTALL_PREFIX}/"
cp "scripts/enable_uinput.sh" "$DEB_DIR/DEBIAN/postinst"
chmod +x "$DEB_DIR/DEBIAN/postinst"

cat > "$DEB_DIR/DEBIAN/control" <<EOF
Package: Perpetua
Version: $VERSION
Section: utils
Priority: optional
Architecture: $ARCH
Maintainer: fizzi01
Description: Cross-platform Mouse, keyboard, and clipboard sharing
EOF

cat > "$DEB_DIR/usr/share/applications/$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=Perpetua
Exec=${INSTALL_PREFIX}/$BINARY_NAME
Icon=${INSTALL_PREFIX}/icon.png
Terminal=false
NoDisplay=false
SingleMainWindow=true
Categories=Utility;
EOF

if [ -f "src-gui/src-tauri/icons/icon.png" ]; then
  mkdir -p "$DEB_DIR${INSTALL_PREFIX}"
  cp "src-gui/src-tauri/icons/icon.png" "$DEB_DIR${INSTALL_PREFIX}/icon.png"
fi

fakeroot dpkg-deb --build "$DEB_DIR"

echo "Deb created at: .build/perpetua_${VERSION}_${ARCH}.deb"