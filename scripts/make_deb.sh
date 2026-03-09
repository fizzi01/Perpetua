#!/usr/bin/env bash
set -euo pipefail
umask 022

# ── Identity ──────────────────────────────────────────────────────────────────
APP_NAME="perpetua"
APP_DISPLAY_NAME="Perpetua"
BINARY_NAME="Perpetua"
MAINTAINER="fizzi01"
HOMEPAGE="https://github.com/fizzi01/Perpetua"
DESCRIPTION_SHORT="Cross-platform mouse, keyboard, and clipboard sharing"
DESCRIPTION_LONG=" Perpetua is an open-source, cross-platform KVM software that lets
 you share a single keyboard and mouse across multiple devices.
 .
 Inspired by Apple's Universal Control, it provides seamless cursor
 movement between devices, keyboard sharing, and automatic clipboard
 synchronization. All secured with TLS encryption."

# ── Build metadata ────────────────────────────────────────────────────────────
VERSION="$(grep -m1 '^version\s*=\s*"' pyproject.toml | sed -E 's/^version\s*=\s*"([^"]+)"/\1/')"
ARCH="$(dpkg --print-architecture)"
BUILD_DIR=".build/x86_64-unknown-linux-gnu/release"
DIST_DIR="$BUILD_DIR/${APP_DISPLAY_NAME}.dist"
DEB_ROOT=".build/${APP_NAME}_${VERSION}_${ARCH}"
DEB_OUT="${DEB_ROOT}.deb"
CHANGELOG_SRC="CHANGELOG.md"

# ── Install paths ─────────────────────────────────────────────────────────────
INSTALL_PREFIX="/opt/${APP_DISPLAY_NAME}"
ICON_SRC="src-gui/src-tauri/icons/icon.png"

# ── Preflight checks ──────────────────────────────────────────────────────────
if [ ! -f "pyproject.toml" ]; then
  echo "error: pyproject.toml not found — run this script from the project root." >&2
  exit 1
fi

if [ -z "$VERSION" ]; then
  echo "error: could not parse version from pyproject.toml." >&2
  exit 1
fi

for cmd in fakeroot dpkg-deb; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "error: required tool '$cmd' is not installed." >&2
    exit 1
  fi
done

if [ ! -d "$DIST_DIR" ]; then
  echo "error: dist directory not found: $DIST_DIR" >&2
  exit 1
fi

echo "Building ${APP_NAME} ${VERSION} (${ARCH})…"

# ── Clean slate ───────────────────────────────────────────────────────────────
rm -rf "$DEB_ROOT" "$DEB_OUT"

# ── Directory tree ────────────────────────────────────────────────────────────
mkdir -p \
  "$DEB_ROOT/DEBIAN" \
  "$DEB_ROOT${INSTALL_PREFIX}" \
  "$DEB_ROOT/usr/bin" \
  "$DEB_ROOT/usr/share/applications" \
  "$DEB_ROOT/usr/share/pixmaps" \
  "$DEB_ROOT/usr/share/icons/hicolor/256x256/apps" \
  "$DEB_ROOT/usr/share/doc/${APP_NAME}" \
  "$DEB_ROOT/usr/share/metainfo" \
  "$DEB_ROOT/usr/share/lintian/overrides"

# ── Application binary & data ─────────────────────────────────────────────────
cp -a "$DIST_DIR/." "$DEB_ROOT${INSTALL_PREFIX}/"

find "$DEB_ROOT" -type d -exec chmod 755 {} +
find "$DEB_ROOT" -type f -exec chmod 644 {} +
find "$DEB_ROOT${INSTALL_PREFIX}" -type f \( -name "$BINARY_NAME" -o -name "_perpetua" \) \
  -exec chmod 755 {} +

# ── Symlink in PATH ─────────────────────────────────────────────────────────
ln -sf "${INSTALL_PREFIX}/${BINARY_NAME}" "$DEB_ROOT/usr/bin/${APP_NAME}"

# ── Maintainer scripts ────────────────────────────────────────────────────────
install -m 0775 "scripts/enable_uinput.sh" "$DEB_ROOT/DEBIAN/postinst"

# ── Icona ─────────────────────────────────────────────────────────────────────
if [ -f "$ICON_SRC" ]; then
  if command -v identify >/dev/null 2>&1; then
    ICON_SIZE="$(identify -format '%wx%h' "$ICON_SRC" 2>/dev/null || echo '512x512')"
  else
    ICON_SIZE="512x512"   # fallback
  fi
  mkdir -p "$DEB_ROOT/usr/share/icons/hicolor/${ICON_SIZE}/apps"
  cp "$ICON_SRC" "$DEB_ROOT/usr/share/icons/hicolor/${ICON_SIZE}/apps/${APP_NAME}.png"
  cp "$ICON_SRC" "$DEB_ROOT/usr/share/pixmaps/${APP_NAME}.png"
else
  echo "warning: icon not found at $ICON_SRC" >&2
fi

# ── Changelog Debian ──────────────────
printf "%s (%s) unstable; urgency=low\n\n  * See upstream CHANGELOG.md for details.\n\n -- %s  %s\n" \
  "$APP_NAME" "$VERSION" "$MAINTAINER" "$(date -R)" \
  | gzip -9 -n -c > "$DEB_ROOT/usr/share/doc/${APP_NAME}/changelog.Debian.gz"

if [ -f "$CHANGELOG_SRC" ]; then
  gzip -9 -n -c "$CHANGELOG_SRC" > "$DEB_ROOT/usr/share/doc/${APP_NAME}/changelog.gz"
else
  echo "warning: $CHANGELOG_SRC not found — skipping upstream changelog." >&2
fi

# ── AppStream metainfo ────────────────────────────────────────────────────────
parse_releases() {
  local changelog="$1"
  local current_version="" current_date="" in_section=0 body=""

  while IFS= read -r line; do
    if echo "$line" | grep -qE '^## '; then
      if [ -n "$current_version" ]; then
        body="$(echo "$body" | sed 's/&/\&amp;/g; s/</\&lt;/g; s/>/\&gt;/g')"
        printf '    <release version="%s" date="%s">\n      <description><p>%s</p></description>\n    </release>\n' \
          "$current_version" "$current_date" \
          "$(echo "$body" | tr '\n' ' ' | sed 's/  */ /g; s/^ //; s/ $//')"
      fi
      current_version="$(echo "$line" | sed -E 's/^## \[?([0-9]+\.[0-9]+[^] ]*)\]?.*/\1/')"
      current_date="$(echo "$line" | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}' || date +%Y-%m-%d)"
      in_section=1
      body=""
    elif [ "$in_section" -eq 1 ] && [ -n "$line" ]; then
      clean="$(echo "$line" | sed -E 's/^#+\s*//; s/^\*+\s*//; s/^-\s*//')"
      [ -n "$clean" ] && body="${body} ${clean}"
    fi
  done < "$changelog"

  if [ -n "$current_version" ]; then
    body="$(echo "$body" | sed 's/&/\&amp;/g; s/</\&lt;/g; s/>/\&gt;/g')"
    printf '    <release version="%s" date="%s">\n      <description><p>%s</p></description>\n    </release>\n' \
      "$current_version" "$current_date" \
      "$(echo "$body" | tr '\n' ' ' | sed 's/  */ /g; s/^ //; s/ $//')"
  fi
}

if [ -f "$CHANGELOG_SRC" ]; then
  RELEASES="$(parse_releases "$CHANGELOG_SRC")"
else
  RELEASES="    <release version=\"${VERSION}\" date=\"$(date +%Y-%m-%d)\"/>"
fi

cat > "$DEB_ROOT/usr/share/metainfo/${APP_NAME}.metainfo.xml" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<component type="desktop-application">
  <id>${APP_NAME}</id>
  <name>${APP_DISPLAY_NAME}</name>
  <summary>${DESCRIPTION_SHORT}</summary>
  <metadata_license>MIT</metadata_license>
  <project_license>MIT</project_license>
  <description>
    <p>${DESCRIPTION_SHORT}.</p>
  </description>
  <launchable type="desktop-id">${APP_NAME}.desktop</launchable>
  <icon type="stock">${APP_NAME}</icon>
  <url type="homepage">${HOMEPAGE}</url>
  <provides>
    <binary>${BINARY_NAME}</binary>
  </provides>
  <releases>
${RELEASES}
  </releases>
  <content_rating type="oars-1.1"/>
</component>
EOF

# ── Lintian overrides ─────────────────────────────────────────────────────────
cat > "$DEB_ROOT/usr/share/lintian/overrides/${APP_NAME}" <<EOF
# /opt è corretto per applicazioni standalone con runtime bundled
perpetua: dir-or-file-in-opt

# Le librerie sono parte del bundle, non dipendenze di sistema separabili
perpetua: embedded-library

# I binari del bundle non hanno simboli di debug, è intenzionale
perpetua: unstripped-binary-or-object

# Le .so del bundle non hanno SONAME né prerequisiti standard
perpetua: shared-library-lacks-prerequisites

# Le odd-permissions sulle .so sono già corrette da find/chmod sopra;
# questo override copre eventuali .so residue nel bundle
perpetua: odd-permissions-on-shared-library
EOF

# ── control ───────────────────────────────────────────────────────────────────
INSTALLED_SIZE="$(du -sk "$DEB_ROOT${INSTALL_PREFIX}" | cut -f1)"

cat > "$DEB_ROOT/DEBIAN/control" <<EOF
Package: ${APP_NAME}
Version: ${VERSION}
Section: net
Priority: optional
Architecture: ${ARCH}
Installed-Size: ${INSTALLED_SIZE}
Maintainer: ${MAINTAINER}
Homepage: ${HOMEPAGE}
Depends: libc6
Recommends: liboeffis1
Description: ${DESCRIPTION_SHORT}
${DESCRIPTION_LONG}
EOF

# ── .desktop ──────────────────────────────────────────────────────────────────
cat > "$DEB_ROOT/usr/share/applications/${APP_NAME}.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=${APP_DISPLAY_NAME}
GenericName=Input Sharing
Comment=${DESCRIPTION_SHORT}
Exec=${INSTALL_PREFIX}/${BINARY_NAME}
Icon=${APP_NAME}
Terminal=false
NoDisplay=false
StartupNotify=true
StartupWMClass=${APP_DISPLAY_NAME}
Categories=Network;Utility;
Keywords=mouse;keyboard;clipboard;sharing;kvm;
EOF

# ── md5sums ───────────────────────────────────────────────────────────────────
(cd "$DEB_ROOT" && find . -type f ! -path './DEBIAN/*' -exec md5sum {} + | sed 's|\./||') \
  > "$DEB_ROOT/DEBIAN/md5sums"

# ── Build ─────────────────────────────────────────────────────────────────────
fakeroot dpkg-deb --build --root-owner-group "$DEB_ROOT"

echo ""
echo "  Package ready: $DEB_OUT"
echo "  Size:        $(du -sh "$DEB_OUT" | cut -f1)"
echo "  Inspect with: dpkg-deb -I $DEB_OUT"
echo "  Lint    with: lintian --tag-display-limit 0 $DEB_OUT"