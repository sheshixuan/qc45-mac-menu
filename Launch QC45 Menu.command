#!/bin/sh
# Opens Terminal and starts the menu-bar app.
# Closing this window quits the app. Minimizing it is fine.
cd "$(dirname "$0")" || exit 1
if ! /usr/bin/python3 -c 'import AppKit, IOBluetooth' >/dev/null 2>&1; then
  echo "Installing the Mac Bluetooth libraries for this Mac's Python..."
  /usr/bin/python3 -m pip install --user -r requirements.txt || exit 1
fi
exec /usr/bin/python3 menubar/qc45menu.py
