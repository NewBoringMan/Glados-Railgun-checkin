#!/bin/bash
set -euo pipefail

SOURCE_APP="${1:-/Users/enoch/Applications/GLaDOS Account Center.app}"
OUTPUT_APP="${2:-/Users/enoch/LocalAnt-Sandbox/GLaDOS Account Center-v2.0.7-build.app}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VERSION="2.0.7"
BUILD="20007"

if [[ ! -d "$SOURCE_APP/Contents" ]]; then
  echo "Source app not found: $SOURCE_APP" >&2
  exit 2
fi
if [[ "$SOURCE_APP" == "$OUTPUT_APP" ]]; then
  echo "Source and output must be different paths." >&2
  exit 2
fi

python3 -c 'import pathlib,shutil,sys; p=pathlib.Path(sys.argv[1]); shutil.rmtree(p) if p.exists() else None' "$OUTPUT_APP"
/usr/bin/ditto "$SOURCE_APP" "$OUTPUT_APP"

MACOS="$OUTPUT_APP/Contents/MacOS"
FRAMEWORKS="$OUTPUT_APP/Contents/Frameworks"
HELPER="$OUTPUT_APP/Contents/Applications/GLaDOS Exchange Policy.app"

mkdir -p "$FRAMEWORKS" "$HELPER/Contents/MacOS"

if [[ -x "$MACOS/GLaDOSAccountCenter.real" ]]; then
  cp "$MACOS/GLaDOSAccountCenter.real" "$MACOS/GLaDOSAccountCenter"
else
  mv "$MACOS/GLaDOSAccountCenter" "$MACOS/GLaDOSAccountCenter.real"
fi

xcrun clang -arch arm64 \
  -o "$MACOS/GLaDOSAccountCenter" \
  "$SCRIPT_DIR/launcher.c"

xcrun clang -arch arm64 -dynamiclib -fobjc-arc -framework AppKit \
  -o "$FRAMEWORKS/PolicyMenuPlugin.dylib" \
  "$SCRIPT_DIR/PolicyMenuPlugin.m"

xcrun swiftc -parse-as-library -target arm64-apple-macos13.0 \
  -framework SwiftUI -framework AppKit \
  -o "$HELPER/Contents/MacOS/GLaDOSPolicyEditor" \
  "$SCRIPT_DIR/PolicyEditor.swift"

cp "$SCRIPT_DIR/PolicyEditor-Info.plist" "$HELPER/Contents/Info.plist"

/usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier com.enoch.glados-account-center" "$OUTPUT_APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleName GLaDOS Account Center" "$OUTPUT_APP/Contents/Info.plist"
if /usr/libexec/PlistBuddy -c 'Print :CFBundleDisplayName' "$OUTPUT_APP/Contents/Info.plist" >/dev/null 2>&1; then
  /usr/libexec/PlistBuddy -c "Set :CFBundleDisplayName GLaDOS Account Center" "$OUTPUT_APP/Contents/Info.plist"
else
  /usr/libexec/PlistBuddy -c "Add :CFBundleDisplayName string GLaDOS Account Center" "$OUTPUT_APP/Contents/Info.plist"
fi
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" "$OUTPUT_APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleVersion $BUILD" "$OUTPUT_APP/Contents/Info.plist"

codesign --force --sign - "$HELPER"
codesign --force --sign - "$FRAMEWORKS/PolicyMenuPlugin.dylib"
codesign --force --deep --sign - "$OUTPUT_APP"
codesign --verify --deep --strict "$OUTPUT_APP"

echo "Built: $OUTPUT_APP"
echo "Version: $VERSION ($BUILD)"
