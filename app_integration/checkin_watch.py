"""Independent, read-only GitHub check-in monitor. No login or workflow dispatch."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from urllib.parse import quote
import uuid

REPOSITORY = "NewBoringMan/Glados-Railgun-checkin"
WORKFLOW = ".github/workflows/gladosAccounts.yml"
FAILURES = {"failure", "timed_out", "action_required", "cancelled", "startup_failure", "stale"}
AUTHORIZATIONS = {"authorized", "provisional", "denied", "not_determined", "unavailable"}
NOTICE_COLUMNS = "notice_id,kind,item_count,created_at,next_attempt,accepted_at,delivery_attempts,last_error"
SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS notices (
 notice_id TEXT PRIMARY KEY, kind TEXT NOT NULL, item_count INTEGER NOT NULL,
 created_at REAL NOT NULL, next_attempt REAL NOT NULL, accepted_at REAL,
 delivery_attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT NOT NULL DEFAULT '');
"""


def notice_id(scope: str) -> str:
    return "glados-refresh-" + hashlib.sha256(("checkin_failure\0" + scope).encode()).hexdigest()[:32]


def private_directory(path: Path) -> None:
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise ValueError("unsafe local state")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.is_dir():
        raise ValueError("invalid local state")


def regular_or_absent(path: Path) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError("unsafe local file")


def notification(app: Path, request: dict) -> dict:
    result = subprocess.run(
        [str(app / "Contents/MacOS/GLaDOSAccountCenter"), "--refresh-notifications"],
        input=json.dumps(request), text=True, capture_output=True, timeout=30, check=True,
    )
    value = json.loads(result.stdout)
    if not isinstance(value, dict) or type(value.get("ok")) is not bool:
        raise ValueError("invalid notification response")
    return value


def github(endpoint: str) -> dict:
    # Fixed API endpoints, explicit GET, and the user's existing gh session.
    # No token output, shell, arbitrary URL, log download or write API is used.
    executable = next((Path(p) for p in (
        "/opt/homebrew/bin/gh", "/usr/local/bin/gh", "/usr/bin/gh"
    ) if os.access(p, os.X_OK)), None)
    if executable is None:
        raise RuntimeError("github unavailable")
    result = subprocess.run(
        [str(executable), "api", "--hostname", "github.com", "--method", "GET", endpoint],
        text=True, capture_output=True, timeout=45, check=True,
    )
    value = json.loads(result.stdout)
    if not isinstance(value, dict):
        raise ValueError("invalid github response")
    return value


class Watch:
    def __init__(self, support: Path, app: Path, *, clock=time.time, api=github, notify=notification):
        self.support, self.app = support, app
        self.clock, self.api, self.notify = clock, api, notify
        folder = support / "CheckinWatch"
        private_directory(folder)
        lock_path = folder / "watch.lock"
        regular_or_absent(lock_path)
        self.lock = os.fdopen(os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600), "r+")
        try:
            # The poller may spend time querying GitHub. UI callers fail promptly
            # with a retryable status instead of launching a second poller.
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            database = folder / "watch.sqlite"
            for path in (database, Path(str(database) + "-journal"), Path(str(database) + "-wal"), Path(str(database) + "-shm")):
                regular_or_absent(path)
            descriptor = os.open(database, os.O_CREAT | os.O_RDWR, 0o600)
            os.close(descriptor)
            self.db = sqlite3.connect(database, timeout=5)
            self.db.executescript(SCHEMA)
            self.migrate()
        except BaseException:
            if hasattr(self, "db"):
                self.db.close()
            self.lock.close()
            raise

    def close(self):
        self.db.close()
        self.lock.close()

    def get(self, key, default=None):
        row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, json.dumps(value)))

    def migrate(self):
        if self.get("legacy_migrated", False):
            return
        legacy = self.support / "login-refresh.sqlite"
        regular_or_absent(legacy)
        enabled = False
        notices = []
        if legacy.exists():
            # mode=ro does not alter the original settings, notices or identities.
            connection = sqlite3.connect("file:" + quote(str(legacy)) + "?mode=ro", uri=True)
            try:
                tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if "ui_settings" in tables:
                    row = connection.execute("SELECT value FROM ui_settings WHERE key='checkin_watch_enabled'").fetchone()
                    if row:
                        enabled = json.loads(row[0])
                        if type(enabled) is not bool:
                            raise ValueError("invalid legacy switch")
                if "refresh_notices" in tables:
                    notices = connection.execute(
                        "SELECT " + NOTICE_COLUMNS + " FROM refresh_notices WHERE kind='checkin_failure'"
                    ).fetchall()
            finally:
                connection.close()
        with self.db:
            self.put("enabled", enabled)
            for row in notices:
                if (not isinstance(row[0], str) or len(row[0]) != 47 or not row[0].startswith("glados-refresh-")
                        or any(c not in "0123456789abcdef" for c in row[0][15:])
                        or type(row[2]) is not int or not 1 <= row[2] <= 500):
                    raise ValueError("invalid legacy notice")
                # Keep the stable ID, acceptance and retry deadline. Do not copy
                # old diagnostic text (it is not needed by this monitor).
                self.db.execute("INSERT OR IGNORE INTO notices VALUES (?,?,?,?,?,?,?,?)", (*row[:7], ""))
            self.put("legacy_migrated", True)

    def authorization(self):
        try:
            value = self.notify(self.app, {"op": "status"})
            status = value.get("authorization")
            return status if value.get("ok") and status in AUTHORIZATIONS else "unavailable"
        except (OSError, ValueError, subprocess.SubprocessError):
            return "unavailable"

    def status(self, *, ok=True, error=None, authorization=None):
        enabled = self.get("enabled", False)
        if type(enabled) is not bool:
            raise ValueError("invalid switch")
        return {"ok": ok, "enabled": enabled,
                "authorization": authorization or self.authorization(),
                "lastCheckedAt": self.get("lastCheckedAt"),
                "pendingNotifications": self.db.execute("SELECT count(*) FROM notices WHERE accepted_at IS NULL").fetchone()[0],
                "error": error if error is not None else self.get("error")}

    def pages(self, endpoint, field, limit):
        for page in range(1, limit + 1):
            value = self.api(endpoint + f"&per_page=100&page={page}")
            entries = value.get(field)
            if not isinstance(entries, list) or any(not isinstance(v, dict) for v in entries):
                raise ValueError("invalid github page")
            yield from entries
            if len(entries) < 100:
                return
        raise ValueError("github pagination limit")

    def scan(self):
        now = self.clock()
        start = datetime.fromtimestamp(now - 3 * 86400, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        endpoint = f"repos/{REPOSITORY}/actions/workflows/gladosAccounts.yml/runs?event=schedule&created=%3E%3D{quote(start)}"
        for run in self.pages(endpoint, "workflow_runs", 3):
            if run.get("path") != WORKFLOW or run.get("event") != "schedule" or run.get("head_branch") != "master":
                continue
            created = datetime.fromisoformat(str(run.get("created_at", "")).replace("Z", "+00:00"))
            if created.tzinfo is None or not -60 <= now - created.timestamp() <= 3 * 86400:
                continue
            if run.get("status") != "completed" or run.get("conclusion") not in FAILURES:
                continue
            run_id = run.get("id")
            if type(run_id) is not int or run_id <= 0:
                raise ValueError("invalid run id")
            identifier = notice_id("checkin:" + str(run_id))
            if self.db.execute("SELECT 1 FROM notices WHERE notice_id=?", (identifier,)).fetchone():
                continue
            failed = sum(job.get("conclusion") in FAILURES for job in self.pages(
                f"repos/{REPOSITORY}/actions/runs/{run_id}/jobs?filter=latest", "jobs", 10))
            with self.db:
                self.db.execute("INSERT OR IGNORE INTO notices VALUES (?,?,?,?,?,NULL,0,'')",
                                (identifier, "checkin_failure", max(1, min(500, failed)), now, now))

    def flush(self):
        if self.authorization() not in {"authorized", "provisional"}:
            return
        for identifier, kind, count in self.db.execute(
                "SELECT notice_id,kind,item_count FROM notices WHERE accepted_at IS NULL AND next_attempt<=? ORDER BY created_at LIMIT 5",
                (self.clock(),)).fetchall():
            with self.db:
                self.db.execute("UPDATE notices SET next_attempt=?,delivery_attempts=delivery_attempts+1 WHERE notice_id=?",
                                (self.clock() + 300, identifier))
            try:
                result = self.notify(self.app, {"op": "send", "id": identifier, "kind": kind, "count": count})
                accepted = result.get("ok") is True and result.get("accepted_by_system") is True
            except (OSError, ValueError, subprocess.SubprocessError):
                accepted = False
            with self.db:
                if accepted:
                    self.db.execute("UPDATE notices SET accepted_at=?,last_error='' WHERE notice_id=?", (self.clock(), identifier))
                else:
                    self.db.execute("UPDATE notices SET last_error='通知尚未被系统接收，将稍后重试。' WHERE notice_id=?", (identifier,))

    def execute(self, action, request=None):
        if action == "status":
            return self.status()
        if action == "set-enabled":
            if not isinstance(request, dict) or set(request) != {"enabled"} or type(request["enabled"]) is not bool:
                raise ValueError("invalid switch request")
            authorization = self.authorization()
            if request["enabled"] and authorization not in {"authorized", "provisional"}:
                return self.status(ok=False, error="请先允许系统通知，再开启签到失败提醒。", authorization=authorization)
            with self.db:
                self.put("enabled", request["enabled"])
                self.put("error", None)
            return self.status(authorization=authorization)
        if action == "authorize":
            result = self.notify(self.app, {"op": "authorize"})
            return self.status(ok=result.get("ok") is True,
                               error=None if result.get("ok") else "系统通知尚未获准，请在系统设置中允许通知。")
        if action == "test":
            identifier = notice_id("test:" + uuid.uuid4().hex)
            result = self.notify(self.app, {"op": "send", "id": identifier, "kind": "test", "count": 1})
            accepted = result.get("ok") is True and result.get("accepted_by_system") is True
            return self.status(ok=accepted, error=None if accepted else "测试通知未被系统接收，请检查通知权限。")
        if action == "poll":
            if self.get("enabled", False) is not True:
                return self.status()  # Never query GitHub or deliver while off.
            error = None
            try:
                self.scan()
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                error = "暂时无法读取 GitHub 签到结果，将稍后重试。"
            self.flush()  # Existing outbox survives a temporary GitHub failure.
            with self.db:
                self.put("lastCheckedAt", datetime.fromtimestamp(self.clock(), timezone.utc).isoformat())
                self.put("error", error)
            return self.status(ok=error is None)
        raise ValueError("invalid action")


def main():
    if sys.platform != "darwin" or len(sys.argv) != 2 or sys.argv[1] not in {"status", "set-enabled", "authorize", "test", "poll"}:
        raise ValueError("unsupported invocation")
    request = None
    if sys.argv[1] == "set-enabled":
        data = sys.stdin.buffer.read(4097)
        if len(data) > 4096:
            raise ValueError("oversize request")
        request = json.loads(data)
    app = Path(__file__).resolve().parents[2]
    support = Path.home() / "Library/Application Support/GLaDOS Account Center"
    watch = Watch(support, app)
    try:
        return watch.execute(sys.argv[1], request)
    finally:
        watch.close()


if __name__ == "__main__":
    try:
        response = main()
    except Exception:
        # No exceptions from GitHub, sqlite or subprocesses are exposed: they may
        # contain local paths or remote diagnostic details.
        response = {"ok": False, "enabled": False, "authorization": "unavailable", "lastCheckedAt": None,
                    "pendingNotifications": 0, "error": "暂时无法读取签到提醒设置，请稍后重试。"}
    print(json.dumps(response, ensure_ascii=False, separators=(",", ":")))
