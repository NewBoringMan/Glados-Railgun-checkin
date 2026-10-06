"""Inspect and replace the existing Account Center without any GUI automation.

Install requires the inspected App's fingerprint and a candidate built over that
same App. Only the obsolete login-refresh job is stopped. All local account data,
browser profiles and Keychain records remain untouched.
"""
from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

APP_ID = "com.enoch.glados-account-center"
JOB = APP_ID + ".login-refresh"
AUTO_COMPONENTS = (
    "Contents/Resources/LoginRefresh",
    "Contents/Frameworks/GLaDOSRefreshCenter.dylib",
    "Contents/MacOS/RefreshNotifications",
    "Contents/MacOS/LocalMailReader",
)
PROCESS_RECORD = "login-refresh-processes.json"
PROCESS_RECORD_SCHEMA = "glados.retired-login-processes"
PROCESS_EXIT_TIMEOUT = 5.0
# ps supplies only process metadata, never command arguments or environment.
ProcessTable = dict[int, tuple[int, int, str]]  # PID -> (parent PID, UID, start time)
TrackedProcesses = set[tuple[int, str]]  # PID and start time survive reparenting.


def run(*args: str, check: bool = True, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(args, check=check, text=True, capture_output=True, env=env)


def present(path: Path) -> bool:
    """A broken symlink is still an on-disk component or occupied destination."""
    return path.exists() or path.is_symlink()


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


def process_table() -> ProcessTable:
    result = run("/bin/ps", "-axo", "pid=,ppid=,uid=,lstart=", env={"LC_ALL": "C", "TZ": "UTC"})
    table: ProcessTable = {}
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split(None, 3)
        if len(parts) != 4 or not all(part.isdigit() for part in parts[:3]):
            raise RuntimeError("Could not read the process metadata table")
        pid, parent, uid = map(int, parts[:3])
        started = " ".join(parts[3].split())
        if not re.fullmatch(r"[A-Za-z]{3} [A-Za-z]{3} [0-9]{1,2} [0-9]{2}:[0-9]{2}:[0-9]{2} [0-9]{4}", started):
            raise RuntimeError("Could not read a process start time")
        if pid in table:
            raise RuntimeError("Process metadata changed while it was being read")
        table[pid] = (parent, uid, started)
    if not table:
        raise RuntimeError("The process metadata table is empty")
    return table


@lru_cache(maxsize=1)
def _libproc():
    # Loaded lazily: importing fingerprint() is safe on CI/Linux and has no Mac
    # side effects. proc_pidpath reports the executable, not files held by Finder.
    library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    library.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    library.proc_pidpath.restype = ctypes.c_int
    return library


def process_executable(pid: int) -> Path | None:
    buffer = ctypes.create_string_buffer(4096)  # PROC_PIDPATHINFO_MAXSIZE
    ctypes.set_errno(0)
    length = _libproc().proc_pidpath(pid, buffer, len(buffer))
    if length <= 0:
        error = ctypes.get_errno()
        if error in (errno.ESRCH, errno.ENOENT):
            return None  # The process exited after the metadata snapshot.
        raise RuntimeError(f"Could not determine the executable for process {pid}")
    if length >= len(buffer) or not buffer.value:
        raise RuntimeError(f"Invalid executable path for process {pid}")
    path = Path(os.fsdecode(buffer.value))
    if not path.is_absolute():
        raise RuntimeError(f"Invalid executable path for process {pid}")
    return path.resolve()


def app_processes(app: Path, *, include_workers: bool = True) -> list[int]:
    """Find native App/helper/extension code, including symlink launch paths.

    A system Python interpreter is outside the bundle. Its retired launchd job
    and descendants are checked separately using recorded PID/start-time pairs.
    """
    contents = (app / "Contents").resolve()
    refresh = (app / "Contents/Resources/LoginRefresh").resolve()
    found = []
    for pid, (_, uid, _) in process_table().items():
        if pid == os.getpid() or uid != os.getuid():
            continue
        executable = process_executable(pid)
        if executable is not None and contents in executable.parents:
            if include_workers or refresh not in executable.parents:
                found.append(pid)
    return sorted(found)


def job_target() -> str:
    return f"gui/{os.getuid()}/{JOB}"


def launchd_status() -> subprocess.CompletedProcess:
    result = run("/bin/launchctl", "print", job_target(), check=False)
    if result.returncode == 0:
        return result
    message = (result.stderr + "\n" + result.stdout).lower()
    if not any(text in message for text in ("could not find service", "no such process", "service not found")):
        raise RuntimeError("Could not determine the old login-refresh job state")
    return result


def launchd_pid(result: subprocess.CompletedProcess) -> int | None:
    """Read only the PID field of the exact service's top-level object."""
    lines = result.stdout.splitlines()
    if not lines or lines[0].strip() != job_target() + " = {":
        raise RuntimeError("Unexpected response for the old login-refresh job")
    depth = 0
    pids = []
    state = None
    for line in lines:
        stripped = line.strip()
        if depth == 1:
            match = re.fullmatch(r"pid\s*=\s*([0-9]+)", stripped)
            if match:
                pids.append(int(match.group(1)))
            elif stripped.startswith("state = "):
                state = stripped[len("state = "):]
        if stripped.endswith("{"):
            depth += 1
        elif stripped == "}":
            depth -= 1
    if depth != 0 or len(pids) > 1 or (pids and pids[0] <= 0) or (state == "running" and not pids):
        raise RuntimeError("Could not identify the old login-refresh job process")
    return pids[0] if pids else None


def job_descendants(pid: int, table: ProcessTable) -> TrackedProcesses:
    if pid not in table:
        raise RuntimeError("The old login-refresh job changed during inspection; retry installation")
    if table[pid][1] != os.getuid():
        raise RuntimeError("The old login-refresh job has an unexpected process owner")
    selected = {pid}
    while True:
        expanded = selected | {child for child, (parent, _, _) in table.items() if parent in selected}
        if expanded == selected:
            return {(child, table[child][2]) for child in selected}
        selected = expanded


def write_process_record(backup: Path, processes: TrackedProcesses) -> None:
    value = {"schema": PROCESS_RECORD_SCHEMA, "version": 1, "job": JOB, "uid": os.getuid(),
             "processes": [{"pid": pid, "started": started} for pid, started in sorted(processes)]}
    path = backup / PROCESS_RECORD
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def prior_process_records(backup_root: Path) -> TrackedProcesses:
    """Keep checking a stopped job's residual descendants when installation is retried."""
    tracked: TrackedProcesses = set()
    for directory in sorted(backup_root.glob("manual-rollback-*")):
        if directory.is_symlink():
            raise ValueError("Unexpected symlink in the rollback process records")
        if not directory.is_dir():
            continue
        path = directory / PROCESS_RECORD
        if not present(path):
            continue
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 256 * 1024:
            raise ValueError("Invalid rollback process record")
        with path.open("rb") as stream:
            data = stream.read(256 * 1024 + 1)
        if len(data) > 256 * 1024:
            raise ValueError("Invalid rollback process record")
        value = json.loads(data)
        if not isinstance(value, dict) or value.get("schema") != PROCESS_RECORD_SCHEMA or value.get("version") != 1 or value.get("job") != JOB or value.get("uid") != os.getuid():
            raise ValueError("Unexpected rollback process record identity")
        records = value.get("processes")
        if not isinstance(records, list) or len(records) > 4096:
            raise ValueError("Invalid rollback process record")
        for record in records:
            if not isinstance(record, dict) or type(record.get("pid")) is not int or record["pid"] <= 0 or not isinstance(record.get("started"), str) or not 0 < len(record["started"]) <= 128:
                raise ValueError("Invalid rollback process record")
            tracked.add((record["pid"], record["started"]))
    return tracked


def confirm_processes_exited(processes: TrackedProcesses, *, timeout: float = 0.0) -> None:
    if not processes:
        return
    deadline = time.monotonic() + timeout
    while True:
        table = process_table()
        live = sorted(pid for pid, started in processes if pid in table and table[pid][2] == started)
        if not live:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(f"Old login maintenance processes remain (PIDs: {', '.join(map(str, live))}); installation is pending")
        time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))


def inspect(app: Path) -> dict:
    info = plist(app / "Contents/Info.plist")
    support = Path.home() / "Library/Application Support/GLaDOS Account Center"
    launch_plist = Path.home() / "Library/LaunchAgents" / f"{JOB}.plist"
    return {
        "physical_app_path": str(app), "bundle_id": info["CFBundleIdentifier"],
        "version": info.get("CFBundleShortVersionString"), "build": info.get("CFBundleVersion"),
        "fingerprint": fingerprint(app), "running_pids": app_processes(app),
        "automatic_components": [name for name in AUTO_COMPONENTS if present(app / name)],
        "login_refresh_launch_plist_present": present(launch_plist),
        "legacy_identity_database_present": (support / "login-refresh.sqlite").exists(),
        "manual_account_directory_present": (support / "ManualAccounts/accounts.json").exists(),
        "browser_profiles_present": (support / "BrowserProfiles").exists(),
    }


def disable_old_job(backup: Path) -> TrackedProcesses:
    launch_plist = Path.home() / "Library/LaunchAgents" / f"{JOB}.plist"
    target = job_target()
    if present(launch_plist):
        if launch_plist.is_symlink() or not launch_plist.is_file() or plist(launch_plist).get("Label") != JOB:
            raise ValueError("Unexpected login-refresh launch plist; its job and file were not changed")
        shutil.copy2(launch_plist, backup / launch_plist.name)
    status = launchd_status()
    processes: TrackedProcesses = set()
    if status.returncode == 0:
        pid = launchd_pid(status)
        if pid is not None:
            processes = job_descendants(pid, process_table())
        # Record before bootout: if a later step fails, retries still know which
        # reparented Python/browser descendants belong to this exact retired job.
        write_process_record(backup, processes)
        run("/bin/launchctl", "bootout", target)
        if launchd_status().returncode == 0:
            raise RuntimeError("The old login-refresh job is still running")
    # Persist the user's decision even if an old App is restored later.
    run("/bin/launchctl", "disable", target)
    if present(launch_plist):
        if launch_plist.is_symlink() or not launch_plist.is_file() or plist(launch_plist).get("Label") != JOB:
            raise ValueError("The login-refresh launch plist changed while stopping its job")
        launch_plist.unlink()
    return processes


def assert_ready_to_replace(app: Path, expected: str, retired_processes: TrackedProcesses) -> None:
    confirm_processes_exited(retired_processes)
    if app_processes(app):
        raise ValueError("An App process remains after stopping login maintenance; installation is pending")
    if fingerprint(app) != expected:
        raise ValueError("Installed App changed during staging; installation is pending")


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
    if origin_path.is_symlink() or not origin_path.is_file() or origin_path.stat().st_size > 4096:
        raise ValueError("Invalid candidate build origin")
    origin = json.loads(origin_path.read_text())
    if not isinstance(origin, dict) or origin.get("schema") != "glados.manual-build-origin" or origin.get("version") != 1 or origin.get("sourceFingerprint") != expected:
        raise ValueError("Build the candidate over the inspected existing App to preserve its other resources")
    for name in AUTO_COMPONENTS:
        if present(candidate / name):
            raise ValueError("Candidate still contains an automatic-login component")
    if list((candidate / "Contents").rglob("*.app")):
        raise ValueError("Candidate contains an unexpected nested App")
    run("/usr/bin/codesign", "--verify", "--deep", "--strict", str(candidate))
    candidate_fingerprint = fingerprint(candidate)
    backup_root = backup_root.expanduser().resolve()
    if backup_root == app or app in backup_root.parents or backup_root == candidate or candidate in backup_root.parents:
        raise ValueError("Rollback directory must be outside both Apps")
    backup_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    retired_processes = prior_process_records(backup_root)
    backup = backup_root / ("manual-rollback-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ"))
    backup.mkdir(mode=0o700)
    old_backup = backup / "GLaDOS Account Center.app"
    run("/usr/bin/ditto", str(app), str(old_backup))
    if fingerprint(old_backup) != expected:
        raise RuntimeError("Rollback copy could not be verified; installed App was not changed")
    before = inspect(app)
    (backup / "before.json").write_text(json.dumps(before, indent=2) + "\n")
    os.chmod(backup / "before.json", 0o600)
    retired_processes |= disable_old_job(backup)
    confirm_processes_exited(retired_processes, timeout=PROCESS_EXIT_TIMEOUT)
    assert_ready_to_replace(app, expected, retired_processes)
    staging_dir = Path(tempfile.mkdtemp(prefix=".glados-manual-stage-", dir=app.parent))
    staged = staging_dir / "candidate.app"
    displaced = staging_dir / "previous.app"
    safe_to_clean = True
    try:
        run("/usr/bin/ditto", str(candidate), str(staged))
        if fingerprint(staged) != candidate_fingerprint or fingerprint(candidate) != candidate_fingerprint:
            raise RuntimeError("Staged App verification failed")
        run("/usr/bin/codesign", "--verify", "--deep", "--strict", str(staged))
        # Copying and signature verification can take time. Recheck the source,
        # native processes and retired workers immediately before any rename.
        assert_ready_to_replace(app, expected, retired_processes)
        safe_to_clean = False
        try:
            # Protection is active before the first OS rename: a signal may be
            # delivered after a rename succeeds but before Python returns.
            app.rename(displaced)
            staged.rename(app)
            run("/usr/bin/codesign", "--verify", "--deep", "--strict", str(app))
        except BaseException:
            try:
                if present(displaced):
                    if present(app):
                        if present(staged):
                            raise RuntimeError("An unexpected App appeared at the installation path")
                        app.rename(staging_dir / "failed-candidate.app")
                    displaced.rename(app)
                if not present(app) or fingerprint(app) != expected:
                    raise RuntimeError("The original App could not be verified after restoration")
            except BaseException as restore_error:
                raise RuntimeError(f"App restoration needs attention; retained files are at {staging_dir}; verified rollback copy is at {old_backup}") from restore_error
            safe_to_clean = True
            raise
        else:
            # An interruption after this success marker must not enter rollback
            # with cleanup enabled; the replacement try has already completed.
            safe_to_clean = True
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
