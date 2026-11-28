#!/usr/bin/env bash
# packaging/make_appimage.sh — wrap the one-dir bundle in a Linux AppImage.
#
# Runs after the PyInstaller onedir build (dist/OwnVoiceReader/). Produces
# dist/OwnVoiceReader-x86_64.AppImage. Self-fetches appimagetool + the runtime
# (cached under build/appimage/) when they're not already on PATH. If appimagetool
# can't be obtained (no network, CI locked down), it falls back to a portable
# tarball — the onedir is itself runnable, so nothing is lost.
#
# A PyInstaller onedir is NOT an AppDir: appimagetool needs a directory with an
# AppRun launcher, a .desktop file and an icon. So we assemble a proper AppDir
# (build/AppDir/) that keeps the onedir folder *intact* (the exe must stay beside
# its _internal/ sibling) and adds the three AppDir pieces around it.
#
# Usage:  bash packaging/make_appimage.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ONEDIR="$ROOT/dist/OwnVoiceReader"
OUTDIR="$ROOT/dist"
CACHE="$ROOT/build/appimage"
APPDIR="$ROOT/build/AppDir"
APP="OwnVoiceReader"
# Arch-aware: default to the build host's arch (x86_64 on ubuntu-latest, aarch64 on
# an arm runner). Override with OV_ARCH or pass it as $1 to build for a target.
ARCH="${1:-${OV_ARCH:-$(uname -m)}}"
EXE="$APP"   # PyInstaller 6.x onedir: the launcher exe is named after the COLLECT

if [ ! -x "$ONEDIR/$EXE" ]; then
  echo "error: $ONEDIR/$EXE not found — run the PyInstaller build first:" >&2
  echo "       pyinstaller packaging/ownvoicereader.spec" >&2
  exit 1
fi

# --- locate appimagetool (PATH -> cache -> download the continuous release) ----
appimagetool_bin=""
if command -v appimagetool >/dev/null 2>&1; then
  appimagetool_bin="$(command -v appimagetool)"
elif [ -x "$CACHE/squashfs-root/AppRun" ]; then
  appimagetool_bin="$CACHE/squashfs-root/AppRun"
elif [ -x "$CACHE/appimagetool.AppImage" ]; then
  appimagetool_bin="$CACHE/appimagetool.AppImage"
else
  mkdir -p "$CACHE"
  echo "appimagetool not found; downloading the continuous release to $CACHE ..."
  URL="https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-${ARCH}.AppImage"
  if curl -fL --retry 3 -o "$CACHE/appimagetool.AppImage" "$URL" 2>/dev/null; then
    chmod +x "$CACHE/appimagetool.AppImage"
    # The tool is itself an AppImage; --appimage-extract makes it runnable in place.
    "$CACHE/appimagetool.AppImage" --appimage-extract >/dev/null 2>&1 || true
    [ -x "$CACHE/squashfs-root/AppRun" ] && appimagetool_bin="$CACHE/squashfs-root/AppRun"
  fi
fi

# --- assemble a proper AppDir (onedir kept intact + AppRun + .desktop + icon) ----
build_appdir() {
  rm -rf "$APPDIR"
  mkdir -p "$APPDIR/usr/share/applications" "$APPDIR/usr/share/icons/hicolor"
  # Keep the onedir folder intact; the exe finds its _internal/ sibling.
  cp -a "$ONEDIR" "$APPDIR/$APP"

  # AppRun: launch the onedir's exe, forwarding args (e.g. a dropped PDF path).
  cat > "$APPDIR/AppRun" <<'RUN'
#!/bin/bash
DIR="$(dirname "$(readlink -f "$0")")"
exec "$DIR/OwnVoiceReader/OwnVoiceReader" "$@"
RUN
  chmod +x "$APPDIR/AppRun"

  # .desktop entry (installed under usr/share/applications so the AppImage advertises
  # a desktop icon + MIME association for PDFs).
  cat > "$APPDIR/usr/share/applications/${APP}.desktop" <<'DESK'
[Desktop Entry]
Type=Application
Name=OwnVoiceReader
GenericName=Local paper reader
Comment=Read a paper aloud with a local voice; your model, your voice, your machine.
Exec=OwnVoiceReader %F
Icon=OwnVoiceReader
Terminal=false
Categories=Office;Viewer;Utility;
MimeType=application/pdf;
DESK

  # The icon (appimagetool wants an `icon` at the AppDir root + a copy for the
  # desktop entry). Use the 256px app icon if present; appimagetool accepts PNG.
  ICON_SRC="$ROOT/ownvoicereader/viewer/imgs/ownvoicereader-icon-256.png"
  if [ -f "$ICON_SRC" ]; then
    cp "$ICON_SRC" "$APPDIR/icon"
    cp "$ICON_SRC" "$APPDIR/usr/share/icons/hicolor/256x256/apps/OwnVoiceReader.png"
  fi
}

# --- build the AppImage, or fall back to a tarball ----------------------------
if [ -n "$appimagetool_bin" ]; then
  echo "building AppImage with $appimagetool_bin ..."
  build_appdir
  "$appimagetool_bin" --appimage-compat "$APPDIR" "$OUTDIR/${APP}-${ARCH}.AppImage"
  chmod +x "$OUTDIR/${APP}-${ARCH}.AppImage"
  echo "done: $OUTDIR/${APP}-${ARCH}.AppImage"
else
  echo "appimagetool unavailable; falling back to a portable tarball ..."
  tar -C "$OUTDIR" -czf "${APP}-${ARCH}.tar.gz" "$APP"
  echo "done: $OUTDIR/${APP}-${ARCH}.tar.gz  (run: tar xzf ... && ./${APP}/${EXE})"
fi
