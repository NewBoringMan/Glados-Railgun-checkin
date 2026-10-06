"""Offline source and single-App packaging checks. Never opens a GUI or logs in."""
from __future__ import annotations

import argparse
import json
import plistlib
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.parent
APP_ID = "com.enoch.glados-account-center"


def run(*args: str) -> None:
    subprocess.run(args, cwd=PROJECT, check=True)


def read_plist(path: Path) -> dict:
    with path.open("rb") as stream:
        return plistlib.load(stream)


def validate_source() -> None:
    sources = sorted((ROOT / "Sources").glob("*.swift"))
    assert sources and (ROOT / "Sources/ManualAccountStore.swift").exists()
    if shutil.which("swiftc"):
        run("swiftc", "-frontend", "-parse", *map(str, sources))
    for source in sorted((ROOT / "Resources").glob("*.js")):
        run("node", "--check", str(source))
    run("node", "--test", *map(str, sorted((ROOT / "Tests").glob("*.test.js"))))
    info = read_plist(ROOT / "Info.plist")
    assert info["CFBundleIdentifier"] == APP_ID
    assert info["CFBundleShortVersionString"] == "2.0.12"
    assert info["CFBundleVersion"] == "20044"
    safari = PROJECT / "app_integration/SafariExtensionSource"
    manifest = json.loads((safari / "Resources/manifest.json").read_text())
    assert {"nativeMessaging", "cookies"}.issubset(manifest["permissions"])
    assert "<all_urls>" not in manifest.get("host_permissions", [])
    assert read_plist(safari / "Info.plist")["NSExtension"]["NSExtensionPointIdentifier"] == "com.apple.Safari.web-extension"
    capture = (ROOT / "Resources/capture_account.js").read_text()
    assert "SAFARI_COMPANION_APP" not in capture
    assert "SAFARI_BUILD_DIR" not in capture
    assert "GLaDOS Safari Bridge Extension.appex" in capture
    launcher = (PROJECT / "app_integration/launcher.c").read_text()
    assert "PolicyMenuPlugin.dylib" in launcher
    assert "GLaDOSRefreshCenter" not in launcher
    assert "GLaDOSRunNotificationCLI" in launcher and "--checkin-watch" in launcher
    compile((PROJECT / "app_integration/checkin_watch.py").read_text(), "checkin_watch.py", "exec")
    plugin = (PROJECT / "app_integration/PolicyMenuPlugin.m").read_text()
    assert "GLaDOSShowPolicyEditor" in plugin
    assert "GLaDOSRefreshCenter" not in plugin
    print("SOURCE_PACKAGE_VALIDATION_OK")


def validate_bundle(app: Path) -> None:
    contents = app / "Contents"
    info = read_plist(contents / "Info.plist")
    assert info["CFBundleIdentifier"] == APP_ID
    assert info["CFBundleExecutable"] == "GLaDOSAccountCenter"
    assert info["CFBundleVersion"] == "20044"
    assert info["CFBundleShortVersionString"] == "2.0.12"
    assert "GLaDOSRefreshPython" not in info
    origin = json.loads((contents / "Resources/manual-build-origin.json").read_text())
    assert origin.get("schema") == "glados.manual-build-origin" and type(origin.get("version")) is int and origin["version"] == 1
    source_hash = origin.get("sourceFingerprint")
    assert source_hash is None or (isinstance(source_hash, str) and re.fullmatch(r"[a-f0-9]{64}", source_hash))
    for name in (
        "MacOS/GLaDOSAccountCenter", "MacOS/GLaDOSAccountCenter.real",
        "Frameworks/PolicyMenuPlugin.dylib", "Frameworks/GLaDOSPolicyEditor.dylib",
        "Frameworks/GLaDOSNotifications.dylib", "Resources/checkin_watch.py",
        "Resources/core.js", "Resources/capture_account.js", "Resources/AppIcon.icns",
        "Resources/browser_support.js", "Resources/safari_native_protocol.js",
        "Resources/firefox_bidi_support.js",
        "Resources/safari_native_bridge_server.js",
    ):
        path = contents / name
        assert path.is_file() and path.stat().st_size > 0, f"Required App resource missing: {name}"
    extension = contents / "PlugIns/GLaDOS Safari Bridge Extension.appex"
    extension_info = read_plist(extension / "Contents/Info.plist")
    assert extension_info["CFBundleIdentifier"] == APP_ID + ".safari-bridge.extension"
    assert extension_info["NSExtension"]["NSExtensionPointIdentifier"] == "com.apple.Safari.web-extension"
    assert not list(contents.rglob("*.app")), "A second App must not be embedded"
    for name in (
        "Resources/LoginRefresh", "Frameworks/GLaDOSRefreshCenter.dylib",
        "Frameworks/GLaDOSLocalMail.dylib", "Resources/edge_login_support.js",
        "MacOS/RefreshNotifications", "MacOS/LocalMailReader",
    ):
        assert not (contents / name).exists() and not (contents / name).is_symlink(), f"Automatic-login component remains: {name}"
    print("SINGLE_APP_BUNDLE_VALIDATION_OK")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", type=Path)
    parser.add_argument("--bundle-only", action="store_true")
    args = parser.parse_args()
    if not args.bundle_only:
        validate_source()
    if args.app:
        validate_bundle(args.app)
    elif args.bundle_only:
        parser.error("--bundle-only requires --app")


if __name__ == "__main__":
    main()
