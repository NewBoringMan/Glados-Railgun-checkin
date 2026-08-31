#!/bin/bash
set -euo pipefail

SOURCE_APP="${1:-/Users/enoch/Applications/GLaDOS Account Center.app}"
OUTPUT_APP="${2:-/private/tmp/GLaDOS-Account-Center-v2.0.8-stage.app}"
SAFARI_BRIDGE_APP="${3:-/Users/enoch/Applications/GLaDOS Safari Bridge.app}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VERSION="2.0.8"
BUILD="20008"

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
PLUGINS="$OUTPUT_APP/Contents/PlugIns"
CAPTURE="$OUTPUT_APP/Contents/Resources/capture_account.js"
EMBEDDED_SAFARI="$PLUGINS/GLaDOS Safari Bridge Extension.appex"
SOURCE_EMBEDDED="$SOURCE_APP/Contents/PlugIns/GLaDOS Safari Bridge Extension.appex"
STANDALONE_EMBEDDED="$SAFARI_BRIDGE_APP/Contents/PlugIns/GLaDOS Safari Bridge Extension.appex"

mkdir -p "$FRAMEWORKS" "$PLUGINS"
python3 -c 'import pathlib,shutil,sys; p=pathlib.Path(sys.argv[1]); shutil.rmtree(p) if p.exists() else None' "$OUTPUT_APP/Contents/Applications"

if [[ -x "$MACOS/GLaDOSAccountCenter.real" ]]; then
  cp "$MACOS/GLaDOSAccountCenter.real" "$MACOS/GLaDOSAccountCenter"
else
  mv "$MACOS/GLaDOSAccountCenter" "$MACOS/GLaDOSAccountCenter.real"
fi

xcrun clang -arch arm64 \
  -o "$MACOS/GLaDOSAccountCenter" \
  "$SCRIPT_DIR/launcher.c"

xcrun swiftc -parse-as-library -emit-library \
  -module-name GLaDOSPolicyEditor \
  -target arm64-apple-macos13.0 \
  -framework SwiftUI -framework AppKit \
  -o "$FRAMEWORKS/GLaDOSPolicyEditor.dylib" \
  "$SCRIPT_DIR/PolicyEditor.swift"

xcrun clang -arch arm64 -dynamiclib -fobjc-arc -framework AppKit \
  -o "$FRAMEWORKS/PolicyMenuPlugin.dylib" \
  "$SCRIPT_DIR/PolicyMenuPlugin.m"

if [[ ! -d "$EMBEDDED_SAFARI" ]]; then
  if [[ -d "$SOURCE_EMBEDDED" ]]; then
    /usr/bin/ditto "$SOURCE_EMBEDDED" "$EMBEDDED_SAFARI"
  elif [[ -d "$STANDALONE_EMBEDDED" ]]; then
    /usr/bin/ditto "$STANDALONE_EMBEDDED" "$EMBEDDED_SAFARI"
  else
    echo "Safari extension source not found; refusing to build a version that loses Safari support." >&2
    exit 3
  fi
fi

python3 - "$CAPTURE" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
text = p.read_text(encoding='utf-8')
old_const = "const SAFARI_COMPANION_APP = path.join(os.homedir(), 'Applications', 'GLaDOS Safari Bridge.app');"
new_const = "const SAFARI_EXTENSION_BUNDLE = path.resolve(__dirname, '..', 'PlugIns', 'GLaDOS Safari Bridge Extension.appex');"
old_check = """  if (!fs.existsSync(SAFARI_COMPANION_APP)) {
    throw new Error('尚未安装“GLaDOS Safari Bridge.app”。请运行交付包中的“构建并安装 Safari 扩展.command”。');
  }
"""
new_check = """  if (!fs.existsSync(SAFARI_EXTENSION_BUNDLE)) {
    throw new Error('GLaDOS Account Center 内置的 Safari 扩展缺失。请重新安装 GLaDOS Account Center。');
  }
"""
if old_const in text:
    text = text.replace(old_const, new_const, 1)
if old_check in text:
    text = text.replace(old_check, new_check, 1)
if 'SAFARI_EXTENSION_BUNDLE' not in text or 'GLaDOS Safari Bridge.app' in text:
    raise SystemExit('Safari bridge integration patch did not apply cleanly')
p.write_text(text, encoding='utf-8')
PY

/usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier com.enoch.glados-account-center" "$OUTPUT_APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleName GLaDOS Account Center" "$OUTPUT_APP/Contents/Info.plist"
if /usr/libexec/PlistBuddy -c 'Print :CFBundleDisplayName' "$OUTPUT_APP/Contents/Info.plist" >/dev/null 2>&1; then
  /usr/libexec/PlistBuddy -c "Set :CFBundleDisplayName GLaDOS Account Center" "$OUTPUT_APP/Contents/Info.plist"
else
  /usr/libexec/PlistBuddy -c "Add :CFBundleDisplayName string GLaDOS Account Center" "$OUTPUT_APP/Contents/Info.plist"
fi
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" "$OUTPUT_APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleVersion $BUILD" "$OUTPUT_APP/Contents/Info.plist"

codesign --force --sign - "$EMBEDDED_SAFARI"
codesign --force --sign - "$FRAMEWORKS/GLaDOSPolicyEditor.dylib"
codesign --force --sign - "$FRAMEWORKS/PolicyMenuPlugin.dylib"
codesign --force --deep --sign - "$OUTPUT_APP"
codesign --verify --deep --strict "$OUTPUT_APP"

if find "$OUTPUT_APP/Contents" -mindepth 2 -name '*.app' -print -quit | grep -q .; then
  echo "Nested .app detected; refusing single-app build." >&2
  find "$OUTPUT_APP/Contents" -mindepth 2 -name '*.app' -print >&2
  exit 4
fi
if [[ ! -d "$EMBEDDED_SAFARI" ]]; then
  echo "Embedded Safari extension missing after build." >&2
  exit 5
fi

echo "Built: $OUTPUT_APP"
echo "Version: $VERSION ($BUILD)"
echo "Single-app layout: OK"
