"""Build a same-app maintenance update from the user's existing Account Center.

Preserve its original business executable, browser scripts and Safari extension.
No application is launched, no credentials are accessed, no schedule is activated.
Build outputs live under build/.noindex; installation is a separately requested step.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_ID = 'com.enoch.glados-account-center'
VERSION = '2.0.10'
BUILD = '20014'
SOURCE = Path.home() / 'Applications/GLaDOS Account Center.app'
OUT_ROOT = ROOT / 'build/maintenance.noindex'
PRODUCT = OUT_ROOT / 'GLaDOS Account Center.app'


def run(args, *, timeout=180):
    result = subprocess.run([str(a) for a in args], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=timeout)
    if result.returncode:
        raise RuntimeError('Build command failed: ' + str(args[0]) + '\n' +
                           result.stderr.decode('utf-8', 'replace')[-7000:])
    return result.stdout


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest(path):
    return {str(p.relative_to(path)): sha(p) for p in path.rglob('*') if p.is_file() and not p.is_symlink()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, default=SOURCE)
    args = parser.parse_args()
    source = args.source.expanduser().resolve(strict=True)
    if source.suffix != '.app' or source == PRODUCT:
        raise RuntimeError('Existing .app source required')
    info = plistlib.loads((source / 'Contents/Info.plist').read_bytes())
    if info.get('CFBundleIdentifier') != EXPECTED_ID:
        raise RuntimeError('Unexpected source bundle identity')
    run(['/usr/bin/codesign', '--verify', '--deep', '--strict', source])
    original = source / 'Contents/MacOS/GLaDOSAccountCenter.real'
    if not original.is_file():
        raise RuntimeError('Expected existing original core executable is missing')
    original_sha = sha(original)
    baseline_resources = manifest(source / 'Contents/Resources')
    baseline_plugins = manifest(source / 'Contents/PlugIns')
    python = Path('/opt/homebrew/bin/python3')
    if not python.is_file():
        raise RuntimeError('Existing Python runtime unavailable; no new runtime installed')
    run([python, '-I', '-B', '-c', 'import sqlite3,ssl,urllib.request,fcntl'])
    if OUT_ROOT.exists():
        raise RuntimeError('Build output already exists; inspect and clean it before rebuilding')
    OUT_ROOT.mkdir(parents=True)
    cache = OUT_ROOT / 'ModuleCache'
    try:
        run(['/usr/bin/ditto', source, PRODUCT])
        if any((PRODUCT / 'Contents').rglob('*.app')):
            raise RuntimeError('Nested application bundle detected; source requires review')
        frameworks = PRODUCT / 'Contents/Frameworks'
        macos = PRODUCT / 'Contents/MacOS'
        resources = PRODUCT / 'Contents/Resources/LoginRefresh'
        frameworks.mkdir(exist_ok=True)
        resources.mkdir(exist_ok=False)
        for path in ROOT.glob('login_refresh_*.py'):
            shutil.copy2(path, resources / path.name)
        sources = ROOT / 'app_integration'
        common = ['/usr/bin/xcrun', 'swiftc', '-parse-as-library', '-target',
                  'arm64-apple-macos13.0', '-module-cache-path', cache]
        run([*common, '-emit-library', '-module-name', 'GLaDOSPolicyEditor',
             '-framework', 'SwiftUI', '-framework', 'AppKit', '-lsqlite3',
             sources / 'PolicyEditor.swift', sources / 'AccountEmailDirectory.swift',
             '-o', frameworks / 'GLaDOSPolicyEditor.dylib'])
        run([*common, '-emit-library', '-module-name', 'GLaDOSRefreshCenter',
             '-framework', 'SwiftUI', '-framework', 'AppKit',
             sources / 'RefreshCenter.swift', '-o', frameworks / 'GLaDOSRefreshCenter.dylib'])
        run(['/usr/bin/xcrun', 'clang', '-arch', 'arm64', '-dynamiclib', '-fobjc-arc',
             '-framework', 'AppKit', sources / 'PolicyMenuPlugin.m',
             '-o', frameworks / 'PolicyMenuPlugin.dylib'])
        run([*common, '-DREFRESH_SECRET_CLI', '-framework', 'Security', '-framework',
             'LocalAuthentication', sources / 'RefreshSecretStore.swift',
             '-o', macos / 'RefreshSecretStore'])
        updated_info = dict(info)
        updated_info.update(CFBundleShortVersionString=VERSION, CFBundleVersion=BUILD,
                            GLaDOSRefreshPython=str(python))
        (PRODUCT / 'Contents/Info.plist').write_bytes(plistlib.dumps(updated_info))
        for name in ['GLaDOSPolicyEditor.dylib', 'GLaDOSRefreshCenter.dylib', 'PolicyMenuPlugin.dylib']:
            run(['/usr/bin/codesign', '--force', '--sign', '-', frameworks / name])
        run(['/usr/bin/codesign', '--force', '--sign', '-', '--identifier',
             EXPECTED_ID + '.refresh-secret-store', macos / 'RefreshSecretStore'])
        run(['/usr/bin/codesign', '--force', '--sign', '-', PRODUCT])
        run(['/usr/bin/codesign', '--verify', '--deep', '--strict', PRODUCT])
        if sha(PRODUCT / 'Contents/MacOS/GLaDOSAccountCenter.real') != original_sha:
            raise RuntimeError('Original business core was unexpectedly altered')
        current_resources = manifest(PRODUCT / 'Contents/Resources')
        if any(current_resources.get(name) != digest for name, digest in baseline_resources.items()):
            raise RuntimeError('Existing browser/resource logic unexpectedly changed')
        if manifest(PRODUCT / 'Contents/PlugIns') != baseline_plugins:
            raise RuntimeError('Existing Safari extension unexpectedly changed')
        symbols = run(['/usr/bin/nm', '-gU', frameworks / 'GLaDOSRefreshCenter.dylib']).decode()
        if '_GLaDOSShowRefreshCenter' not in symbols:
            raise RuntimeError('Maintenance UI entry point not linked')
        report = dict(version=VERSION, build=BUILD, source=str(source), product=str(PRODUCT),
                      source_version=info.get('CFBundleShortVersionString'),
                      source_build=info.get('CFBundleVersion'), original_core_sha256=original_sha,
                      original_core_unchanged=True, original_resources_unchanged=True,
                      original_safari_extension_unchanged=True, signature_verified=True,
                      nested_apps=0, schedule_activated=False, installed=False,
                      python_modules=sorted(p.name for p in resources.glob('*.py')))
        (OUT_ROOT / 'build-verification.json').write_text(json.dumps(report, indent=2)+'\n')
        print(json.dumps(report))
    finally:
        if cache.exists():
            shutil.rmtree(cache)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'ok':False, 'reason':str(exc)}))
        raise SystemExit(1)
