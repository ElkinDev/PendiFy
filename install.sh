#!/bin/bash
# PendiFy for macOS: installs into its own virtual environment, makes ~/Applications/PendiFy.app and starts it.
# No administrator rights. Run it again to update; the config and the link with the phone are kept.
#
#   curl -fsSL https://raw.githubusercontent.com/ElkinDev/PendiFy/main/install.sh | bash
#
# PENDIFY_DRYRUN=1        says what it would do and changes nothing
# PENDIFY_NOSTART=1       does not start the program at the end
# PENDIFY_SOURCE          what pip installs (default: pendify, from PyPI); a folder or a git URL also works
# PENDIFY_BUILD_APP=1     builds the app on this Mac instead of downloading the signed one
# PENDIFY_SIGN_IDENTITY   the identity an app built here is signed with (default: a Developer ID in the keychain,
#                         else ad hoc); - for ad hoc
# PENDIFY_LSREGISTER      the LaunchServices tool the app is registered with (the tests name a stand-in)
set -euo pipefail

SUPPORT="$HOME/Library/Application Support/pendify"
VENV="$SUPPORT/venv"
APP="$HOME/Applications/PendiFy.app"
AGENT="$HOME/Library/LaunchAgents/io.github.elkindev.pendify.plist"
SOURCE="${PENDIFY_SOURCE:-pendify}"
DRY="${PENDIFY_DRYRUN:-}"
LSREGISTER="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
LSREGISTER="${PENDIFY_LSREGISTER:-$LSREGISTER}"

# The app prebuilt by tools/release-macos-app.sh, signed with PendiFy's Developer ID and notarized by Apple. A release
# writes these two lines; while either is empty, the app is built on this Mac.
MACOS_APP_URL=""
MACOS_APP_SHA256=""
# The team the downloaded app must be signed by.
MACOS_APP_TEAM="BL4KZH338B"

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

# The app: its main executable is PendiFy's launcher (tools/macos_launcher.c), which starts the environment's Python
# as its child, so macOS lists the app as "PendiFy", with its icon, for the Accessibility permission. No Dock icon (the page
# and its «Salir» button are the program's face). Opened again, it opens the page of the copy that runs.
build_app() {
    local pkg identity
    pkg="$("$VENV/bin/python" -c 'import pendify, pathlib; print(pathlib.Path(pendify.__file__).parent)')"
    rm -rf "$APP"
    mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
    cp "$pkg/macos/PendiFy" "$APP/Contents/MacOS/PendiFy"
    chmod 755 "$APP/Contents/MacOS/PendiFy"
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
    <key>CFBundleShortVersionString</key><string>1</string>
    <key>LSMinimumSystemVersion</key><string>11.0</string>
    <key>LSUIElement</key><true/>
</dict>
</plist>
EOF
    sips -s format icns "$pkg/pendify.ico" --out "$APP/Contents/Resources/PendiFy.icns" >/dev/null 2>&1 || true
    # Signed with the owner's Developer ID when the keychain holds one, so macOS names the developer in Login Items
    # instead of "unidentified developer"; ad hoc otherwise, or when that signing fails. A fixed identifier either
    # way: the same bytes and identity give the same signature, so the permission is kept. PENDIFY_SIGN_IDENTITY
    # names another identity, or - for ad hoc.
    identity=${PENDIFY_SIGN_IDENTITY:-$(security find-identity -v -p codesigning 2>/dev/null \
        | sed -n 's/.*"\(Developer ID Application: [^"]*\)".*/\1/p' | head -n 1)}
    [ -n "$identity" ] || identity=-
    codesign --force --sign "$identity" --timestamp=none --identifier io.github.elkindev.pendify.app "$APP" \
        >/dev/null 2>&1 \
        || { [ "$identity" != - ] && codesign --force --sign - --identifier io.github.elkindev.pendify.app "$APP" \
             >/dev/null 2>&1; } \
        || say "The app could not be signed; macOS may list it under another name."
}

# The signed app, downloaded into a temporary folder and put in place only when its sha256, its signature and its
# team all check. On a failure WHY says, in a few words, which check failed.
WHY=""
fetch_into() {
    local work="$1" sum team
    curl -fsSL --retry 2 -o "$work/PendiFy.zip" "$MACOS_APP_URL" || { WHY="the download failed"; return 1; }
    sum="$(shasum -a 256 "$work/PendiFy.zip" | cut -d ' ' -f 1)"
    [ "$sum" = "$MACOS_APP_SHA256" ] || { WHY="its sha256 is not the one expected"; return 1; }
    ditto -x -k "$work/PendiFy.zip" "$work/unpacked" || { WHY="it could not be unpacked"; return 1; }
    [ -d "$work/unpacked/PendiFy.app" ] || { WHY="it holds no PendiFy.app"; return 1; }
    codesign --verify --deep --strict "$work/unpacked/PendiFy.app" >/dev/null 2>&1 \
        || { WHY="its signature does not verify"; return 1; }
    team="$(codesign -dv "$work/unpacked/PendiFy.app" 2>&1 | sed -n 's/^TeamIdentifier=//p')"
    [ "$team" = "$MACOS_APP_TEAM" ] \
        || { WHY="its team is not $MACOS_APP_TEAM (codesign reads: ${team:-nothing})"; return 1; }
    rm -rf "$APP"
    mkdir -p "$(dirname "$APP")"
    ditto "$work/unpacked/PendiFy.app" "$APP" || { WHY="it could not be put at $APP"; return 1; }
}
fetch_app() {
    local work code=0
    work="$(mktemp -d "${TMPDIR:-/tmp}/pendify-app.XXXXXX")" \
        || { WHY="no temporary folder could be made"; return 1; }
    fetch_into "$work" || code=1
    rm -rf "$work"
    return "$code"
}

say "Making $APP"
if [ "${PENDIFY_BUILD_APP:-}" = 1 ]; then
    say "Building the app here: PENDIFY_BUILD_APP=1 asks for it."
    [ -n "$DRY" ] || build_app
elif [ -z "$MACOS_APP_URL" ] || [ -z "$MACOS_APP_SHA256" ]; then
    say "Building the app here: no signed app is published yet."
    [ -n "$DRY" ] || build_app
elif [ -n "$DRY" ]; then
    say "  (dry) download $MACOS_APP_URL; kept if its sha256, signature and team check, else built here"
elif fetch_app; then
    say "Downloaded the app signed by team $MACOS_APP_TEAM."
else
    say "Building the app here: the signed download was not used, $WHY."
    build_app
fi
if [ -z "$DRY" ]; then
    touch "$APP"
    "$LSREGISTER" -f "$APP" >/dev/null 2>&1 || true
fi

# A start at logon turned on from the page is rewritten to go through the app, as the page writes it.
if [ -f "$AGENT" ]; then
    say "Updating the start with macOS"
    PENDIFY_APP="$APP/Contents/MacOS/PendiFy" run "$VENV/bin/python" -c \
        'from pendify.autostart import Autostart; Autostart().enable()'
fi

say ""
say "PendiFy is installed: $APP"
say "Open it from Spotlight or Launchpad, or run: open \"$APP\""
if [ -z "${PENDIFY_NOSTART:-}" ]; then
    say "Starting PendiFy: the page opens in the browser."
    run open "$APP"
fi
