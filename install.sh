#!/bin/sh
# Copy the app into ~/Applications so it can be launched without the git checkout.
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/Applications/QC45 Menu"

if [ "$1" = "--uninstall" ]; then
  rm -rf "$DEST"
  echo "removed $DEST"
  exit 0
fi

rm -rf "$DEST"
mkdir -p "$DEST/menubar"
cp -R "$ROOT/pybmap" "$DEST/pybmap"
cp "$ROOT/menubar/qc45menu.py" "$ROOT/menubar/qc45_settings.py" "$ROOT/menubar/i18n.py" "$DEST/menubar/"
cp "$ROOT/requirements.txt" "$ROOT/LICENSE" "$DEST/"
cp "$ROOT/Launch QC45 Menu.command" "$DEST/"
chmod +x "$DEST/Launch QC45 Menu.command"
echo "installed: $DEST"
echo "start it by double-clicking: $DEST/Launch QC45 Menu.command"
