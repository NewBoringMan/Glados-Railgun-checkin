"""Compile and exercise the internal native SecretStore using synthetic data only.

No installation, UI, network, real tokens, or changes to the production service.
All build files use one temporary directory removed before returning.
"""
from __future__ import annotations
import json
from pathlib import Path
import platform
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    if platform.system() != 'Darwin':
        print(json.dumps({'status':'NOT_RUN', 'reason':'macOS_required'}))
        return 2
    with tempfile.TemporaryDirectory(prefix='glados-secretstore-build-') as directory:
        root = Path(directory)
        target = root / 'RefreshSecretStore'
        command = ['/usr/bin/xcrun', 'swiftc', '-parse-as-library', '-DREFRESH_SECRET_CLI',
                   '-module-cache-path', str(root / 'ModuleCache'),
                   str(ROOT / 'app_integration/RefreshSecretStore.swift'),
                   '-framework', 'Security', '-framework', 'LocalAuthentication',
                   '-o', str(target)]
        compiled = subprocess.run(command, capture_output=True, text=True, timeout=120)
        if compiled.returncode:
            print(json.dumps({'status':'FAIL', 'phase':'compile', 'diagnostic':compiled.stderr[-5000:]}))
            return 1
        subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', str(target)],
                       capture_output=True, check=True, timeout=20)
        subprocess.run(['/usr/bin/codesign', '--verify', '--strict', str(target)],
                       capture_output=True, check=True, timeout=20)
        result = subprocess.run([str(target), '--self-test'], capture_output=True, text=True, timeout=15)
        data = json.loads(result.stdout)
        if data.get('ok') is not True:
            print(json.dumps({'status':'BLOCKED', 'phase':'noninteractive_keychain', 'detail':data}))
            return 1
        # Malformed commands may never turn into a general-purpose Keychain reader.
        for request in [{'op':'get','key':'unrelated-app'}, {'op':'list','key':'gmail-token'},
                        {'op':'get','key':'gmail-token','service':'com.apple.account'}]:
            r = subprocess.run([str(target)], input=json.dumps(request), capture_output=True,
                               text=True, timeout=10)
            assert json.loads(r.stdout).get('ok') is False
        print(json.dumps({'status':'PASS', 'native_compile':True, 'codesign_verified':True,
                          'synthetic_keychain_roundtrip':True, 'synthetic_item_removed':True,
                          'rejected_out_of_scope_requests':3, 'production_credentials_touched':False,
                          'background_ui_allowed':False}))
    print(json.dumps({'temporary_build_removed': not Path(directory).exists()}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
