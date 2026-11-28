#!/usr/bin/env bash
# packaging/make_deb.sh — wrap the Linux one-dir bundle in a native .deb, the
# one-click installer for Ubuntu/Debian: double-click -> install (Ubuntu Software /
# GDebi), then launch from the app menu. Self-contained: it bundles the whole app
# (core + vision + ocr + the windowed host + the bundled voice + the Piper TTS), so
# it needs only a base C + GL/X11 system — present on any graphical Ubuntu/Debian.
#
# Produces dist/ownvoicereader_<ver>_<arch>.deb.
#
# Usage:  bash packaging/make_deb.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ONEDIR="$ROOT/dist/OwnVoiceReader"
OUTDIR="$ROOT/dist"
DEBDIR="$ROOT/build/deb"
APP="ownvoicereader"        # .deb package name (lowercase, the dpkg convention)
VERSION="0.1.0"
# dpkg arch name (amd64 / arm64); differs from the AppImage's x86_64 spelling.
ARCH="$(dpkg --print-architecture)"
DST="$OUTDIR/${APP}_${VERSION}_${ARCH}.deb"
ICON_SRC="$ROOT/ownvoicereader/viewer/imgs/ownvoicereader-icon-256.png"

if [ ! -x "$ONEDIR/OwnVoiceReader" ]; then
  echo "error: $ONEDIR/OwnVoiceReader not found — run the PyInstaller build first:" >&2
  echo "       pyinstaller packaging/ownvoicereader.spec" >&2
  exit 1
fi

rm -rf "$DEBDIR"
mkdir -p \
  "$DEBDIR/DEBIAN" \
  "$DEBDIR/usr/lib/$APP" \
  "$DEBDIR/usr/bin" \
  "$DEBDIR/usr/share/applications" \
  "$DEBDIR/usr/share/icons/hicolor/256x256/apps"

# 1) The whole onedir -> /usr/lib/ownvoicereader (the exe stays beside _internal/).
cp -a "$ONEDIR/." "$DEBDIR/usr/lib/$APP/"

# 2) A launcher on PATH (so it's runnable from a terminal and from the menu).
cat > "$DEBDIR/usr/bin/$APP" <<'EOF'
#!/bin/sh
exec /usr/lib/ownvoicereader/OwnVoiceReader "$@"
EOF
chmod +x "$DEBDIR/usr/bin/$APP"

# 3) The menu entry: one-click launch from the app grid, a PDF association, the icon.
cat > "$DEBDIR/usr/share/applications/$APP.desktop" <<'DESK'
[Desktop Entry]
Type=Application
Name=OwnVoiceReader
GenericName=Local paper reader
Comment=Read a paper aloud with a local voice; your model, your voice, your machine.
Exec=ownvoicereader %F
Icon=ownvoicereader
Terminal=false
Categories=Office;Viewer;Utility;
MimeType=application/pdf;
DESK

# 4) The icon (so the menu / launcher shows the app's icon, not a generic one).
if [ -f "$ICON_SRC" ]; then
  cp "$ICON_SRC" "$DEBDIR/usr/share/icons/hicolor/256x256/apps/ownvoicereader.png"
fi

# 5) control — package metadata + a minimal, version-stable Depends. The app is fully
#    self-contained (Qt/Chromium/onnxruntime/piper are all bundled), so it needs only
#    the base C + GL/X11 system any graphical Ubuntu/Debian ships. A minimal Depends
#    keeps the .deb installable across Ubuntu versions without version-mismatch errors
#    (a richer list would fight per-release package renames like libasound2 -> t64).
cat > "$DEBDIR/DEBIAN/control" <<EOF
Package: $APP
Version: $VERSION
Section: utils
Priority: optional
Architecture: $ARCH
Maintainer: Paulo Viviurka do Carmo <paulorvdc@users.noreply.github.com>
Homepage: https://github.com/paulorvdc/OwnVoiceReader
Depends: libc6, libgl1, libx11-6
Description: OwnVoiceReader - read a paper aloud with a local voice
 Your model, your voice, your machine. A cross-platform desktop app that opens a
 paper in its own native window and reads it aloud with a fully local TTS voice
 (offline, no cloud). Ships core + vision + ocr + a bundled voice.
EOF

# --root-owner-group (dpkg 1.22.7+) silences a benign "root-owned files" warning; on
# older dpkg it is unknown, so fall back to a plain build (the cp is as the current
# user, so there's no root-ownership issue to begin with).
if dpkg-deb --root-owner-group --build "$DEBDIR" "$DST" 2>/dev/null; then
  :
else
  dpkg-deb --build "$DEBDIR" "$DST"
fi
echo "done: $DST ($(du -h "$DST" | cut -f1))"
