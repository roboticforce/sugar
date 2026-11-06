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
MIN_MACOS="14.0"

# Version comes from the repo's pyproject (fall back to a static value when the
# script is run outside a checkout). CFBundleShortVersionString must look like
# X.Y.Z, so a PEP 440 pre-release suffix (3.10.6.dev1) is kept for the DMG name
# but stripped from the bundle.
PYPROJECT="$(cd ../.. && pwd)/pyproject.toml"
FULL_VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' "$PYPROJECT" 2>/dev/null | head -1)"
FULL_VERSION="${SUGAR_APP_VERSION:-${FULL_VERSION:-0.0.0}}"
VERSION="$(printf '%s' "$FULL_VERSION" | sed -E 's/(\.dev|\.rc|\.post|\+).*$//')"
BUILD_NUMBER="${SUGAR_BUILD_NUMBER:-1}"
IDENTITY="${SUGAR_CODESIGN_IDENTITY:--}"  # "-" = ad-hoc
UNIVERSAL="${SUGAR_UNIVERSAL:-0}"

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
if [[ "$UNIVERSAL" == "1" ]]; then
    echo "==> Building universal (arm64 + x86_64)"
    TMP_BIN="$(mktemp -d)"
    ARCH_BINARIES=()
    for triple in "arm64-apple-macosx$MIN_MACOS" "x86_64-apple-macosx$MIN_MACOS"; do
        scratch=".build-$triple"
        swift build "${SWIFT_BUILD_FLAGS[@]}" -c release \
            --triple "$triple" --scratch-path "$scratch"
        arch_bin="$(swift build "${SWIFT_BUILD_FLAGS[@]}" -c release \
            --triple "$triple" --scratch-path "$scratch" --show-bin-path)/$EXECUTABLE"
        if [[ ! -x "$arch_bin" ]]; then
            echo "error: missing $triple binary at $arch_bin" >&2
            exit 1
        fi
        ARCH_BINARIES+=("$arch_bin")
    done
    BINARY="$TMP_BIN/$EXECUTABLE"
    lipo -create "${ARCH_BINARIES[@]}" -output "$BINARY"
    lipo -info "$BINARY"
else
    swift build "${SWIFT_BUILD_FLAGS[@]}" -c release
    BIN_DIR="$(swift build "${SWIFT_BUILD_FLAGS[@]}" -c release --show-bin-path)"
    BINARY="$BIN_DIR/$EXECUTABLE"
fi

if [[ ! -x "$BINARY" ]]; then
    echo "error: expected release binary at $BINARY" >&2
    exit 1
fi

echo "==> Assembling $APP_DIR"
rm -rf "$APP_DIR"
mkdir -p "$APP_DIR/Contents/MacOS" "$APP_DIR/Contents/Resources"

cp "$BINARY" "$APP_DIR/Contents/MacOS/$EXECUTABLE"
chmod +x "$APP_DIR/Contents/MacOS/$EXECUTABLE"

# App icon (generate with `python3 make-icon.py`; committed so builds are reproducible)
if [[ -f Resources/AppIcon.icns ]]; then
    cp Resources/AppIcon.icns "$APP_DIR/Contents/Resources/AppIcon.icns"
fi

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
    <key>CFBundleDisplayName</key>
    <string>$APP_NAME</string>
    <key>CFBundleIconFile</key>
    <string>AppIcon</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>NSHumanReadableCopyright</key>
    <string>© 2026 RoboticForce Inc. Licensed under AGPL-3.0.</string>
    <key>NSApplicationCategoryType</key>
    <string>public.app-category.developer-tools</string>
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

if [[ "$IDENTITY" == "-" ]]; then
    echo "==> Signing ad-hoc (local use; not notarizable)"
    codesign -s - --force "$APP_DIR"
else
    echo "==> Signing with: $IDENTITY (hardened runtime)"
    codesign -s "$IDENTITY" --force --options runtime --timestamp "$APP_DIR"
fi
codesign --verify --strict "$APP_DIR"

# build-dmg.sh reads the exact version (including .devN) from here.
printf '%s\n' "$FULL_VERSION" > "$DIST_DIR/SugarDash.version"

echo
echo "Built: $APP_DIR"
echo "Version: $FULL_VERSION"
echo "Run:   open \"$APP_DIR\""
