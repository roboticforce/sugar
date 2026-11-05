#!/usr/bin/env bash
#
# Build SugarDash.app and package it as a drag-to-Applications DMG.
#
# Usage:
#   ./build-dmg.sh                     # native (arm64 on Apple Silicon)
#   ./build-dmg.sh --universal         # arm64 + x86_64 (needs both SDKs)
#   SUGAR_CODESIGN_IDENTITY="Developer ID Application: Name (TEAMID)" \
#     SUGAR_NOTARY_PROFILE=notary-profile ./build-dmg.sh --universal --notarize
#
# Signing:
#   No identity  -> ad-hoc signed. Runs locally; recipients must right-click
#                   Open (or `xattr -dr com.apple.quarantine`) once.
#   Developer ID -> signed with hardened runtime + timestamp. Add --notarize
#                   (and a `xcrun notarytool` keychain profile) for a DMG that
#                   opens with no Gatekeeper warning.
#
# Result: dist/SugarDash-<version>.dmg
#
set -euo pipefail
cd "$(dirname "$0")"

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "error: DMG packaging requires macOS" >&2
    exit 1
fi

UNIVERSAL=0
NOTARIZE=0
IDENTITY="${SUGAR_CODESIGN_IDENTITY:--}"
NOTARY_PROFILE="${SUGAR_NOTARY_PROFILE:-}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --universal) UNIVERSAL=1 ;;
        --notarize) NOTARIZE=1 ;;
        --identity) shift; IDENTITY="${1:?--identity needs a value}" ;;
        --notary-profile) shift; NOTARY_PROFILE="${1:?--notary-profile needs a value}" ;;
        -h|--help)
            sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "error: unknown option: $1" >&2
            exit 2
            ;;
    esac
    shift
done

if [[ "$NOTARIZE" == "1" && -z "$NOTARY_PROFILE" ]]; then
    echo "error: --notarize needs SUGAR_NOTARY_PROFILE (a notarytool keychain profile)" >&2
    echo "       xcrun notarytool store-credentials <profile> --apple-id ... --team-id ..." >&2
    exit 2
fi

echo "==> Building SugarDash.app (universal=$UNIVERSAL, identity=$IDENTITY)"
SUGAR_UNIVERSAL="$UNIVERSAL" \
SUGAR_CODESIGN_IDENTITY="$IDENTITY" \
    ./build-app.sh

APP_DIR="$PWD/dist/SugarDash.app"
VERSION="$(cat dist/SugarDash.version 2>/dev/null || echo 0.0.0)"
DMG="$PWD/dist/SugarDash-${VERSION}.dmg"

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

echo "==> Staging"
cp -R "$APP_DIR" "$STAGE/SugarDash.app"
ln -s /Applications "$STAGE/Applications"

echo "==> Creating $DMG"
rm -f "$DMG"
hdiutil create \
    -volname "SugarDash" \
    -srcfolder "$STAGE" \
    -fs HFS+ \
    -format UDZO \
    -ov \
    "$DMG" >/dev/null

if [[ "$IDENTITY" != "-" ]]; then
    echo "==> Signing the DMG with: $IDENTITY"
    codesign -s "$IDENTITY" --force --timestamp "$DMG"
fi

hdiutil verify "$DMG" >/dev/null
echo "==> Verified: $DMG ($(du -h "$DMG" | cut -f1))"

if [[ "$NOTARIZE" == "1" ]]; then
    echo "==> Submitting for notarization (profile: $NOTARY_PROFILE)"
    xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" --wait
    echo "==> Stapling"
    xcrun stapler staple "$DMG"
    xcrun stapler validate "$DMG"
fi

echo
if [[ "$IDENTITY" == "-" ]]; then
    echo "Built (ad-hoc signed): $DMG"
    echo "Recipients: right-click the app > Open, or run"
    echo "  xattr -dr com.apple.quarantine /Applications/SugarDash.app"
    echo "For a warning-free download, sign with a Developer ID and --notarize."
else
    echo "Built and signed: $DMG"
fi
