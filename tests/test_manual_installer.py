"""Offline installation fault injection; no Mac commands, GUI or credentials."""
import ctypes
import errno
import importlib.util
import json
import os
import plistlib
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


SPEC = importlib.util.spec_from_file_location(
    "manual_installer_under_test",
    Path(__file__).resolve().parents[1] / "app_integration/install-manual.py",
)
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)

START = "Tue Oct 6 01:02:03 2026"
LATER_START = "Tue Oct 6 02:03:04 2026"


def completed(args=(), *, code=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args, code, stdout, stderr)


def loaded_job(pid=210):
    return completed(stdout=(
        installer.job_target() + " = {\n"
        "\tstate = running\n"
        "\targuments = {\n\t\tpid = 999\n\t}\n"
        f"\tpid = {pid}\n"
        "}\n"
    ))


def missing_job():
    return completed(code=113, stderr="Could not find service in domain for user gui")


class InstallerFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="glados-installer-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.user_home = self.root / "home"
        self.user_home.mkdir()
        self.app = self.root / "physical" / "GLaDOS Account Center.app"
        self.candidate = self.root / "build" / "Candidate.app"
        self.backups = self.root / "backups"
        self.make_app(self.app, "20042", b"original executable")
        self.expected = installer.fingerprint(self.app)
        self.make_app(self.candidate, "20045", b"new executable")
        self.origin_path = self.candidate / "Contents/Resources/manual-build-origin.json"
        self.origin_path.write_text(json.dumps({
            "schema": "glados.manual-build-origin", "version": 1,
            "sourceFingerprint": self.expected,
        }))
        self.launch_plist = self.user_home / "Library/LaunchAgents" / f"{installer.JOB}.plist"
        self.launch_plist.parent.mkdir(parents=True)
        self.native_pids = []
        self.table = {}
        self.commands = []
        self.launch_results = []
        self.watch_loaded = False
        self.watch_pid = None
        self.on_watch_bootstrap = lambda: None
        self.on_staged_copy = lambda: None
        self.on_verify = lambda path: None
        self.on_bootout = lambda: None
        self.start_patch(patch.object(Path, "home", return_value=self.user_home))
        self.start_patch(patch.object(installer, "run", side_effect=self.fake_run))
        self.native_mock = self.start_patch(patch.object(installer, "app_processes", side_effect=lambda *a, **k: list(self.native_pids)))
        self.table_mock = self.start_patch(patch.object(installer, "process_table", side_effect=lambda: dict(self.table)))
        self.start_patch(patch.object(installer, "PROCESS_EXIT_TIMEOUT", 0))
        # No accidental subprocess or native-library call can escape the fakes.
        self.start_patch(patch.object(subprocess, "run", side_effect=AssertionError("Unexpected real subprocess")))
        self.start_patch(patch.object(ctypes, "CDLL", side_effect=AssertionError("Unexpected real native-library load")))

    def start_patch(self, patcher):
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    @staticmethod
    def make_app(app, build, executable):
        (app / "Contents/MacOS").mkdir(parents=True)
        (app / "Contents/Resources").mkdir()
        (app / "Contents/Info.plist").write_bytes(plistlib.dumps({
            "CFBundleIdentifier": installer.APP_ID,
            "CFBundleVersion": build,
            "CFBundleShortVersionString": "2.0.13" if build == "20045" else "2.0.10",
            "CFBundleExecutable": "GLaDOSAccountCenter",
        }))
        (app / "Contents/MacOS/GLaDOSAccountCenter").write_bytes(executable)
        (app / "Contents/Resources/kept.txt").write_text("unrelated original resource")
        (app / "Contents/Frameworks").mkdir()
        (app / "Contents/Frameworks/GLaDOSNotifications.dylib").write_bytes(b"retained notification library")
        (app / "Contents/Resources/checkin_watch.py").write_text("# synthetic offline helper")

    def fake_run(self, *args, **kwargs):
        self.commands.append(args)
        if args[0] == "/usr/bin/ditto":
            shutil.copytree(args[1], args[2], symlinks=True)
            if Path(args[1]) == self.candidate:
                self.on_staged_copy()
            return completed(args)
        if args[0] == "/usr/bin/codesign":
            self.on_verify(Path(args[-1]))
            return completed(args)
        if args[:2] == ("/bin/launchctl", "print"):
            if args[2] == installer.job_target(installer.WATCH_JOB):
                if not self.watch_loaded:
                    return missing_job()
                pid_line = f"\tpid = {self.watch_pid}\n" if self.watch_pid else ""
                return completed(stdout=args[2] + " = {\n\tstate = waiting\n" + pid_line + "}\n")
            self.assertEqual(args[2], installer.job_target())
            return self.launch_results.pop(0) if self.launch_results else missing_job()
        if args[:2] == ("/bin/launchctl", "bootout"):
            if args[2] == installer.job_target(installer.WATCH_JOB):
                self.watch_loaded = False
                return completed(args)
            self.assertEqual(args[2], installer.job_target())
            self.on_bootout()
            return completed(args)
        if args[:2] == ("/bin/launchctl", "disable"):
            self.assertEqual(args[2], installer.job_target())
            return completed(args)
        if args[:2] == ("/bin/launchctl", "bootstrap"):
            self.assertEqual(args[2], f"gui/{os.getuid()}")
            self.assertEqual(Path(args[3]).name, installer.WATCH_JOB + ".plist")
            self.watch_loaded = True
            self.on_watch_bootstrap()
            return completed(args)
        raise AssertionError(f"Unexpected simulated command: {args}")

    def install(self):
        return installer.install(self.app, self.candidate, self.expected, self.backups)

    def assert_original(self):
        self.assertTrue(self.app.is_dir())
        self.assertEqual(installer.fingerprint(self.app), self.expected)

    def staging_dirs(self):
        return list(self.app.parent.glob(".glados-manual-stage-*"))

    def job_backup(self, name="manual-rollback-test"):
        backup = self.backups / name
        backup.mkdir(parents=True)
        return backup

    def write_launch_plist(self, label=None):
        self.launch_plist.write_bytes(plistlib.dumps({
            "Label": label or installer.JOB,
            "ProgramArguments": ["/synthetic/python", "/synthetic/worker.py"],
        }))


class IndependentWatchDeploymentTests(InstallerFixture):
    @property
    def watch_plist(self):
        return self.launch_plist.with_name(installer.WATCH_JOB + ".plist")

    def existing_watch(self):
        content = plistlib.dumps({
            "Label": installer.WATCH_JOB,
            "ProgramArguments": [str(self.app / "Contents/MacOS/GLaDOSAccountCenter"), "--checkin-watch", "poll"],
            "StartInterval": 900, "RunAtLoad": True, "OriginalSetting": "retained on rollback",
        })
        self.watch_plist.write_bytes(content)
        self.watch_loaded = True
        return content

    def test_success_deploys_readonly_watch_without_app_support_or_auto_enable(self):
        result = self.install()
        value = installer.plist(self.watch_plist)
        self.assertEqual(value["ProgramArguments"][1:], ["--checkin-watch", "poll"])
        self.assertEqual(value["StartInterval"], 900)
        self.assertIs(value["RunAtLoad"], True)
        self.assertTrue(self.watch_loaded)
        self.assertFalse((self.user_home / "Library/Application Support").exists())
        self.assertFalse(any(command[:2] == ("/bin/launchctl", "enable") for command in self.commands))
        record = json.loads(Path(result["checkin_watch"]["deployment_record"]).read_text())
        self.assertEqual(record["state"], "deployed")

    def test_bootstrap_failure_rolls_back_app_and_removes_new_job_plist(self):
        self.on_watch_bootstrap = lambda: (_ for _ in ()).throw(subprocess.CalledProcessError(5, "synthetic bootstrap"))
        with self.assertRaises(subprocess.CalledProcessError):
            self.install()
        self.assert_original()
        self.assertFalse(self.watch_plist.exists())
        self.assertFalse(self.watch_loaded)
        self.assertEqual(self.staging_dirs(), [])

    def test_interrupt_after_watch_bootstrap_restores_app_and_watch(self):
        original = self.existing_watch()
        calls = 0
        def interrupt_first():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise KeyboardInterrupt()
        self.on_watch_bootstrap = interrupt_first
        with self.assertRaises(KeyboardInterrupt):
            self.install()
        self.assert_original()
        self.assertEqual(self.watch_plist.read_bytes(), original)
        self.assertTrue(self.watch_loaded)
        self.assertEqual(calls, 2)
        self.assertFalse(any(command[:3] == ("/bin/launchctl", "bootstrap", installer.job_target()) for command in self.commands))

    def test_unknown_existing_watch_is_not_overwritten_or_stopped(self):
        self.watch_plist.write_bytes(plistlib.dumps({"Label": installer.WATCH_JOB, "ProgramArguments": ["/foreign/program"]}))
        with self.assertRaisesRegex(ValueError, "Unexpected existing"):
            self.install()
        self.assert_original()
        self.assertFalse(any(command[1] == "bootout" for command in self.commands if command[0] == "/bin/launchctl"))

    def test_dangling_watch_plist_blocks_installation(self):
        self.watch_plist.symlink_to(self.user_home / "missing")
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.install()
        self.assert_original()

    def test_changed_retained_notification_library_rejects_candidate(self):
        (self.candidate / "Contents/Frameworks/GLaDOSNotifications.dylib").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "retained helper"):
            self.install()
        self.assert_original()

    def test_old_candidate_version_rejected_before_launchd(self):
        info_path = self.candidate / "Contents/Info.plist"
        value = installer.plist(info_path)
        value["CFBundleVersion"] = "20017"
        info_path.write_bytes(plistlib.dumps(value))
        with self.assertRaisesRegex(ValueError, "verified manual-login build"):
            self.install()
        self.assertEqual(self.commands, [])

    def test_watch_restore_failure_keeps_displaced_app_and_verified_backup(self):
        self.existing_watch()
        self.on_watch_bootstrap = lambda: (_ for _ in ()).throw(subprocess.CalledProcessError(5, "synthetic bootstrap"))
        with self.assertRaisesRegex(RuntimeError, "restoration needs attention"):
            self.install()
        self.assert_original()
        self.assertEqual(len(self.staging_dirs()), 1)
        self.assertEqual(len(list(self.backups.glob("manual-rollback-*/GLaDOS Account Center.app"))), 1)


class ReplacementTests(InstallerFixture):
    def test_success_keeps_logical_app_symlink_and_verified_rollback(self):
        alias = self.root / "Applications" / self.app.name
        alias.parent.mkdir()
        alias.symlink_to(self.app, target_is_directory=True)
        self.assertEqual(installer.app_path(str(alias)), self.app)
        result = self.install()
        self.assertTrue(alias.is_symlink())
        self.assertEqual(alias.resolve(), self.app)
        self.assertEqual(installer.plist(self.app / "Contents/Info.plist")["CFBundleVersion"], "20045")
        self.assertEqual(installer.fingerprint(Path(result["rollback_app"])), self.expected)
        self.assertEqual(self.staging_dirs(), [])

    def test_first_rename_error_preserves_original_and_cleans_own_stage(self):
        original_rename = Path.rename
        def rename(path, target):
            if path == self.app:
                raise OSError("synthetic first rename error")
            return original_rename(path, target)
        with patch.object(Path, "rename", rename), self.assertRaises(OSError):
            self.install()
        self.assert_original()
        self.assertEqual(self.staging_dirs(), [])

    def test_interrupt_after_original_os_rename_restores_original(self):
        original_rename = Path.rename
        interrupted = False
        def rename(path, target):
            nonlocal interrupted
            result = original_rename(path, target)
            if path == self.app and not interrupted:
                interrupted = True
                raise KeyboardInterrupt("after successful OS rename")
            return result
        with patch.object(Path, "rename", rename), self.assertRaises(KeyboardInterrupt):
            self.install()
        self.assert_original()
        self.assertEqual(self.staging_dirs(), [])

    def test_candidate_rename_error_restores_original(self):
        original_rename = Path.rename
        def rename(path, target):
            if path.name == "candidate.app" and path.parent.name.startswith(".glados-manual-stage-"):
                raise OSError("synthetic candidate rename error")
            return original_rename(path, target)
        with patch.object(Path, "rename", rename), self.assertRaises(OSError):
            self.install()
        self.assert_original()
        self.assertEqual(self.staging_dirs(), [])

    def test_interrupt_after_candidate_os_rename_restores_original(self):
        original_rename = Path.rename
        def rename(path, target):
            result = original_rename(path, target)
            if path.name == "candidate.app" and Path(target) == self.app:
                raise KeyboardInterrupt("after successful candidate rename")
            return result
        with patch.object(Path, "rename", rename), self.assertRaises(KeyboardInterrupt):
            self.install()
        self.assert_original()
        self.assertEqual(self.staging_dirs(), [])

    def test_signature_failure_after_replacement_restores_original(self):
        def verify(path):
            if path == self.app:
                raise subprocess.CalledProcessError(1, "synthetic codesign")
        self.on_verify = verify
        with self.assertRaises(subprocess.CalledProcessError):
            self.install()
        self.assert_original()
        self.assertEqual(self.staging_dirs(), [])

    def test_restore_error_never_deletes_displaced_original(self):
        original_rename = Path.rename
        def rename(path, target):
            if path.name in ("candidate.app", "previous.app") and path.parent.name.startswith(".glados-manual-stage-"):
                raise OSError("synthetic rename/restore failure")
            return original_rename(path, target)
        with patch.object(Path, "rename", rename), self.assertRaisesRegex(RuntimeError, "retained files"):
            self.install()
        stages = self.staging_dirs()
        self.assertEqual(len(stages), 1)
        self.assertEqual(installer.fingerprint(stages[0] / "previous.app"), self.expected)
        rollbacks = list(self.backups.glob("manual-rollback-*/GLaDOS Account Center.app"))
        self.assertEqual(len(rollbacks), 1)
        self.assertEqual(installer.fingerprint(rollbacks[0]), self.expected)

    def test_interrupt_during_completed_restore_keeps_retained_files(self):
        original_rename = Path.rename
        def rename(path, target):
            if path.name == "candidate.app" and path.parent.name.startswith(".glados-manual-stage-"):
                raise OSError("synthetic replacement failure")
            result = original_rename(path, target)
            if path.name == "previous.app":
                raise KeyboardInterrupt("after completed restore")
            return result
        with patch.object(Path, "rename", rename), self.assertRaisesRegex(RuntimeError, "retained files"):
            self.install()
        self.assert_original()
        self.assertEqual(len(self.staging_dirs()), 1)

    def test_unexpected_new_app_is_not_removed_during_recovery(self):
        original_rename = Path.rename
        def rename(path, target):
            result = original_rename(path, target)
            if path == self.app:
                self.make_app(self.app, "99999", b"unexpected separate App")
            return result
        with patch.object(Path, "rename", rename), self.assertRaisesRegex(RuntimeError, "retained files"):
            self.install()
        self.assertEqual(installer.plist(self.app / "Contents/Info.plist")["CFBundleVersion"], "99999")
        self.assertEqual(installer.fingerprint(self.staging_dirs()[0] / "previous.app"), self.expected)

    def test_source_changed_after_staging_is_not_replaced(self):
        def change_source():
            (self.app / "Contents/Resources/kept.txt").write_text("changed during staging")
        self.on_staged_copy = change_source
        with patch.object(Path, "rename", side_effect=AssertionError("Replacement must not begin")), self.assertRaisesRegex(ValueError, "changed during staging"):
            self.install()
        self.assertEqual((self.app / "Contents/Resources/kept.txt").read_text(), "changed during staging")
        self.assertEqual(self.staging_dirs(), [])

    def test_app_reopened_after_staging_is_not_replaced(self):
        self.on_staged_copy = lambda: self.native_pids.append(250)
        with patch.object(Path, "rename", side_effect=AssertionError("Replacement must not begin")), self.assertRaisesRegex(ValueError, "App process remains"):
            self.install()
        self.assert_original()
        self.assertEqual(self.staging_dirs(), [])

    def test_candidate_changed_during_staging_is_not_installed(self):
        self.on_staged_copy = lambda: (self.candidate / "Contents/Resources/kept.txt").write_text("changed candidate")
        with self.assertRaisesRegex(RuntimeError, "Staged App verification"):
            self.install()
        self.assert_original()
        self.assertEqual(self.staging_dirs(), [])

    def test_dangling_automatic_component_is_rejected(self):
        for name in installer.AUTO_COMPONENTS:
            with self.subTest(name=name):
                path = self.candidate / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.symlink_to(self.root / "nonexistent")
                self.assertFalse(path.exists())
                with self.assertRaisesRegex(ValueError, "automatic-login component"):
                    self.install()
                self.assertTrue(path.is_symlink())
                path.unlink()
        self.assert_original()
        self.assertFalse(self.backups.exists())

    def test_symlink_origin_is_rejected_before_reading_external_file(self):
        outside = self.root / "outside-origin.json"
        self.origin_path.rename(outside)
        self.origin_path.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "build origin"):
            self.install()
        self.assert_original()
        self.assertFalse(self.backups.exists())


class LaunchdTests(InstallerFixture):
    def test_only_exact_job_is_stopped_and_descendants_are_recorded_before_bootout(self):
        self.write_launch_plist()
        unrelated = self.launch_plist.parent / "unrelated.plist"
        unrelated.write_text("unrelated launch agent")
        uid = os.getuid()
        self.table = {210: (1, uid, START), 211: (210, uid, START),
                      212: (211, uid, START), 299: (1, uid, START)}
        self.launch_results = [loaded_job(), missing_job()]
        backup = self.job_backup()
        def bootout():
            self.assertTrue((backup / installer.PROCESS_RECORD).is_file())
        self.on_bootout = bootout
        processes = installer.disable_old_job(backup)
        self.assertEqual(processes, {(210, START), (211, START), (212, START)})
        self.assertEqual(installer.prior_process_records(self.backups), processes)
        self.assertEqual([command[1] for command in self.commands], ["print", "bootout", "print", "disable"])
        self.assertTrue(all(command[2] == installer.job_target() for command in self.commands))
        self.assertFalse(installer.present(self.launch_plist))
        self.assertEqual(unrelated.read_text(), "unrelated launch agent")
        self.assertEqual((backup / installer.PROCESS_RECORD).stat().st_mode & 0o777, 0o600)

    def test_unknown_initial_launchctl_error_does_not_stop_or_unlink(self):
        self.write_launch_plist()
        self.launch_results = [completed(code=1, stderr="Operation not permitted")]
        with self.assertRaisesRegex(RuntimeError, "job state"):
            installer.disable_old_job(self.job_backup())
        self.assertTrue(self.launch_plist.exists())
        self.assertEqual([command[1] for command in self.commands], ["print"])

    def test_unknown_post_bootout_error_is_not_treated_as_absence(self):
        self.write_launch_plist()
        self.table = {210: (1, os.getuid(), START)}
        self.launch_results = [loaded_job(), completed(code=1, stderr="IPC connection failed")]
        backup = self.job_backup()
        with self.assertRaisesRegex(RuntimeError, "job state"):
            installer.disable_old_job(backup)
        self.assertTrue(self.launch_plist.exists())
        self.assertEqual([command[1] for command in self.commands], ["print", "bootout", "print"])
        self.assertEqual(installer.prior_process_records(self.backups), {(210, START)})

    def test_still_loaded_job_after_bootout_blocks_unlink(self):
        self.write_launch_plist()
        self.table = {210: (1, os.getuid(), START)}
        self.launch_results = [loaded_job(), loaded_job()]
        with self.assertRaisesRegex(RuntimeError, "still running"):
            installer.disable_old_job(self.job_backup())
        self.assertTrue(self.launch_plist.exists())

    def test_explicit_not_found_allows_only_disable(self):
        for index, message in enumerate(("Could not find service", "No such process", "Service not found")):
            with self.subTest(message=message):
                self.commands.clear()
                self.launch_results = [completed(code=113, stderr=message)]
                processes = installer.disable_old_job(self.job_backup(f"manual-rollback-{index}"))
                self.assertEqual(processes, set())
                self.assertEqual([command[1] for command in self.commands], ["print", "disable"])

    def test_dangling_launch_plist_is_rejected_without_launchctl(self):
        self.launch_plist.symlink_to(self.root / "missing-plist")
        with self.assertRaisesRegex(ValueError, "Unexpected login-refresh launch plist"):
            installer.disable_old_job(self.job_backup())
        self.assertTrue(self.launch_plist.is_symlink())
        self.assertEqual(self.commands, [])

    def test_different_label_is_rejected_without_launchctl(self):
        self.write_launch_plist("unrelated.job")
        with self.assertRaises(ValueError):
            installer.disable_old_job(self.job_backup())
        self.assertTrue(self.launch_plist.exists())
        self.assertEqual(self.commands, [])

    def test_wrong_job_response_or_ambiguous_pid_is_rejected(self):
        for text in (
            "gui/999/unrelated.job = {\n\tpid = 210\n}\n",
            installer.job_target() + " = {\n\tpid = 210\n\tpid = 211\n}\n",
            installer.job_target() + " = {\n\tstate = running\n}\n",
        ):
            with self.subTest(response=text), self.assertRaises(RuntimeError):
                installer.launchd_pid(completed(stdout=text))

    def test_reparented_worker_blocks_but_reused_pid_does_not(self):
        tracked = {(211, START)}
        self.table = {211: (1, os.getuid(), START), 299: (1, os.getuid(), START)}
        with self.assertRaisesRegex(RuntimeError, "211"):
            installer.confirm_processes_exited(tracked)
        self.table[211] = (1, os.getuid(), LATER_START)
        installer.confirm_processes_exited(tracked)

    def test_exit_confirmation_waits_only_for_recorded_processes(self):
        self.table_mock.side_effect = [
            {211: (1, os.getuid(), START), 299: (1, os.getuid(), START)},
            {299: (1, os.getuid(), START)},
        ]
        with patch.object(installer.time, "sleep") as sleep:
            installer.confirm_processes_exited({(211, START)}, timeout=1.0)
        sleep.assert_called_once()

    def test_retry_checks_descendants_even_when_old_job_is_already_gone(self):
        installer.write_process_record(self.job_backup(), {(211, START)})
        self.table = {211: (1, os.getuid(), START)}
        with self.assertRaisesRegex(RuntimeError, "211"):
            self.install()
        self.assert_original()
        self.assertEqual(self.staging_dirs(), [])
        self.assertFalse(any(command[1] == "bootout" for command in self.commands if command[0] == "/bin/launchctl"))

    def test_retired_worker_is_rechecked_after_staging(self):
        installer.write_process_record(self.job_backup(), {(211, START)})
        self.on_staged_copy = lambda: self.table.update({211: (1, os.getuid(), START)})
        with patch.object(Path, "rename", side_effect=AssertionError("Replacement must not begin")), self.assertRaisesRegex(RuntimeError, "211"):
            self.install()
        self.assert_original()
        self.assertEqual(self.staging_dirs(), [])


class ProcessInspectionTests(unittest.TestCase):
    def setUp(self):
        self.native_guard = patch.object(ctypes, "CDLL", side_effect=AssertionError("Unexpected native-library load"))
        self.native_guard.start()
        self.addCleanup(self.native_guard.stop)

    def test_proc_pidpath_resolves_app_symlink(self):
        with tempfile.TemporaryDirectory(prefix="glados-proc-test-") as raw:
            root = Path(raw).resolve()
            physical = root / "physical.app"
            (physical / "Contents/MacOS").mkdir(parents=True)
            executable = physical / "Contents/MacOS/GLaDOSAccountCenter"
            executable.write_bytes(b"synthetic executable")
            alias = root / "logical.app"
            alias.symlink_to(physical, target_is_directory=True)
            path = os.fsencode(alias / "Contents/MacOS/GLaDOSAccountCenter")
            def pidpath(pid, buffer, size):
                self.assertEqual(pid, 210)
                self.assertEqual(size, 4096)
                buffer.value = path
                return len(path)
            library = Mock(proc_pidpath=Mock(side_effect=pidpath))
            with patch.object(installer, "_libproc", return_value=library):
                self.assertEqual(installer.process_executable(210), executable)

    def test_exited_pid_is_ignored_but_permission_failure_is_not(self):
        for code, should_raise in ((errno.ESRCH, False), (errno.EPERM, True), (0, True)):
            with self.subTest(errno=code):
                def pidpath(*args):
                    ctypes.set_errno(code)
                    return 0
                with patch.object(installer, "_libproc", return_value=Mock(proc_pidpath=Mock(side_effect=pidpath))):
                    if should_raise:
                        with self.assertRaises(RuntimeError):
                            installer.process_executable(210)
                    else:
                        self.assertIsNone(installer.process_executable(210))

    def test_native_lookup_does_not_confuse_finder_or_path_prefixes_with_app(self):
        app = Path("/synthetic/Account Center.app")
        table = {210: (1, os.getuid(), START), 211: (1, os.getuid(), START),
                 212: (1, os.getuid(), START), 213: (1, os.getuid() + 1, START)}
        paths = {210: app / "Contents/MacOS/GLaDOSAccountCenter.real",
                 211: Path("/System/Library/CoreServices/Finder.app/Contents/MacOS/Finder"),
                 212: Path("/synthetic/Account Center.app-other/Contents/MacOS/Other")}
        with patch.object(installer, "process_table", return_value=table), patch.object(installer, "process_executable", side_effect=paths.get) as lookup:
            self.assertEqual(installer.app_processes(app), [210])
        self.assertEqual([call.args[0] for call in lookup.call_args_list], [210, 211, 212])

    def test_metadata_command_omits_arguments_and_environment(self):
        output = " 210 1 501 Tue Oct  6 01:02:03 2026\n"
        with patch.object(installer, "run", return_value=completed(stdout=output)) as run:
            self.assertEqual(installer.process_table(), {210: (1, 501, START)})
        run.assert_called_once_with("/bin/ps", "-axo", "pid=,ppid=,uid=,lstart=", env={"LC_ALL": "C", "TZ": "UTC"})

    def test_missing_or_malformed_process_metadata_is_not_an_empty_success(self):
        for output in ("", "not process metadata", "210 1 501 unknown-start-time"):
            with self.subTest(output=output), patch.object(installer, "run", return_value=completed(stdout=output)), self.assertRaises(RuntimeError):
                installer.process_table()


if __name__ == "__main__":
    unittest.main()
