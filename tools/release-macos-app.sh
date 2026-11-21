#!/bin/bash
# Publishes PendiFy.app for macOS, prebuilt: built as install.sh builds it, signed with the Developer ID of team
# TEAM_ID (hardened runtime, secure timestamp), notarized by Apple with the ticket stapled, zipped, published as a
# GitHub release, and its URL and sha256 written into install.sh, which is then committed and pushed. install.sh
# downloads that app, so a Mac without the owner's key gets an app macOS names the developer of.
# Only the maintainer runs it, on a Mac whose keychain holds that identity and the notary credentials (README,
# "Releasing the macOS app"). Needs the Xcode command line tools and, to publish, gh signed in.
#
#   tools/release-macos-app.sh               the whole release
#   tools/release-macos-app.sh --no-publish  builds and signs only: no notarization, no release, install.sh untouched
#
# NOTARY_PROFILE  the notarytool keychain profile the credentials are stored under (default: PendiFy)
set -euo pipefail
cd "$(dirname "$0")/.."

TEAM_ID=BL4KZH338B
BUNDLE_ID=io.github.elkindev.pendify.app
REPO=ElkinDev/PendiFy
NOTARY_PROFILE="${NOTARY_PROFILE:-PendiFy}"
LAUNCHER=src/pendify/macos/PendiFy
ICON=src/pendify/pendify.ico
OUT=build/macos-app
APP="$OUT/PendiFy.app"

say() { printf '%s\n' "$*"; }
die() { printf '%s\n' "$*" >&2; exit 1; }

publish=1
case "${1:-}" in
    "") ;;
    --no-publish) publish="" ;;
    *) die "usage: tools/release-macos-app.sh [--no-publish]" ;;
esac
[ "$(uname -s)" = Darwin ] || die "The app is built, signed and notarized on macOS only."

version="$(awk -F '"' '/^version = "/ {print $2; exit}' pyproject.toml)"
[ -n "$version" ] || die "No version found in pyproject.toml."
tag="macos-app-$version"
zip_name="PendiFy-macos-$version.zip"
url="https://github.com/$REPO/releases/download/$tag/$zip_name"

# A release is made from a commit GitHub holds, with no change beside it, so its tag names the source the app is built
# from and the edit of install.sh is the only change left to commit. Checked before the long steps.
if [ -n "$publish" ]; then
    git diff --quiet HEAD -- || die "The tree has changes; commit or stash them first."
    [ -n "$(git branch -r --contains HEAD 2>/dev/null)" ] || die "Push this commit first: the release's tag names it."
    gh auth status >/dev/null 2>&1 || die "gh is not signed in: run gh auth login."
    ! gh release view "$tag" --repo "$REPO" >/dev/null 2>&1 \
        || die "The release $tag exists already; raise the version in pyproject.toml for a new one."
    xcrun notarytool history --keychain-profile "$NOTARY_PROFILE" >/dev/null 2>&1 \
        || die "No notary credentials under the profile $NOTARY_PROFILE; README, Releasing the macOS app, says how."
fi

# The Info.plist is read from install.sh, so the app published and the app built on a Mac never differ.
plist="$(awk '/Contents\/Info\.plist" <<EOF$/ {on = 1; next} on && /^EOF$/ {exit} on' install.sh)"
[ -n "$plist" ] || die "No Info.plist found in install.sh."

rm -rf "$OUT"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
printf '%s\n' "$plist" > "$APP/Contents/Info.plist"
plutil -lint -s "$APP/Contents/Info.plist" || die "The Info.plist read from install.sh is not a valid plist."
read_plist() { /usr/libexec/PlistBuddy -c "Print :$1" "$APP/Contents/Info.plist"; }
[ "$(read_plist CFBundleIdentifier)" = "$BUNDLE_ID" ] || die "install.sh's Info.plist names another identifier."
minimum="$(read_plist LSMinimumSystemVersion)"

# The launcher: the one the package ships (tools/build_macos_launcher.sh builds it), universal, for the macOS the
# Info.plist asks for, and with no per-user path in it (it reads HOME when it runs), so it starts any user's install.
for arch in arm64 x86_64; do
    lipo "$LAUNCHER" -verify_arch "$arch" 2>/dev/null \
        || die "$LAUNCHER has no $arch slice; rebuild it with tools/build_macos_launcher.sh."
    target="$(otool -arch "$arch" -l "$LAUNCHER" \
        | awk '$1 == "cmd" && $2 == "LC_BUILD_VERSION" {on = 1} on && $1 == "minos" {print $2; exit}')"
    [ "$target" = "$minimum" ] \
        || die "$LAUNCHER's $arch slice targets macOS ${target:-unknown}, not $minimum; rebuild it."
done
! strings "$LAUNCHER" | grep '/Users/' >/dev/null || die "$LAUNCHER holds a per-user path; rebuild it."
cp "$LAUNCHER" "$APP/Contents/MacOS/PendiFy"
chmod 755 "$APP/Contents/MacOS/PendiFy"
sips -s format icns "$ICON" --out "$APP/Contents/Resources/PendiFy.icns" >/dev/null \
    || die "The icon could not be converted."

# Hardened runtime and no entitlements: the launcher only spawns the environment's Python, which runs under its own
# signature (the runtime is not inherited by a child), and macOS asks no entitlement for the Accessibility permission.
identity="$(security find-identity -v -p codesigning \
    | sed -n "s/^ *[0-9]*) \([0-9A-F]\{40\}\) \"Developer ID Application: .* ($TEAM_ID)\"\$/\1/p" | awk 'NR == 1')"
[ -n "$identity" ] || die "No Developer ID Application identity of team $TEAM_ID is in the keychain."
say "Signing $APP (Developer ID, team $TEAM_ID, hardened runtime)"
codesign --force --sign "$identity" --options runtime --timestamp --identifier "$BUNDLE_ID" "$APP"
codesign --verify --deep --strict "$APP" || die "The signature does not verify."
details="$(codesign -dv "$APP" 2>&1)"
grep -qx "TeamIdentifier=$TEAM_ID" <<<"$details" || die "The app is not signed by team $TEAM_ID."
grep -q '^CodeDirectory .*flags=.*runtime' <<<"$details" || die "The app is not signed with the hardened runtime."
grep -q '^Timestamp=' <<<"$details" || die "The signature carries no secure timestamp."

if [ -z "$publish" ]; then
    say "Built and signed, not notarized: $APP"
    exit 0
fi

say "Notarizing: Apple answers in a few minutes"
ditto -c -k --keepParent "$APP" "$OUT/upload.zip"
xcrun notarytool submit "$OUT/upload.zip" --keychain-profile "$NOTARY_PROFILE" --wait --output-format plist \
    > "$OUT/notary.plist" || true
status="$(/usr/libexec/PlistBuddy -c 'Print :status' "$OUT/notary.plist" 2>/dev/null || true)"
id="$(/usr/libexec/PlistBuddy -c 'Print :id' "$OUT/notary.plist" 2>/dev/null || true)"
[ "$status" = Accepted ] || die "Apple did not accept it (${status:-no answer}); its log: xcrun notarytool log \
${id:-<id>} --keychain-profile $NOTARY_PROFILE"
xcrun stapler staple "$APP"
xcrun stapler validate "$APP"
spctl --assess --type execute "$APP" || die "Gatekeeper refuses the notarized app."

ditto -c -k --keepParent "$APP" "$OUT/$zip_name"
sha="$(shasum -a 256 "$OUT/$zip_name" | cut -d ' ' -f 1)"
say "Publishing $tag"
gh release create "$tag" "$OUT/$zip_name" --repo "$REPO" --target "$(git rev-parse HEAD)" --latest=false \
    --title "PendiFy.app $version for macOS" \
    --notes "PendiFy.app signed with the Developer ID of team $TEAM_ID and notarized by Apple. install.sh downloads it \
and checks its sha256 ($sha) and its team; nothing here needs to be downloaded by hand."
curl -fsSL --retry 3 -o "$OUT/published.zip" "$url" || die "The published asset cannot be downloaded from $url."
[ "$(shasum -a 256 "$OUT/published.zip" | cut -d ' ' -f 1)" = "$sha" ] || die "The published asset is not the zip made."

# The two lines of install.sh, each found exactly once, rewritten.
for name in MACOS_APP_URL MACOS_APP_SHA256; do
    [ "$(grep -c "^$name=" install.sh)" = 1 ] || die "install.sh holds no single $name line."
done
sed -i '' -e "s|^MACOS_APP_URL=.*\$|MACOS_APP_URL=\"$url\"|" -e "s|^MACOS_APP_SHA256=.*\$|MACOS_APP_SHA256=\"$sha\"|" \
    install.sh
grep -qx "MACOS_APP_URL=\"$url\"" install.sh && grep -qx "MACOS_APP_SHA256=\"$sha\"" install.sh \
    || die "install.sh was not rewritten as expected."
say ""
say "Published $url"
say "install.sh now names it. Commit and push install.sh so installs download it:"
say "  git commit -m \"chore: install.sh downloads PendiFy.app $version\" install.sh && git push"
