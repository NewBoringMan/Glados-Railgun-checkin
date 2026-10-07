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
GLADOS_PREBUILT_APP="${GLADOS_PREBUILT_APP:-}"
GLADOS_PREBUILT_SHA256="${GLADOS_PREBUILT_SHA256:-}"
case "$GLADOS_ARCH" in arm64|x86_64) ;; *) echo 'Unsupported architecture' >&2; exit 2;; esac
if [[ -n "$GLADOS_PREBUILT_APP" || -n "$GLADOS_PREBUILT_SHA256" ]]; then
  if [[ -z "$GLADOS_PREBUILT_APP" || -z "$GLADOS_PREBUILT_SHA256" || -z "$SOURCE_APP" ]]; then
    echo 'Prebuilt mode requires GLADOS_PREBUILT_APP, GLADOS_PREBUILT_SHA256 and an inspected existing App.' >&2
    exit 2
  fi
else
  # Fail before copying the installed App when the Safari compiler is unavailable.
  /usr/bin/xcodebuild -version >/dev/null
fi
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

if [[ -n "$GLADOS_PREBUILT_APP" ]]; then
  # Validate the complete CI artifact before any of its compiled code is copied.
  # Local resources must match that artifact; a version number alone is not enough.
  GLADOS_PREBUILT_APP="$(python3 - "$SCRIPT_DIR/install-manual.py" "$GLADOS_PREBUILT_APP" "$GLADOS_PREBUILT_SHA256" "$SOURCE_APP" "$OUTPUT_APP" "${TMPDIR:-/private/tmp}" "$PROJECT_DIR" "$GLADOS_ARCH" <<'PY'
import importlib.util, plistlib, re, subprocess, sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('glados_manual_installer', sys.argv[1])
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
prebuilt=module.app_path(sys.argv[2])
expected=sys.argv[3]
source=module.app_path(sys.argv[4])
project=Path(sys.argv[7])
if not re.fullmatch(r'[a-f0-9]{64}', expected):
    raise SystemExit('The prebuilt fingerprint must be a lowercase SHA-256 package fingerprint.')
if any(path.is_symlink() for path in prebuilt.rglob('*')):
    raise SystemExit('The prebuilt App must not contain symlinks.')
if '\n' in str(prebuilt) or '\r' in str(prebuilt):
    raise SystemExit('The prebuilt App path contains an unsupported newline.')
if prebuilt == source or prebuilt in source.parents or source in prebuilt.parents:
    raise SystemExit('The prebuilt and inspected Apps must be separate.')
output=Path(sys.argv[5]).expanduser().absolute()
for path in (output, *output.parents):
    if path.is_symlink():
        raise SystemExit('The output write path must not contain symlinks.')
for raw in (sys.argv[5], sys.argv[6]):
    destination=Path(raw).expanduser().resolve()
    if destination == prebuilt or prebuilt in destination.parents:
        raise SystemExit('Build output and temporary files must be outside the prebuilt App.')
if module.fingerprint(prebuilt) != expected:
    raise SystemExit('The prebuilt App fingerprint does not match the verified artifact.')
fresh=module.plist(project/'macos/Info.plist')
info=module.plist(prebuilt/'Contents/Info.plist')
for key in ('CFBundleIdentifier', 'CFBundleExecutable', 'CFBundleShortVersionString', 'CFBundleVersion'):
    if info.get(key) != fresh.get(key):
        raise SystemExit('The prebuilt App identity or version does not match this source.')
subprocess.run([sys.executable, str(project/'macos/Tests/validate_package.py'), '--app', str(prebuilt), '--bundle-only'], check=True, capture_output=True)
subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(prebuilt)], check=True, capture_output=True)
extension=prebuilt/'Contents/PlugIns/GLaDOS Safari Bridge Extension.appex'
signed=subprocess.run(['/usr/bin/codesign', '-d', '--entitlements', '-', '--xml', str(extension)], check=True, capture_output=True)
entitlements=plistlib.loads(signed.stdout)
if any(entitlements.get(key) is not True for key in ('com.apple.security.app-sandbox', 'com.apple.security.network.client')):
    raise SystemExit('The prebuilt Safari extension is missing required entitlements.')
extension_executable=module.plist(extension/'Contents/Info.plist').get('CFBundleExecutable')
if not isinstance(extension_executable, str) or Path(extension_executable).name != extension_executable:
    raise SystemExit('The prebuilt Safari executable is invalid.')
executables=[prebuilt/name for name in (
    'Contents/MacOS/GLaDOSAccountCenter', 'Contents/MacOS/GLaDOSAccountCenter.real',
    'Contents/Frameworks/GLaDOSPolicyEditor.dylib', 'Contents/Frameworks/PolicyMenuPlugin.dylib',
    'Contents/Frameworks/GLaDOSNotifications.dylib')]
executables.append(extension/'Contents/MacOS'/extension_executable)
for executable in executables:
    subprocess.run(['/usr/bin/lipo', '-verify_arch', sys.argv[8], str(executable)], check=True, capture_output=True)
resources=list((project/'macos/Resources').glob('*.js'))+[project/'macos/Resources/AppIcon.icns', project/'app_integration/checkin_watch.py']
for resource in resources:
    if resource.is_symlink() or not resource.is_file() or resource.read_bytes() != (prebuilt/'Contents/Resources'/resource.name).read_bytes():
        raise SystemExit('A local resource differs from the verified prebuilt App: '+resource.name)
if module.fingerprint(prebuilt) != expected:
    raise SystemExit('The prebuilt App changed during validation.')
print(prebuilt)
PY
)"
fi

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
python3 - "$SCRIPT_DIR/install-manual.py" "$SOURCE_APP" "$STAGE_APP" "$BUILD_TMP/origin.json" "$PROJECT_DIR/macos/Info.plist" "$GLADOS_PREBUILT_SHA256" <<'PY'
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
origin={
    'schema':'glados.manual-build-origin', 'version':1,
    'sourceFingerprint':source_fingerprint,
}
if sys.argv[6]:
    origin['prebuiltFingerprint']=sys.argv[6]
Path(sys.argv[4]).write_text(json.dumps(origin, indent=2)+'\n', encoding='utf-8')
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
             'Contents/Frameworks/GLaDOSNotifications.dylib',
             'Contents/Resources/checkin_watch.py',
             'Contents/Resources/AppIcon.icns', 'Contents/Resources/manual-build-origin.json',
             'Contents/_CodeSignature/CodeResources', 'Contents/MacOS/RefreshSecretStore'):
    writable_path(name)
for resource in Path(sys.argv[3]).glob('*.js'):
    writable_path('Contents/Resources/'+resource.name)

# Only the rebuilt nested code will be signed below. Retain the helper's bytes
# and designated requirement so existing Keychain ACL references stay intact.
retained={}
for name in ('Contents/MacOS/RefreshSecretStore', 'Contents/Frameworks/GLaDOSNotifications.dylib'):
    helper=app/name
    if not helper.exists():
        continue
    subprocess.run(['/usr/bin/codesign', '--verify', '--strict', str(helper)], check=True)
    result=subprocess.run(['/usr/bin/codesign', '-d', '-r-', str(helper)],
                          check=True, capture_output=True, text=True)
    requirement=[line for line in (result.stdout+'\n'+result.stderr).splitlines()
                 if line.startswith(('designated =>', '# designated =>'))]
    if len(requirement) != 1:
        raise SystemExit('Could not record a retained helper signature.')
    retained[name]={'sha256':hashlib.sha256(helper.read_bytes()).hexdigest(),
                    'designatedRequirement':requirement[0]}
Path(sys.argv[5]).write_text(json.dumps(retained)+'\n', encoding='utf-8')

for name in ('Contents/MacOS', 'Contents/Frameworks', 'Contents/Resources', 'Contents/PlugIns'):
    (app/name).mkdir(parents=True, exist_ok=True)
for name in (
    'Contents/Resources/LoginRefresh',
    'Contents/Frameworks/GLaDOSRefreshCenter.dylib',
    'Contents/Frameworks/GLaDOSLocalMail.dylib',
    'Contents/Resources/edge_login_support.js',
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
cp "$SCRIPT_DIR/checkin_watch.py" "$RESOURCES/checkin_watch.py"

EMBEDDED_SAFARI="$PLUGINS/GLaDOS Safari Bridge Extension.appex"
if [[ -n "$GLADOS_PREBUILT_APP" ]]; then
  python3 - "$SCRIPT_DIR/install-manual.py" "$GLADOS_PREBUILT_APP" "$GLADOS_PREBUILT_SHA256" "$STAGE_APP" "$PROJECT_DIR" <<'PY'
import importlib.util, shutil, sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('glados_manual_installer', sys.argv[1])
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
prebuilt=module.app_path(sys.argv[2])
app=Path(sys.argv[4]).resolve(strict=True)
if module.fingerprint(prebuilt) != sys.argv[3]:
    raise SystemExit('The prebuilt App changed before copying.')
names=[
    'Contents/MacOS/GLaDOSAccountCenter', 'Contents/MacOS/GLaDOSAccountCenter.real',
    'Contents/Frameworks/GLaDOSPolicyEditor.dylib', 'Contents/Frameworks/PolicyMenuPlugin.dylib',
    'Contents/PlugIns/GLaDOS Safari Bridge Extension.appex',
]
# Never replace either retained helper. Only a genuinely absent notification
# library receives the validated CI fallback, matching the source-build behavior.
notifications='Contents/Frameworks/GLaDOSNotifications.dylib'
if not module.present(app/notifications):
    names.append(notifications)
for name in names:
    source=prebuilt/name
    target=app/name
    for path in (target, *target.parents):
        if path == app:
            break
        if path.is_symlink():
            raise SystemExit('Unsafe symlink in a prebuilt copy destination: '+name)
    if app not in target.resolve().parents:
        raise SystemExit('The prebuilt copy destination escapes the staging App.')
    if source.is_dir():
        if module.present(target):
            raise SystemExit('The extension copy destination is already occupied.')
        shutil.copytree(source, target)
        if module.fingerprint(source) != module.fingerprint(target):
            raise SystemExit('The copied Safari extension differs from the prebuilt App.')
    else:
        if source.is_symlink() or not source.is_file() or (target.exists() and not target.is_file()):
            raise SystemExit('Invalid compiled component in the prebuilt copy.')
        shutil.copy2(source, target)
        if source.read_bytes() != target.read_bytes() or (source.stat().st_mode & 0o777) != (target.stat().st_mode & 0o777):
            raise SystemExit('A copied compiled component differs from the prebuilt App.')
for resource in (Path(sys.argv[5])/'macos/Resources').glob('*.js'):
    # Check precisely the local build's resources, including any files added
    # since preflight; unrelated resources inherited from the old App stay intact.
    candidate=prebuilt/'Contents/Resources'/resource.name
    staged=app/'Contents/Resources'/resource.name
    if not candidate.is_file() or not staged.is_file() or staged.read_bytes() != candidate.read_bytes():
        raise SystemExit('A staged JavaScript resource differs from the prebuilt App: '+resource.name)
for name in ('AppIcon.icns', 'checkin_watch.py'):
    if (app/'Contents/Resources'/name).read_bytes() != (prebuilt/'Contents/Resources'/name).read_bytes():
        raise SystemExit('A staged resource differs from the prebuilt App: '+name)
if module.fingerprint(prebuilt) != sys.argv[3]:
    raise SystemExit('The prebuilt App changed during copying.')
PY
else
# The installed notification library is retained byte-for-byte. A source-only
# CI build compiles the recovered notification ABI for packaging validation.
if [[ ! -e "$FRAMEWORKS/GLaDOSNotifications.dylib" ]]; then
  xcrun swiftc -swift-version 5 -O -parse-as-library -emit-library \
    -D REFRESH_NOTIFICATION_LIBRARY -module-name GLaDOSNotifications \
    -target "$GLADOS_ARCH-apple-macos13.0" -framework Foundation -framework UserNotifications \
    -o "$FRAMEWORKS/GLaDOSNotifications.dylib" "$SCRIPT_DIR/RefreshNotifications.swift"
  /usr/bin/codesign --force --sign - "$FRAMEWORKS/GLaDOSNotifications.dylib"
fi

xcrun swiftc -swift-version 5 -O -parse-as-library \
  -target "$GLADOS_ARCH-apple-macos13.0" \
  -framework SwiftUI -framework AppKit -framework Security -framework CryptoKit \
  -lsqlite3 -o "$MACOS/GLaDOSAccountCenter.real" \
  "$PROJECT_DIR/macos/Sources/"*.swift
xcrun clang -arch "$GLADOS_ARCH" -o "$MACOS/GLaDOSAccountCenter" \
  "$SCRIPT_DIR/launcher.c" "$SCRIPT_DIR/browser_process_info.c" -lproc
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
/usr/bin/ditto "$SAFARI_PRODUCT" "$EMBEDDED_SAFARI"
fi

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
python3 - "$STAGE_APP" "$BUILD_TMP/retained-helper.json" "$EMBEDDED_SAFARI" <<'PY'
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
for name, signature in expected.items():
    helper=Path(sys.argv[1])/name
    result=subprocess.run(['/usr/bin/codesign', '-d', '-r-', str(helper)],
                          check=True, capture_output=True, text=True)
    requirement=[line for line in (result.stdout+'\n'+result.stderr).splitlines()
                 if line.startswith(('designated =>', '# designated =>'))]
    actual={'sha256':hashlib.sha256(helper.read_bytes()).hexdigest(),
            'designatedRequirement':requirement[0] if len(requirement) == 1 else None}
    if actual != signature:
        raise SystemExit('A retained helper changed during the build: '+name)
PY
python3 "$PROJECT_DIR/macos/Tests/validate_package.py" --app "$STAGE_APP" --bundle-only

# Publish only a fully copied, verified bundle. A failed output copy never leaves
# a partial App at the requested output path or removes an existing output.
python3 - "$SCRIPT_DIR/install-manual.py" "$STAGE_APP" "$OUTPUT_APP" "$GLADOS_PREBUILT_APP" <<'PY'
import importlib.util, os, shutil, subprocess, sys, tempfile
from pathlib import Path
spec=importlib.util.spec_from_file_location('glados_manual_installer', sys.argv[1])
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
source=Path(sys.argv[2])
output=Path(sys.argv[3]).expanduser()
if os.path.lexists(output):
    raise SystemExit('Output already exists; choose a new staging path.')
if sys.argv[4]:
    absolute=output.absolute()
    if any(path.is_symlink() for path in (absolute, *absolute.parents)):
        raise SystemExit('The output write path changed to a symlink.')
    prebuilt=module.app_path(sys.argv[4])
    if output.resolve() == prebuilt or prebuilt in output.resolve().parents:
        raise SystemExit('Final output must remain outside the prebuilt App.')
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
