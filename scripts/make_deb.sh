#!/bin/bash
# Script per creare un package .deb per Perpetua
set -e

APP_NAME="Perpetua"
BINARY_NAME="Perpetua"
VERSION="1.0.0"
ARCH="amd64"
BUILD_DIR=".build/x86_64-unknown-linux-gnu/release"
DEB_DIR=".build/perpetua_${VERSION}_${ARCH}"
INSTALL_PREFIX="/opt/${APP_NAME}"
DESKTOP_FILE="${APP_NAME}.desktop"

# Pulizia
rm -rf "$DEB_DIR" "${DEB_DIR}.deb"

# Struttura cartelle
mkdir -p "$DEB_DIR/DEBIAN"
mkdir -p "$DEB_DIR${INSTALL_PREFIX}"
mkdir -p "$DEB_DIR/usr/share/applications"

# Copia binario
cp "$BUILD_DIR/$APP_NAME" "$DEB_DIR${INSTALL_PREFIX}/$BINARY_NAME"
chmod 755 "$DEB_DIR${INSTALL_PREFIX}/$BINARY_NAME"

# Crea file control
cat > "$DEB_DIR/DEBIAN/control" <<EOF
Package: Perpetua
Version: $VERSION
Section: utils
Priority: optional
Architecture: $ARCH
Maintainer: Your Name <your@email.com>
Description: Perpetua utility
EOF

# Crea desktop entry
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

# (Opzionale) Copia icona se esiste
if [ -f "src-gui/src-tauri/icons/icon.png" ]; then
  mkdir -p "$DEB_DIR${INSTALL_PREFIX}"
  cp "src-gui/src-tauri/icons/icon.png" "$DEB_DIR${INSTALL_PREFIX}/icon.png"
fi

# Crea il pacchetto deb
fakeroot dpkg-deb --build "$DEB_DIR"

# Rinomina
mv "$DEB_DIR.deb" ".build/perpetua_${VERSION}_${ARCH}.deb"
echo "Deb created at: .build/perpetua_${VERSION}_${ARCH}.deb"