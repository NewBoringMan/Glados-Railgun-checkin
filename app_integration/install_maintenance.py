"""Install the verified same-app build only while the existing app is closed.

No launch, quit, GUI action, credentials, system settings or production Git changes.
One compressed original bundle is retained until post-install acceptance is complete.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
from datetime import datetime, timezone

from build_maintenance import SOURCE, PRODUCT, OUT_ROOT, EXPECTED_ID, sha, manifest, run


def assert_closed():
    output = run(['/bin/ps', '-axo', 'comm=']).decode('utf-8', 'replace')
    target = str(SOURCE / 'Contents/MacOS') + '/'
    if any(line.strip().startswith(target) for line in output.splitlines()):
        raise RuntimeError('Account Center is running; no files replaced')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--confirm-replace', action='store_true')
    args = parser.parse_args()
    if not args.confirm_replace:
        raise RuntimeError('Explicit same-app replacement flag required')
    assert_closed()
    if SOURCE.is_symlink() or not SOURCE.is_dir() or not PRODUCT.is_dir():
        raise RuntimeError('Expected physical existing app and build product required')
    report = json.loads((OUT_ROOT / 'build-verification.json').read_text())
    if report.get('source') != str(SOURCE.resolve()) or report.get('product') != str(PRODUCT):
        raise RuntimeError('Build report targets do not match the single intended app')
    for app in (SOURCE, PRODUCT):
        run(['/usr/bin/codesign', '--verify', '--deep', '--strict', app])
    installed_info = plistlib.loads((SOURCE / 'Contents/Info.plist').read_bytes())
    build_info = plistlib.loads((PRODUCT / 'Contents/Info.plist').read_bytes())
    if installed_info.get('CFBundleIdentifier') != EXPECTED_ID or build_info.get('CFBundleIdentifier') != EXPECTED_ID:
        raise RuntimeError('Bundle identity mismatch')
    if installed_info.get('CFBundleVersion') != report.get('source_build'):
        raise RuntimeError('Installed app changed since build; review before replacement')
    if sha(SOURCE / 'Contents/MacOS/GLaDOSAccountCenter.real') != report['original_core_sha256']:
        raise RuntimeError('Business core changed since build')
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backups = OUT_ROOT.parent / 'rollback'
    backups.mkdir(exist_ok=True)
    archive = backups / ('GLaDOS-Account-Center-' + str(report['source_build']) + '-' + timestamp + '.zip')
    # Hidden .noindex preparation on the same filesystem enables an atomic rename.
    stage = SOURCE.parent / '.GLaDOS-maintenance-install.noindex'
    candidate = stage / 'candidate.app'
    previous = stage / 'previous.bundle'
    if stage.exists():
        raise RuntimeError('An existing installation transaction requires review')
    stage.mkdir(mode=0o700)
    replaced = False
    baseline = manifest(SOURCE)
    try:
        run(['/usr/bin/ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', SOURCE, archive])
        run(['/usr/bin/ditto', PRODUCT, candidate])
        run(['/usr/bin/codesign', '--verify', '--deep', '--strict', candidate])
        if manifest(candidate) != manifest(PRODUCT):
            raise RuntimeError('Staged product does not match verified build')
        assert_closed()
        if manifest(SOURCE) != baseline:
            raise RuntimeError('Installed bundle changed during staging')
        SOURCE.rename(previous)
        try:
            candidate.rename(SOURCE)
            run(['/usr/bin/codesign', '--verify', '--deep', '--strict', SOURCE])
            if sha(SOURCE / 'Contents/MacOS/GLaDOSAccountCenter.real') != report['original_core_sha256']:
                raise RuntimeError('Installed original core mismatch')
            replaced = True
        except BaseException:
            if SOURCE.exists():
                SOURCE.rename(candidate)
            previous.rename(SOURCE)
            raise
        shutil.rmtree(previous)
        result = {'installed': True, 'path': str(SOURCE),
                  'version': build_info['CFBundleShortVersionString'],
                  'build': build_info['CFBundleVersion'], 'signature_verified': True,
                  'original_business_core_unchanged': True, 'nested_apps': 0,
                  'app_launched': False, 'schedule_activated': False,
                  'rollback_archive': str(archive)}
        (OUT_ROOT / 'install-verification.json').write_text(json.dumps(result, indent=2)+'\n')
        print(json.dumps(result))
    finally:
        # A failed transaction with an un-restored original must remain recoverable.
        if stage.exists() and not previous.exists():
            shutil.rmtree(stage)
        if not replaced and archive.exists() and SOURCE.exists() and manifest(SOURCE) == baseline:
            archive.unlink()


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'installed': False, 'reason': str(exc)}))
        raise SystemExit(1)
