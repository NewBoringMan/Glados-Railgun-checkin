"""Inspect and replace the existing Account Center without any GUI automation.

Install requires the inspected App's fingerprint and a candidate built over that
same App. Only the obsolete login-refresh job is stopped. All local account data,
browser profiles and Keychain records remain untouched.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

APP_ID = "com.enoch.glados-account-center"
JOB = APP_ID + ".login-refresh"
AUTO_COMPONENTS = (
    "Contents/Resources/LoginRefresh",
    "Contents/Frameworks/GLaDOSRefreshCenter.dylib",
    "Contents/MacOS/RefreshNotifications",
    "Contents/MacOS/LocalMailReader",
)


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(args, check=check, text=True, capture_output=True)


def plist(path: Path) -> dict:
    with path.open("rb") as stream:
        value = plistlib.load(stream)
    if not isinstance(value, dict):
        raise ValueError("Invalid property list")
    return value


def app_path(value: str) -> Path:
    app = Path(value).expanduser().resolve(strict=True)
    if not app.is_dir() or app.suffix != ".app" or plist(app / "Contents/Info.plist").get("CFBundleIdentifier") != APP_ID:
        raise ValueError("The selected App does not have the expected bundle identity")
    return app


def fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        digest.update(path.relative_to(root).as_posix().encode() + b"\0")
        if path.is_symlink():
            digest.update(b"link\0" + os.readlink(path).encode())
        elif path.is_file():
            digest.update(b"file\0" + oct(path.stat().st_mode & 0o777).encode() + b"\0")
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
        else:
            digest.update(b"directory\0")
    return digest.hexdigest()


def app_processes(app: Path, *, include_workers: bool = True) -> list[int]:
    result = run("/bin/ps", "-axo", "pid=,command=")
    found = []
    executable = str(app / "Contents/MacOS") + "/"
    refresh = str(app / "Contents/Resources/LoginRefresh") + "/"
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2 or not parts[0].isdigit():
            continue
        if executable in parts[1] or (include_workers and refresh in parts[1]):
            pid = int(parts[0])
            if pid != os.getpid():
                found.append(pid)
    return found


def inspect(app: Path) -> dict:
    info = plist(app / "Contents/Info.plist")
    support = Path.home() / "Library/Application Support/GLaDOS Account Center"
    launch_plist = Path.home() / "Library/LaunchAgents" / f"{JOB}.plist"
    return {
        "physical_app_path": str(app), "bundle_id": info["CFBundleIdentifier"],
        "version": info.get("CFBundleShortVersionString"), "build": info.get("CFBundleVersion"),
        "fingerprint": fingerprint(app), "running_pids": app_processes(app),
        "automatic_components": [name for name in AUTO_COMPONENTS if (app / name).exists()],
        "login_refresh_launch_plist_present": launch_plist.exists(),
        "legacy_identity_database_present": (support / "login-refresh.sqlite").exists(),
        "manual_account_directory_present": (support / "ManualAccounts/accounts.json").exists(),
        "browser_profiles_present": (support / "BrowserProfiles").exists(),
    }


def disable_old_job(backup: Path) -> None:
    launch_plist = Path.home() / "Library/LaunchAgents" / f"{JOB}.plist"
    target = f"gui/{os.getuid()}/{JOB}"
    if launch_plist.exists():
        if launch_plist.is_symlink() or plist(launch_plist).get("Label") != JOB:
            raise ValueError("Unexpected login-refresh launch plist; no files changed")
        shutil.copy2(launch_plist, backup / launch_plist.name)
    present = run("/bin/launchctl", "print", target, check=False)
    if present.returncode == 0:
        run("/bin/launchctl", "bootout", target)
        if run("/bin/launchctl", "print", target, check=False).returncode == 0:
            raise RuntimeError("The old login-refresh job is still running")
    elif not any(text in (present.stderr + present.stdout).lower() for text in ("could not find service", "no such process", "service not found")):
        raise RuntimeError("Could not determine the old login-refresh job state")
    # Persist the user's decision even if an old App is restored later.
    run("/bin/launchctl", "disable", target)
    if launch_plist.exists():
        launch_plist.unlink()


def install(app: Path, candidate: Path, expected: str, backup_root: Path) -> dict:
    if candidate == app or app in candidate.parents or candidate in app.parents:
        raise ValueError("Candidate and installed App must be separate")
    if fingerprint(app) != expected:
        raise ValueError("Installed App changed since inspection; inspect it again before installing")
    if app_processes(app, include_workers=False):
        raise ValueError("Account Center is open; close it through the approved Mac GUI before installation")
    candidate_info = plist(candidate / "Contents/Info.plist")
    if candidate_info.get("CFBundleVersion") != "20017":
        raise ValueError("Candidate is not the verified manual-login build")
    origin_path = candidate / "Contents/Resources/manual-build-origin.json"
    origin = json.loads(origin_path.read_text())
    if origin.get("schema") != "glados.manual-build-origin" or origin.get("version") != 1 or origin.get("sourceFingerprint") != expected:
        raise ValueError("Build the candidate over the inspected existing App to preserve its other resources")
    for name in AUTO_COMPONENTS:
        if (candidate / name).exists():
            raise ValueError("Candidate still contains an automatic-login component")
    if list((candidate / "Contents").rglob("*.app")):
        raise ValueError("Candidate contains an unexpected nested App")
    run("/usr/bin/codesign", "--verify", "--deep", "--strict", str(candidate))
    backup_root = backup_root.expanduser().resolve()
    if backup_root == app or app in backup_root.parents or backup_root == candidate or candidate in backup_root.parents:
        raise ValueError("Rollback directory must be outside both Apps")
    backup_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    backup = backup_root / ("manual-rollback-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ"))
    backup.mkdir(mode=0o700)
    old_backup = backup / "GLaDOS Account Center.app"
    run("/usr/bin/ditto", str(app), str(old_backup))
    if fingerprint(old_backup) != expected:
        raise RuntimeError("Rollback copy could not be verified; installed App was not changed")
    before = inspect(app)
    (backup / "before.json").write_text(json.dumps(before, indent=2) + "\n")
    os.chmod(backup / "before.json", 0o600)
    disable_old_job(backup)
    if app_processes(app):
        raise ValueError("An App process remains after stopping login maintenance; installation is pending")
    if fingerprint(app) != expected:
        raise ValueError("Installed App changed during staging; installation is pending")
    staging_dir = Path(tempfile.mkdtemp(prefix=".glados-manual-stage-", dir=app.parent))
    staged = staging_dir / app.name
    displaced = staging_dir / "previous.app"
    safe_to_clean = True
    try:
        run("/usr/bin/ditto", str(candidate), str(staged))
        if fingerprint(staged) != fingerprint(candidate):
            raise RuntimeError("Staged App verification failed")
        run("/usr/bin/codesign", "--verify", "--deep", "--strict", str(staged))
        app.rename(displaced)
        safe_to_clean = False
        try:
            staged.rename(app)
            run("/usr/bin/codesign", "--verify", "--deep", "--strict", str(app))
            safe_to_clean = True
        except BaseException:
            if app.exists():
                app.rename(staging_dir / "failed-candidate.app")
            displaced.rename(app)
            safe_to_clean = True
            raise
    finally:
        # Never remove the displaced App if restoration itself failed.
        if safe_to_clean:
            shutil.rmtree(staging_dir, ignore_errors=True)
    return {"installed": inspect(app), "rollback_app": str(old_backup), "preserved": ["App Support", "BrowserProfiles", "login-refresh.sqlite", "Keychain"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["inspect", "install"])
    parser.add_argument("--app", required=True)
    parser.add_argument("--candidate")
    parser.add_argument("--expected-sha256")
    parser.add_argument("--backup-dir")
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("This helper only operates on the actual macOS host")
    app = app_path(args.app)
    if args.action == "inspect":
        result = inspect(app)
    else:
        if not all((args.candidate, args.expected_sha256, args.backup_dir)):
            parser.error("install requires --candidate, --expected-sha256 and --backup-dir")
        result = install(app, app_path(args.candidate), args.expected_sha256, Path(args.backup_dir))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(f"Installation stopped: {exc}", file=sys.stderr)
        raise SystemExit(1)
