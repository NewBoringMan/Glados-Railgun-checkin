"""Synthetic check-in notifications; no Mac, GitHub, credentials or real notices."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
from datetime import datetime, timezone

SPEC = importlib.util.spec_from_file_location("checkin_watch_under_test", Path(__file__).resolve().parents[1] / "app_integration/checkin_watch.py")
watch_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(watch_module)
NOW = datetime(2026, 10, 6, 12, tzinfo=timezone.utc).timestamp()


class WatchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="glados-watch-test-")
        self.addCleanup(temporary.cleanup)
        self.support = Path(temporary.name).resolve() / "support"
        self.support.mkdir()
        self.app = Path(temporary.name).resolve() / "Example.app"
        self.api = Mock(return_value={"workflow_runs": []})
        self.allowed = "authorized"
        self.accepted = True
        self.notices = []
        self.now = NOW
        self.watches = []
        self.addCleanup(self.close_all)
        guard = patch.object(subprocess, "run", side_effect=AssertionError("Unexpected real process"))
        guard.start()
        self.addCleanup(guard.stop)

    def close_all(self):
        for watch in self.watches:
            watch.close()
        self.watches.clear()

    def notify(self, app, request):
        self.assertEqual(app, self.app)
        if request["op"] == "status":
            return {"ok": True, "authorization": self.allowed}
        if request["op"] == "authorize":
            return {"ok": self.allowed in {"authorized", "provisional"}, "authorization": self.allowed}
        self.notices.append(request)
        return {"ok": self.accepted, "accepted_by_system": self.accepted}

    def open(self):
        watch = watch_module.Watch(self.support, self.app, api=self.api, notify=self.notify, clock=lambda: self.now)
        self.watches.append(watch)
        return watch

    def legacy(self, enabled=False, notices=()):
        connection = sqlite3.connect(self.support / "login-refresh.sqlite")
        connection.executescript("CREATE TABLE ui_settings (key TEXT PRIMARY KEY, value TEXT);" + watch_module.SCHEMA.replace("notices (", "refresh_notices ("))
        connection.execute("INSERT INTO ui_settings VALUES ('checkin_watch_enabled',?)", (json.dumps(enabled),))
        for row in notices:
            connection.execute("INSERT INTO refresh_notices VALUES (?,?,?,?,?,?,?,?)", row)
        connection.commit()
        connection.close()

    @staticmethod
    def run_data(run_id=8, **changes):
        value = {"id": run_id, "path": watch_module.WORKFLOW, "event": "schedule", "head_branch": "master",
                 "status": "completed", "conclusion": "failure", "created_at": "2026-10-06T10:00:00Z"}
        value.update(changes)
        return value

    def enable(self, watch):
        self.assertTrue(watch.execute("set-enabled", {"enabled": True})["ok"])

    def failed_api(self, endpoint):
        if "/workflows/" in endpoint:
            return {"workflow_runs": [self.run_data()]}
        self.assertIn("/actions/runs/8/jobs?filter=latest&per_page=100&page=1", endpoint)
        return {"jobs": [{"conclusion": "failure"}, {"conclusion": "success"}, {"conclusion": "timed_out"}]}

    def test_missing_setting_defaults_off_without_github_or_delivery(self):
        watch = self.open()
        result = watch.execute("poll")
        self.assertFalse(result["enabled"])
        self.assertIsNone(result["lastCheckedAt"])
        self.api.assert_not_called()
        self.assertEqual(self.notices, [])

    def test_legacy_off_is_preserved_without_database_write(self):
        self.legacy(False)
        path = self.support / "login-refresh.sqlite"
        before = path.read_bytes()
        self.assertFalse(self.open().status()["enabled"])
        self.assertEqual(path.read_bytes(), before)

    def test_migration_copies_only_checkin_notices_and_preserves_acceptance(self):
        identifier = watch_module.notice_id("checkin:8")
        self.legacy(True, [(identifier, "checkin_failure", 3, NOW - 100, NOW, NOW - 50, 1, "old diagnostic"),
                           ("glados-refresh-" + "b" * 32, "account_manual", 2, NOW, NOW, None, 1, "omit")])
        watch = self.open()
        self.api.side_effect = self.failed_api
        watch.execute("poll")
        row = watch.db.execute("SELECT accepted_at,last_error FROM notices").fetchone()
        self.assertEqual(row, (NOW - 50, ""))
        self.assertEqual(self.notices, [])
        self.assertEqual(self.api.call_count, 1)  # No need to reread jobs for a known notice.

    def test_new_switch_survives_restart_and_does_not_remigrate_old_value(self):
        self.legacy(True)
        watch = self.open()
        watch.execute("set-enabled", {"enabled": False})
        self.close_all()
        self.assertFalse(self.open().status()["enabled"])

    def test_invalid_legacy_switch_does_not_silently_disable_it(self):
        self.legacy("true")
        with self.assertRaises(ValueError):
            self.open()
        connection = sqlite3.connect(self.support / "CheckinWatch/watch.sqlite")
        self.assertIsNone(connection.execute("SELECT value FROM settings WHERE key='legacy_migrated'").fetchone())
        connection.close()

    def test_failed_run_enqueues_once_with_job_count_and_stable_identifier(self):
        watch = self.open()
        self.enable(watch)
        self.api.side_effect = self.failed_api
        self.assertTrue(watch.execute("poll")["ok"])
        watch.execute("poll")
        self.assertEqual(self.notices, [{"op": "send", "id": watch_module.notice_id("checkin:8"), "kind": "checkin_failure", "count": 2}])
        self.assertIn("created=%3E%3D", self.api.call_args_list[0].args[0])
        self.assertEqual(watch.status()["pendingNotifications"], 0)

    def test_filters_other_workflow_manual_branch_old_future_and_success(self):
        watch = self.open()
        self.enable(watch)
        self.api.return_value = {"workflow_runs": [
            self.run_data(path=".github/workflows/gladosStatus.yml"),
            self.run_data(event="workflow_dispatch"), self.run_data(head_branch="other"),
            self.run_data(created_at="2026-10-02T10:00:00Z"), self.run_data(created_at="2026-10-07T10:00:00Z"),
            self.run_data(conclusion="success"), self.run_data(status="in_progress"),
        ]}
        watch.execute("poll")
        self.assertEqual(self.api.call_count, 1)
        self.assertEqual(self.notices, [])

    def test_retry_persists_and_waits_300_seconds_then_accepts(self):
        watch = self.open()
        self.enable(watch)
        self.api.side_effect = self.failed_api
        self.accepted = False
        self.assertEqual(watch.execute("poll")["pendingNotifications"], 1)
        self.close_all()
        watch = self.open()
        watch.execute("poll")
        self.assertEqual(len(self.notices), 1)
        self.now += 300
        self.accepted = True
        watch.execute("poll")
        self.assertEqual(len(self.notices), 2)
        self.assertEqual(watch.status()["pendingNotifications"], 0)
        self.assertEqual(watch.db.execute("SELECT delivery_attempts FROM notices").fetchone()[0], 2)

    def test_disabled_does_not_flush_existing_outbox_or_query_github(self):
        self.legacy(False, [(watch_module.notice_id("checkin:8"), "checkin_failure", 1, NOW, NOW, None, 0, "")])
        self.assertEqual(self.open().execute("poll")["pendingNotifications"], 1)
        self.api.assert_not_called()
        self.assertEqual(self.notices, [])

    def test_denied_notifications_are_kept_for_later_without_authorization_prompt(self):
        self.legacy(True)
        watch = self.open()
        self.allowed = "denied"
        self.api.side_effect = self.failed_api
        self.assertEqual(watch.execute("poll")["pendingNotifications"], 1)
        self.assertEqual(self.notices, [])
        self.assertFalse(watch.execute("set-enabled", {"enabled": True})["ok"])
        self.assertTrue(watch.execute("set-enabled", {"enabled": False})["ok"])

    def test_github_failure_still_flushes_persisted_outbox_and_redacts_exception(self):
        self.legacy(True, [(watch_module.notice_id("checkin:8"), "checkin_failure", 1, NOW, NOW, None, 0, "")])
        self.api.side_effect = RuntimeError("synthetic private diagnostic")
        result = self.open().execute("poll")
        self.assertFalse(result["ok"])
        self.assertNotIn("private", result["error"])
        self.assertEqual(len(self.notices), 1)

    def test_limit_flush_to_five_and_paginate_failed_jobs(self):
        watch = self.open()
        self.enable(watch)
        runs = [self.run_data(run_id=i) for i in range(1, 7)]
        self.api.side_effect = lambda endpoint: {"workflow_runs": runs} if "/workflows/" in endpoint else {"jobs": [{"conclusion": "failure"}] * 100 if endpoint.endswith("page=1") else [{"conclusion": "cancelled"}]}
        result = watch.execute("poll")
        self.assertEqual(len(self.notices), 5)
        self.assertEqual(result["pendingNotifications"], 1)
        self.assertTrue(all(n["count"] == 101 for n in self.notices))

    def test_explicit_test_does_not_change_switch_or_query_github(self):
        watch = self.open()
        self.assertTrue(watch.execute("test")["ok"])
        self.assertEqual(self.notices[0]["kind"], "test")
        self.assertFalse(watch.status()["enabled"])
        self.api.assert_not_called()

    def test_concurrent_process_is_rejected_without_second_poll(self):
        self.open()
        with self.assertRaises(BlockingIOError):
            self.open()
        self.api.assert_not_called()

    def test_symlinked_state_is_rejected(self):
        target = self.support / "elsewhere"
        target.mkdir()
        (self.support / "CheckinWatch").symlink_to(target, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.open()

    def test_github_adapter_uses_only_fixed_get_and_never_reveals_auth(self):
        result = subprocess.CompletedProcess([], 0, '{"workflow_runs":[]}', "")
        with patch.object(watch_module.os, "access", return_value=True), patch.object(subprocess, "run", return_value=result) as run:
            watch_module.github("repos/example/project/actions/workflows/gladosAccounts.yml/runs?event=schedule")
        command = run.call_args.args[0]
        self.assertEqual(command[1:6], ["api", "--hostname", "github.com", "--method", "GET"])
        self.assertNotIn("input", run.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
