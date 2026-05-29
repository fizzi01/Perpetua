#!/usr/bin/env bash
#
# Build an RPM for Fedora / RHEL / openSUSE from the Nuitka standalone dist.
#
# Mirrors ``make_deb.sh`` (same install layout under /opt/Perpetua, same
# desktop entry, AppStream metadata, and udev-rules postinstall). Generates
# the SPEC inline so the version stays single-sourced in pyproject.toml.
#
# Requires:
#   - rpmbuild + rpm-build (apt: ``rpm``; dnf: ``rpm-build``)
#   - the Nuitka dist already built (run ``poetry run python build.py
#     --skip-gui`` first)
#
set -euo pipefail
umask 022

APP_NAME="perpetua"
APP_DISPLAY_NAME="Perpetua"
BINARY_NAME="Perpetua"
MAINTAINER="fizzi01"
HOMEPAGE="https://github.com/fizzi01/Perpetua"
DESCRIPTION_SHORT="Cross-platform mouse, keyboard, and clipboard sharing"
DESCRIPTION_LONG="Perpetua is an open-source, cross-platform KVM software that lets
you share a single keyboard and mouse across multiple devices.

Inspired by Apple's Universal Control, it provides seamless cursor
movement between devices, keyboard sharing, and automatic clipboard
synchronization. All secured with TLS encryption."

VERSION="$(grep -m1 '^version\s*=\s*"' pyproject.toml | sed -E 's/^version\s*=\s*"([^"]+)"/\1/')"
case "$(uname -m)" in
  x86_64|amd64)   RPM_ARCH="x86_64"; RUST_TARGET="x86_64-unknown-linux-gnu" ;;
  aarch64|arm64)  RPM_ARCH="aarch64"; RUST_TARGET="aarch64-unknown-linux-gnu" ;;
  *)
    echo "error: unsupported architecture '$(uname -m)'" >&2
    exit 1
    ;;
esac

BUILD_DIR=".build/${RUST_TARGET}/release"
DIST_DIR="${BUILD_DIR}/${APP_DISPLAY_NAME}.dist"
INSTALL_PREFIX="/opt/${APP_DISPLAY_NAME}"
ICON_SRC="src-gui/src-tauri/icons/icon.png"

# ── Preflight ─────────────────────────────────────────────────────────────────
[ -f "pyproject.toml" ] || { echo "error: run from project root" >&2; exit 1; }
[ -n "$VERSION" ] || { echo "error: could not parse version" >&2; exit 1; }
[ -d "$DIST_DIR" ] || { echo "error: $DIST_DIR not found (build the daemon first)" >&2; exit 1; }
command -v rpmbuild >/dev/null 2>&1 || {
  echo "error: rpmbuild is not installed (apt: rpm; dnf: rpm-build)" >&2
  exit 1
}

echo "Building ${APP_NAME} ${VERSION} (${RPM_ARCH}) RPM…"

# ── Stage the payload tarball ─────────────────────────────────────────────────
# rpmbuild expects a source tarball under SOURCES/ that the spec unpacks via
# %setup. We use a dedicated topdir under .build/ to keep the operation
# hermetic and avoid clobbering the user's ~/rpmbuild.
TOPDIR="$(pwd)/.build/rpmbuild"
STAGE="$(pwd)/.build/${APP_NAME}-${VERSION}"

rm -rf "$TOPDIR" "$STAGE"
mkdir -p "$STAGE" "$TOPDIR"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}

# Lay out the source tree exactly like the .deb buildroot: ``opt/`` for the
# bundle, ``usr/`` for symlinks + integration files. The spec just copies
# this verbatim into %{buildroot}.
mkdir -p \
  "$STAGE${INSTALL_PREFIX}" \
  "$STAGE/usr/bin" \
  "$STAGE/usr/share/applications" \
  "$STAGE/usr/share/pixmaps" \
  "$STAGE/usr/share/icons/hicolor/256x256/apps" \
  "$STAGE/usr/share/metainfo" \
  "$STAGE/usr/lib/systemd/user"

cp -a "$DIST_DIR/." "$STAGE${INSTALL_PREFIX}/"
find "$STAGE" -type d -exec chmod 755 {} +
find "$STAGE" -type f -exec chmod 644 {} +
find "$STAGE${INSTALL_PREFIX}" \
  -type f \( -name "$BINARY_NAME" -o -name "_perpetua" \) \
  -exec chmod 755 {} +

ln -sf "${INSTALL_PREFIX}/${BINARY_NAME}" "$STAGE/usr/bin/${APP_NAME}"

if [ -f "$ICON_SRC" ]; then
  cp "$ICON_SRC" "$STAGE/usr/share/icons/hicolor/256x256/apps/${APP_NAME}.png"
  cp "$ICON_SRC" "$STAGE/usr/share/pixmaps/${APP_NAME}.png"
fi

cat > "$STAGE/usr/share/applications/${APP_NAME}.desktop" <<EOF
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

cat > "$STAGE/usr/share/metainfo/${APP_NAME}.metainfo.xml" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<component type="desktop-application">
  <id>${APP_NAME}</id>
  <name>${APP_DISPLAY_NAME}</name>
  <summary>${DESCRIPTION_SHORT}</summary>
  <metadata_license>MIT</metadata_license>
  <project_license>GPL-3.0-or-later</project_license>
  <description><p>${DESCRIPTION_SHORT}.</p></description>
  <launchable type="desktop-id">${APP_NAME}.desktop</launchable>
  <icon type="stock">${APP_NAME}</icon>
  <url type="homepage">${HOMEPAGE}</url>
  <provides><binary>${BINARY_NAME}</binary></provides>
  <releases>
    <release version="${VERSION}" date="$(date +%Y-%m-%d)"/>
  </releases>
  <content_rating type="oars-1.1"/>
</component>
EOF

if [ -f "scripts/systemd/perpetua-daemon.service" ]; then
  install -m 0644 "scripts/systemd/perpetua-daemon.service" \
    "$STAGE/usr/lib/systemd/user/perpetua-daemon.service"
fi

(cd "$(dirname "$STAGE")" && tar czf "$TOPDIR/SOURCES/${APP_NAME}-${VERSION}.tar.gz" "${APP_NAME}-${VERSION}")

# ── postinst (re-uses the shared udev-setup script) ──────────────────────────
POSTINST_BODY=""
if [ -f "scripts/enable_uinput.sh" ]; then
  # Strip the shebang so rpm's scriptlet interpreter takes over (it's
  # already /bin/sh-compatible).
  POSTINST_BODY="$(sed -e '1{/^#!/d;}' scripts/enable_uinput.sh)"
fi

# ── SPEC ──────────────────────────────────────────────────────────────────────
# AutoReqProv is disabled because Nuitka's bundled .so files would otherwise
# leak into automatic ``Provides:`` lines and conflict with system packages.
SPEC="$TOPDIR/SPECS/${APP_NAME}.spec"
{
  cat <<EOF
%global debug_package %{nil}
%define _build_id_links none

Name:           ${APP_NAME}
Version:        ${VERSION}
Release:        1%{?dist}
Summary:        ${DESCRIPTION_SHORT}

License:        GPL-3.0-or-later
URL:            ${HOMEPAGE}
Source0:        %{name}-%{version}.tar.gz
BuildArch:      ${RPM_ARCH}

AutoReqProv:    no
Requires:       libei
Requires:       webkit2gtk4.1
Requires:       gtk3
Requires:       libsoup3
Requires:       cairo
Requires:       glib2
Requires:       gdk-pixbuf2
Requires:       libayatana-appindicator-gtk3
Recommends:     xclip
Recommends:     wl-clipboard
Recommends:     liboeffis

%description
${DESCRIPTION_LONG}

%prep
%setup -q

%build
# Pre-built binary; nothing to compile.

%install
rm -rf %{buildroot}
cp -a . %{buildroot}/

%post
EOF

  if [ -n "$POSTINST_BODY" ]; then
    printf '%s\n' "$POSTINST_BODY"
  else
    echo "# (no postinst body found)"
  fi

  cat <<EOF

%postun
# Best-effort cleanup of udev rules we installed; ignore failures so an
# uninstall on a half-broken system doesn't error out the transaction.
if [ "\$1" = 0 ]; then
  rm -f /etc/udev/rules.d/01-perpetua-keyboard.rules || true
  rm -f /etc/udev/rules.d/12-input.rules || true
  udevadm control --reload-rules >/dev/null 2>&1 || true
fi

%files
${INSTALL_PREFIX}
/usr/bin/${APP_NAME}
/usr/share/applications/${APP_NAME}.desktop
/usr/share/icons/hicolor/256x256/apps/${APP_NAME}.png
/usr/share/pixmaps/${APP_NAME}.png
/usr/share/metainfo/${APP_NAME}.metainfo.xml
EOF

  if [ -f "scripts/systemd/perpetua-daemon.service" ]; then
    echo "/usr/lib/systemd/user/perpetua-daemon.service"
  fi

  cat <<EOF

%changelog
* $(LC_ALL=C date '+%a %b %d %Y') ${MAINTAINER} - ${VERSION}-1
- See CHANGELOG.md for upstream details.
EOF
} > "$SPEC"

# ── Build ─────────────────────────────────────────────────────────────────────
rpmbuild \
  --define "_topdir $TOPDIR" \
  --define "_binary_payload w9.xzdio" \
  -bb "$SPEC"

OUT_RPM="$(find "$TOPDIR/RPMS" -type f -name '*.rpm' | head -n1)"
DEST=".build/${APP_NAME}-${VERSION}-1.${RPM_ARCH}.rpm"
cp "$OUT_RPM" "$DEST"

echo ""
echo "  Package ready: $DEST"
echo "  Size:          $(du -sh "$DEST" | cut -f1)"
echo "  Inspect with:  rpm -qpi $DEST"
