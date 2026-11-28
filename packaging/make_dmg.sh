#!/usr/bin/env bash
# packaging/make_dmg.sh — wrap the macOS one-dir bundle in a .dmg (the native
# installer for macOS). Runs after the PyInstaller onedir build
# (dist/OwnVoiceReader/). Produces dist/OwnVoiceReader-<arch>.dmg.
#
# Uses only hdiutil (always present on a macOS runner) — no create-dmg, no extra
# tooling. The onedir is a folder (PyInstaller --onedir), not a .app bundle, so the
# DMG holds the app folder + a symlink to /Applications for drag-to-install UX. A
# proper .app (PyInstaller --windowed/BUNDLE, with an icon + code signing) is a
# future refinement; this ships a working, double-clickable macOS artifact now.
#
# Usage:  bash packaging/make_dmg.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ONEDIR="$ROOT/dist/OwnVoiceReader"
OUTDIR="$ROOT/dist"
STAGE="$ROOT/build/dmg"
APP="OwnVoiceReader"
# Arch-aware: macos-latest is arm64 today; an Intel runner reports x86_64.
ARCH="${1:-${OV_ARCH:-$(uname -m)}}"
DST="$OUTDIR/${APP}-${ARCH}.dmg"

if [ ! -d "$ONEDIR" ]; then
  echo "error: $ONEDIR not found — run the PyInstaller build first:" >&2
  echo "       pyinstaller packaging/ownvoicereader.spec" >&2
  exit 1
fi

rm -rf "$STAGE" "$DST"
mkdir -p "$STAGE"
# Copy the onedir in, then add a symlink to /Applications so the user can drag the
# app folder onto it to install.
cp -a "$ONEDIR" "$STAGE/$APP"
ln -s /Applications "$STAGE/Applications"

# -format UDZO = a compressed (read-only) disk image — the smallest native form.
# -ov overwrites a stale .dmg. hdiutil is the macOS-native tool; no dependencies.
hdiutil create -volname "$APP" -srcfolder "$STAGE" -ov -format UDZO "$DST"
echo "done: $DST ($(du -h "$DST" | cut -f1))"
