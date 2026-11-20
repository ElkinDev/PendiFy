#!/bin/bash
# Removes PendiFy from this Mac: stops it, removes the app, the start with macOS and its environment.
# The config with the link to the phone (config.json) is kept; delete the folder by hand to remove it too.
#
#   curl -fsSL https://raw.githubusercontent.com/ElkinDev/PendiFy/main/uninstall.sh | bash
set -uo pipefail

SUPPORT="$HOME/Library/Application Support/pendify"
VENV="$SUPPORT/venv"
APP="$HOME/Applications/PendiFy.app"
AGENT="$HOME/Library/LaunchAgents/io.github.elkindev.pendify.plist"

pkill -f "$VENV/bin/python -m pendify" 2>/dev/null && echo "Stopped the running copy."
rm -f "$AGENT" && echo "Removed the start with macOS."
rm -rf "$APP" && echo "Removed $APP"
rm -rf "$VENV" && echo "Removed the environment."
rm -f "$SUPPORT/pendify.log"
echo ""
echo "PendiFy was removed. The config is kept in: $SUPPORT"
