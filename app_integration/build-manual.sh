#!/bin/bash
# Build the original single App from source, preserving unrelated resources when
# an inspected installed App is supplied as the optional second argument.
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 || "$(uname -s)" != Darwin ]]; then
  echo 'Usage (macOS): bash app_integration/build-manual.sh OUTPUT.app [INSPECTED_EXISTING.app]' >&2
  exit 2
fi
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
OUTPUT_APP="$1"
SOURCE_APP="${2:-}"
GLADOS_ARCH="${GLADOS_ARCH:-$(uname -m)}"
case "$GLADOS_ARCH" in arm64|x86_64) ;; *) echo 'Unsupported architecture' >&2; exit 2;; esac
if [[ -e "$OUTPUT_APP" || -L "$OUTPUT_APP" ]]; then
  echo 'Output already exists; choose a new staging path.' >&2
  exit 2
fi
if [[ "$OUTPUT_APP" != *.app ]]; then
  echo 'Output must end in .app' >&2
  exit 2
fi

# Resolve App symlink paths before copying. Neither build
# scratch space nor the final output may be inside the inspected source bundle.
SOURCE_APP="$(python3 - "$SCRIPT_DIR/install-manual.py" "$SOURCE_APP" "$OUTPUT_APP" "${TMPDIR:-/private/tmp}" <<'PY'
import importlib.util, sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('glados_manual_installer', sys.argv[1])
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
if sys.argv[2]:
    source=module.app_path(sys.argv[2])
    for raw_path in (sys.argv[3], sys.argv[4]):
        destination=Path(raw_path).expanduser().resolve()
        if destination == source or source in destination.parents:
            raise SystemExit('Build output and temporary files must be outside the inspected App.')
    if '\n' in str(source) or '\r' in str(source):
        raise SystemExit('The selected App path contains an unsupported newline.')
    print(source)
PY
)"

BUILD_TMP="$(mktemp -d "${TMPDIR:-/private/tmp}/glados-manual-build.XXXXXX")"
cleanup() {
  python3 - "$BUILD_TMP" <<'PY'
import shutil, sys
shutil.rmtree(sys.argv[1], ignore_errors=True)
PY
}
trap cleanup EXIT
STAGE_APP="$BUILD_TMP/GLaDOS Account Center.app"

# Compare the source before and after ditto, and compare the untouched copy with
# that same baseline. A CI build without a source gets an explicit null baseline
# and cannot be used by install-manual.py to replace an existing App.
python3 - "$SCRIPT_DIR/install-manual.py" "$SOURCE_APP" "$STAGE_APP" "$BUILD_TMP/origin.json" "$PROJECT_DIR/macos/Info.plist" <<'PY'
import importlib.util, json, shutil, subprocess, sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('glados_manual_installer', sys.argv[1])
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
app=Path(sys.argv[3])
source_fingerprint=None
if sys.argv[2]:
    source=module.app_path(sys.argv[2])
    source_fingerprint=module.fingerprint(source)
    subprocess.run(['/usr/bin/ditto', str(source), str(app)], check=True)
    if module.fingerprint(source) != source_fingerprint or module.fingerprint(app) != source_fingerprint:
        raise SystemExit('The inspected App changed during copying, or the copy is incomplete.')
else:
    (app/'Contents').mkdir(parents=True)
    shutil.copyfile(sys.argv[5], app/'Contents/Info.plist')
Path(sys.argv[4]).write_text(json.dumps({
    'schema':'glados.manual-build-origin', 'version':1,
    'sourceFingerprint':source_fingerprint,
}, indent=2)+'\n', encoding='utf-8')
PY

MACOS="$STAGE_APP/Contents/MacOS"
FRAMEWORKS="$STAGE_APP/Contents/Frameworks"
RESOURCES="$STAGE_APP/Contents/Resources"
PLUGINS="$STAGE_APP/Contents/PlugIns"

# Remove only the superseded automatic-login implementation inside the new
# staging bundle. App Support, browser profiles, SQLite and Keychain are untouched.
python3 - "$STAGE_APP" "$PROJECT_DIR/macos/Info.plist" "$PROJECT_DIR/macos/Resources" "$BUILD_TMP/origin.json" "$BUILD_TMP/retained-helper.json" <<'PY'
from pathlib import Path
import hashlib, json, plistlib, shutil, subprocess, sys
app=Path(sys.argv[1]).resolve(strict=True)

def writable_path(name, *, directory=False):
    path=app/name
    # Check every existing ancestor, not just the destination: ditto preserves
    # symlinks, including ones that point back into the original installed App.
    for parent in (path, *path.parents):
        if parent == app:
            break
        if parent.is_symlink():
            raise SystemExit(f'Unsafe symlink in a build destination: {name}')
    if path.exists() and not (path.is_dir() if directory else path.is_file()):
        raise SystemExit(f'Unexpected type for a build destination: {name}')
    if app not in path.resolve().parents:
        raise SystemExit(f'Build destination escapes the staging App: {name}')
    return path

for name in ('Contents', 'Contents/MacOS', 'Contents/Frameworks', 'Contents/Resources',
             'Contents/PlugIns', 'Contents/Applications', 'Contents/_CodeSignature'):
    writable_path(name, directory=True)
for name in ('Contents/Info.plist', 'Contents/MacOS/GLaDOSAccountCenter',
             'Contents/MacOS/GLaDOSAccountCenter.real',
             'Contents/Frameworks/GLaDOSPolicyEditor.dylib',
             'Contents/Frameworks/PolicyMenuPlugin.dylib',
             'Contents/Resources/AppIcon.icns', 'Contents/Resources/manual-build-origin.json',
             'Contents/_CodeSignature/CodeResources', 'Contents/MacOS/RefreshSecretStore'):
    writable_path(name)
for resource in Path(sys.argv[3]).glob('*.js'):
    writable_path('Contents/Resources/'+resource.name)

# Only the rebuilt nested code will be signed below. Retain the helper's bytes
# and designated requirement so existing Keychain ACL references stay intact.
helper=app/'Contents/MacOS/RefreshSecretStore'
retained=None
if helper.exists():
    subprocess.run(['/usr/bin/codesign', '--verify', '--strict', str(helper)], check=True)
    result=subprocess.run(['/usr/bin/codesign', '-d', '-r-', str(helper)],
                          check=True, capture_output=True, text=True)
    requirement=[line for line in (result.stdout+'\n'+result.stderr).splitlines()
                 if line.startswith('designated =>')]
    if len(requirement) != 1:
        raise SystemExit('Could not record the retained Keychain helper signature.')
    retained={'sha256':hashlib.sha256(helper.read_bytes()).hexdigest(),
              'designatedRequirement':requirement[0]}
Path(sys.argv[5]).write_text(json.dumps(retained)+'\n', encoding='utf-8')

for name in ('Contents/MacOS', 'Contents/Frameworks', 'Contents/Resources', 'Contents/PlugIns'):
    (app/name).mkdir(parents=True, exist_ok=True)
for name in (
    'Contents/Resources/LoginRefresh',
    'Contents/Frameworks/GLaDOSRefreshCenter.dylib',
    'Contents/MacOS/RefreshNotifications',
    'Contents/MacOS/LocalMailReader',
    'Contents/Applications/GLaDOS Safari Bridge.app',
    'Contents/PlugIns/GLaDOS Safari Bridge Extension.appex',
):
    path=app/name
    if path.is_symlink() or path.is_file(): path.unlink()
    elif path.is_dir(): shutil.rmtree(path)
# RefreshSecretStore is deliberately retained if present: old Keychain items may
# still need its existing ACL. It has no launcher/menu/scheduler in this build.
info_path=app/'Contents/Info.plist'
with info_path.open('rb') as f: info=plistlib.load(f)
with Path(sys.argv[2]).open('rb') as f: fresh=plistlib.load(f)
for key in ('CFBundleIdentifier','CFBundleExecutable','CFBundleName','CFBundleDisplayName',
            'CFBundleShortVersionString','CFBundleVersion','LSMinimumSystemVersion'):
    info[key]=fresh[key]
info.pop('GLaDOSRefreshPython', None)
with info_path.open('wb') as f: plistlib.dump(info,f)
shutil.copyfile(sys.argv[4], app/'Contents/Resources/manual-build-origin.json')
PY

cp "$PROJECT_DIR/macos/Resources/"*.js "$RESOURCES/"
cp "$PROJECT_DIR/macos/Resources/AppIcon.icns" "$RESOURCES/AppIcon.icns"

xcrun swiftc -swift-version 5 -O -parse-as-library \
  -target "$GLADOS_ARCH-apple-macos13.0" \
  -framework SwiftUI -framework AppKit -framework Security -framework CryptoKit \
  -lsqlite3 -o "$MACOS/GLaDOSAccountCenter.real" \
  "$PROJECT_DIR/macos/Sources/"*.swift
xcrun clang -arch "$GLADOS_ARCH" -o "$MACOS/GLaDOSAccountCenter" "$SCRIPT_DIR/launcher.c"
xcrun swiftc -swift-version 5 -O -parse-as-library -emit-library \
  -module-name GLaDOSPolicyEditor -target "$GLADOS_ARCH-apple-macos13.0" \
  -framework SwiftUI -framework AppKit \
  -o "$FRAMEWORKS/GLaDOSPolicyEditor.dylib" "$SCRIPT_DIR/PolicyEditor.swift"
xcrun clang -arch "$GLADOS_ARCH" -dynamiclib -fobjc-arc -framework AppKit \
  -o "$FRAMEWORKS/PolicyMenuPlugin.dylib" "$SCRIPT_DIR/PolicyMenuPlugin.m"

SAFARI_SOURCE="$SCRIPT_DIR/SafariExtensionSource"
SAFARI_TEMP="$BUILD_TMP/safari"
mkdir -p "$SAFARI_TEMP"
xcrun safari-web-extension-converter "$SAFARI_SOURCE/Resources" \
  --project-location "$SAFARI_TEMP" --app-name 'GLaDOS Account Center' \
  --bundle-identifier com.enoch.glados-account-center \
  --swift --macos-only --copy-resources --no-open --no-prompt --force
SAFARI_PROJECT="$SAFARI_TEMP/GLaDOS Account Center"
cp "$SAFARI_SOURCE/SafariWebExtensionHandler.swift" \
  "$SAFARI_PROJECT/GLaDOS Account Center Extension/SafariWebExtensionHandler.swift"
xcodebuild -project "$SAFARI_PROJECT/GLaDOS Account Center.xcodeproj" \
  -target 'GLaDOS Account Center Extension' -configuration Release \
  SYMROOT="$SAFARI_TEMP/products" OBJROOT="$SAFARI_TEMP/obj" \
  CODE_SIGNING_ALLOWED=NO MACOSX_DEPLOYMENT_TARGET=13.0 \
  ARCHS="$GLADOS_ARCH" ONLY_ACTIVE_ARCH=YES \
  PRODUCT_BUNDLE_IDENTIFIER=com.enoch.glados-account-center.safari-bridge.extension \
  build
SAFARI_PRODUCT="$SAFARI_TEMP/products/Release/GLaDOS Account Center Extension.appex"
EMBEDDED_SAFARI="$PLUGINS/GLaDOS Safari Bridge Extension.appex"
/usr/bin/ditto "$SAFARI_PRODUCT" "$EMBEDDED_SAFARI"

# xcodebuild signing is disabled above, so apply the extension's capabilities
# explicitly. The native handler makes an outbound loopback TCP connection.
# These entitlements belong to the extension, not to the containing App.
SAFARI_ENTITLEMENTS="$BUILD_TMP/safari-entitlements.plist"
python3 - "$SAFARI_ENTITLEMENTS" <<'PY'
import plistlib, sys
from pathlib import Path
Path(sys.argv[1]).write_bytes(plistlib.dumps({
    'com.apple.security.app-sandbox':True,
    'com.apple.security.network.client':True,
}))
PY
/usr/bin/codesign --force --sign - --entitlements "$SAFARI_ENTITLEMENTS" --generate-entitlement-der "$EMBEDDED_SAFARI"
/usr/bin/codesign --force --sign - "$FRAMEWORKS/GLaDOSPolicyEditor.dylib"
/usr/bin/codesign --force --sign - "$FRAMEWORKS/PolicyMenuPlugin.dylib"
/usr/bin/codesign --force --sign - "$MACOS/GLaDOSAccountCenter.real"
# Do not recursively re-sign preserved helpers or other unchanged nested code.
/usr/bin/codesign --force --sign - "$STAGE_APP"
/usr/bin/codesign --verify --deep --strict "$STAGE_APP"
python3 - "$MACOS/RefreshSecretStore" "$BUILD_TMP/retained-helper.json" "$EMBEDDED_SAFARI" <<'PY'
import hashlib, json, plistlib, subprocess, sys
from pathlib import Path
# Apple TN3125: request XML instead of the default human-readable DER dump.
signed=subprocess.run(['/usr/bin/codesign', '-d', '--entitlements', '-', '--xml', sys.argv[3]],
                      check=True, capture_output=True)
entitlements=plistlib.loads(signed.stdout)
if any(entitlements.get(key) is not True for key in (
        'com.apple.security.app-sandbox', 'com.apple.security.network.client')):
    raise SystemExit('The Safari extension is missing its sandbox or loopback client capability.')
expected=json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))
if expected is not None:
    helper=Path(sys.argv[1])
    result=subprocess.run(['/usr/bin/codesign', '-d', '-r-', str(helper)],
                          check=True, capture_output=True, text=True)
    requirement=[line for line in (result.stdout+'\n'+result.stderr).splitlines()
                 if line.startswith('designated =>')]
    actual={'sha256':hashlib.sha256(helper.read_bytes()).hexdigest(),
            'designatedRequirement':requirement[0] if len(requirement) == 1 else None}
    if actual != expected:
        raise SystemExit('The retained Keychain helper changed during the build.')
PY
python3 "$PROJECT_DIR/macos/Tests/validate_package.py" --app "$STAGE_APP" --bundle-only

# Publish only a fully copied, verified bundle. A failed output copy never leaves
# a partial App at the requested output path or removes an existing output.
python3 - "$SCRIPT_DIR/install-manual.py" "$STAGE_APP" "$OUTPUT_APP" <<'PY'
import importlib.util, os, shutil, subprocess, sys, tempfile
from pathlib import Path
spec=importlib.util.spec_from_file_location('glados_manual_installer', sys.argv[1])
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
source=Path(sys.argv[2])
output=Path(sys.argv[3]).expanduser()
if os.path.lexists(output):
    raise SystemExit('Output already exists; choose a new staging path.')
output.parent.mkdir(parents=True, exist_ok=True)
output=output.parent.resolve(strict=True)/output.name
temporary=Path(tempfile.mkdtemp(prefix='.glados-manual-output-', dir=output.parent))
try:
    candidate=temporary/output.name
    subprocess.run(['/usr/bin/ditto', str(source), str(candidate)], check=True)
    if module.fingerprint(candidate) != module.fingerprint(source):
        raise SystemExit('Final App copy verification failed.')
    subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(candidate)], check=True)
    if os.path.lexists(output):
        raise SystemExit('Output appeared during the build; it was not replaced.')
    candidate.rename(output)
finally:
    shutil.rmtree(temporary)
PY
echo "Built manual-login Account Center: $OUTPUT_APP"
