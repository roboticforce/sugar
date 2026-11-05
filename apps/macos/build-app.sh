#!/usr/bin/env bash
#
# Build SugarDash and assemble a launchable .app bundle.
#
# Usage:  ./build-app.sh
# Result: dist/SugarDash.app  (ad-hoc signed, launch with `open dist/SugarDash.app`)
#
set -euo pipefail
cd "$(dirname "$0")"

APP_NAME="SugarDash"
EXECUTABLE="SugarDash"
BUNDLE_ID="io.roboticforce.sugardash"
VERSION="1.0.0"
BUILD_NUMBER="1"
MIN_MACOS="14.0"

DIST_DIR="$PWD/dist"
APP_DIR="$DIST_DIR/$APP_NAME.app"

# --- Swift build environment -------------------------------------------------
# This package has no dependencies, so SwiftPM's manifest sandbox buys us
# nothing; disabling it keeps the build working in restricted environments
# (CI containers, agent sandboxes, some MDM setups) where nested sandboxes are
# denied. Caches live in a temp dir so the build stays self-contained.
CACHE_ROOT="${SUGAR_SWIFT_CACHE:-/tmp/sugar-swift-cache}"
mkdir -p "$CACHE_ROOT/clang" "$CACHE_ROOT/swift" "$CACHE_ROOT/spm" "$CACHE_ROOT/spmconf"
export CLANG_MODULE_CACHE_PATH="$CACHE_ROOT/clang"
export SWIFT_MODULECACHE_PATH="$CACHE_ROOT/swift"

SWIFT_BUILD_FLAGS=(
    --disable-sandbox
    --cache-path "$CACHE_ROOT/spm"
    --config-path "$CACHE_ROOT/spmconf"
)

echo "==> Building $APP_NAME (release)"

# SwiftPM's incremental build has been seen to relink a stale release product
# after a source change (with the custom cache/config paths used here), which
# silently ships an old binary. If any source is newer than the release
# product, drop the build directory and compile from scratch.
BIN_DIR="$(swift build "${SWIFT_BUILD_FLAGS[@]}" -c release --show-bin-path)"
PRODUCT="$BIN_DIR/$EXECUTABLE"
if [[ -x "$PRODUCT" ]] && [[ -n "$(find Sources Tests -type f -newer "$PRODUCT" 2>/dev/null | head -1)" ]]; then
    echo "==> Sources newer than release product; forcing a clean rebuild"
    rm -rf .build
fi
swift build "${SWIFT_BUILD_FLAGS[@]}" -c release
BIN_DIR="$(swift build "${SWIFT_BUILD_FLAGS[@]}" -c release --show-bin-path)"
BINARY="$BIN_DIR/$EXECUTABLE"

if [[ ! -x "$BINARY" ]]; then
    echo "error: expected release binary at $BINARY" >&2
    exit 1
fi

echo "==> Assembling $APP_DIR"
rm -rf "$APP_DIR"
mkdir -p "$APP_DIR/Contents/MacOS" "$APP_DIR/Contents/Resources"

cp "$BINARY" "$APP_DIR/Contents/MacOS/$EXECUTABLE"
chmod +x "$APP_DIR/Contents/MacOS/$EXECUTABLE"

cat > "$APP_DIR/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleDevelopmentRegion</key>
    <string>en</string>
    <key>CFBundleExecutable</key>
    <string>$EXECUTABLE</string>
    <key>CFBundleIdentifier</key>
    <string>$BUNDLE_ID</string>
    <key>CFBundleInfoDictionaryVersion</key>
    <string>6.0</string>
    <key>CFBundleName</key>
    <string>$APP_NAME</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>$VERSION</string>
    <key>CFBundleVersion</key>
    <string>$BUILD_NUMBER</string>
    <key>LSMinimumSystemVersion</key>
    <string>$MIN_MACOS</string>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>NSPrincipalClass</key>
    <string>NSApplication</string>
    <key>NSAppTransportSecurity</key>
    <dict>
        <!-- The dashboard is loopback-only plain HTTP. Keep ATS on for
             everything else; allow local networking so 127.0.0.1 works. -->
        <key>NSAllowsLocalNetworking</key>
        <true/>
    </dict>
</dict>
</plist>
PLIST

echo "==> Validating Info.plist"
plutil -lint "$APP_DIR/Contents/Info.plist"

echo "==> Signing ad-hoc"
codesign -s - --force --deep "$APP_DIR"
codesign --verify --strict "$APP_DIR"

echo
echo "Built: $APP_DIR"
echo "Run:   open \"$APP_DIR\""
