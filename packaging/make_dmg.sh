#!/usr/bin/env bash
# packaging/make_dmg.sh — wrap the macOS .app bundle in a .dmg (the native installer
# for macOS). Runs after the PyInstaller build, which on macOS produces a real .app
# (dist/OwnVoiceReader.app) via BUNDLE. Produces dist/OwnVoiceReader-<arch>.dmg.
#
# The .dmg holds the .app + a symlink to /Applications for drag-to-install UX — the
# user drags ONE app in, not a folder. hdiutil is always present on a macOS runner
# (no create-dmg, no extra tooling). -format UDZO = a compressed read-only image, the
# smallest native form.
#
# Usage:  bash packaging/make_dmg.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="$ROOT/dist/OwnVoiceReader.app"
OUTDIR="$ROOT/dist"
STAGE="$ROOT/build/dmg"
NAME="OwnVoiceReader"
# Arch-aware: macos-latest is arm64 today; an Intel runner reports x86_64.
ARCH="${1:-${OV_ARCH:-$(uname -m)}}"
DST="$OUTDIR/${NAME}-${ARCH}.dmg"

if [ ! -d "$APP" ]; then
  echo "error: $APP not found — run the PyInstaller build first:" >&2
  echo "       pyinstaller packaging/ownvoicereader.spec   (on macOS this makes a .app)" >&2
  exit 1
fi

rm -rf "$STAGE" "$DST"
mkdir -p "$STAGE"
# Copy the .app in, then add a /Applications symlink for drag-to-install.
cp -a "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"

# -format UDZO = a compressed (read-only) disk image — the smallest native form.
# -ov overwrites a stale .dmg. hdiutil is the macOS-native tool; no dependencies.
hdiutil create -volname "$NAME" -srcfolder "$STAGE" -ov -format UDZO "$DST" >/dev/null
echo "done: $DST ($(du -h "$DST" | cut -f1))"
