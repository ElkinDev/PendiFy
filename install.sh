#!/bin/bash
# PendiFy for macOS: installs into its own virtual environment, makes ~/Applications/PendiFy.app and starts it.
# No administrator rights. Run it again to update; the config and the link with the phone are kept.
#
#   curl -fsSL https://raw.githubusercontent.com/ElkinDev/PendiFy/main/install.sh | bash
#
# PENDIFY_DRYRUN=1   says what it would do and changes nothing
# PENDIFY_NOSTART=1  does not start the program at the end
# PENDIFY_SOURCE     what pip installs (default: pendify, from PyPI); a folder or a git URL also works
set -euo pipefail

SUPPORT="$HOME/Library/Application Support/pendify"
VENV="$SUPPORT/venv"
APP="$HOME/Applications/PendiFy.app"
AGENT="$HOME/Library/LaunchAgents/io.github.elkindev.pendify.plist"
SOURCE="${PENDIFY_SOURCE:-pendify}"
DRY="${PENDIFY_DRYRUN:-}"

say() { printf '%s\n' "$*"; }
run() { if [ -n "$DRY" ]; then say "  (dry) $*"; else "$@"; fi; }

if [ "$(uname -s)" != "Darwin" ]; then
    say "This installer is for macOS. On Windows use install.ps1."
    exit 1
fi

# Python 3.10 or newer: the newest one found first.
python_ok() { "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; }
find_python() {
    local candidate
    for candidate in python3.13 python3.12 python3.11 python3.10 \
                     /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
        if command -v "$candidate" >/dev/null 2>&1 && python_ok "$(command -v "$candidate")"; then
            command -v "$candidate"
            return 0
        fi
    done
    return 1
}

PYTHON="$(find_python || true)"
if [ -z "$PYTHON" ]; then
    if command -v brew >/dev/null 2>&1; then
        say "Python 3.10 or newer was not found: installing python@3.12 with Homebrew."
        run brew install python@3.12
        PYTHON="$(find_python || true)"
    fi
    if [ -z "$PYTHON" ] && [ -z "$DRY" ]; then
        say "Python 3.10 or newer is needed. Install it from https://www.python.org/downloads/ and run this again."
        exit 1
    fi
fi
say "Python: ${PYTHON:-python3 (dry run)}"

# Its own environment: Homebrew's Python refuses pip --user (PEP 668), and nothing of the system's is touched.
if [ ! -x "$VENV/bin/python" ] || ! python_ok "$VENV/bin/python"; then
    say "Making the environment in $VENV"
    run rm -rf "$VENV"
    run mkdir -p "$SUPPORT"
    run "$PYTHON" -m venv "$VENV"
fi
say "Installing $SOURCE"
run "$VENV/bin/python" -m pip install --quiet --disable-pip-version-check --upgrade "$SOURCE"

# The app: a small bundle whose program starts PendiFy with the environment's Python, with no Dock icon (the page
# and its «Salir» button are the program's face). Opened again, it opens the page of the copy that runs.
say "Making $APP"
if [ -z "$DRY" ]; then
    rm -rf "$APP"
    mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
    cat > "$APP/Contents/MacOS/PendiFy" <<EOF
#!/bin/bash
exec "$VENV/bin/python" -m pendify "\$@" >>"$SUPPORT/pendify.log" 2>&1
EOF
    chmod +x "$APP/Contents/MacOS/PendiFy"
    VERSION="$("$VENV/bin/python" -c 'import importlib.metadata as m; print(m.version("pendify"))' 2>/dev/null || echo 0)"
    cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>PendiFy</string>
    <key>CFBundleDisplayName</key><string>PendiFy</string>
    <key>CFBundleIdentifier</key><string>io.github.elkindev.pendify.app</string>
    <key>CFBundleExecutable</key><string>PendiFy</string>
    <key>CFBundleIconFile</key><string>PendiFy</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>$VERSION</string>
    <key>LSUIElement</key><true/>
</dict>
</plist>
EOF
    ICO="$("$VENV/bin/python" -c 'import pendify, pathlib; print(pathlib.Path(pendify.__file__).with_name("pendify.ico"))')"
    sips -s format icns "$ICO" --out "$APP/Contents/Resources/PendiFy.icns" >/dev/null 2>&1 || true
    touch "$APP"
fi

# A start at logon turned on from the page is rewritten with this environment's Python.
if [ -f "$AGENT" ]; then
    say "Updating the start with macOS"
    run "$VENV/bin/python" -c 'from pendify.autostart import Autostart; Autostart().enable()'
fi

say ""
say "PendiFy is installed: $APP"
say "Open it from Spotlight or Launchpad, or run: open \"$APP\""
if [ -z "${PENDIFY_NOSTART:-}" ]; then
    say "Starting PendiFy: the page opens in the browser."
    run open "$APP"
fi
