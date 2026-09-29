"""Mail lifecycle tests; no real apps launched, closed, or brought to foreground."""
import subprocess
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from login_refresh_core import LoginAttempt, RefreshError
from login_refresh_http import GladosEmailLogin, JSONResponse
from login_refresh_mail import MAIL_EXECUTABLE, MailForwardingGate, mac_mail_instance


class Clock:
    def __init__(self): self.now = 0.0
    def time(self): return self.now
    def sleep(self, seconds): self.now += seconds


class MailGateTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.instance = 'mail-process-1'
        self.launch = Mock(return_value=True)

    def gate(self, **kwargs):
        config = dict(probe=lambda: self.instance, open_background=self.launch,
                      clock=self.clock.time, sleep=self.clock.sleep,
                      warmup_seconds=2, timeout_seconds=10, max_gap_seconds=5)
        config.update(kwargs)
        return MailForwardingGate(**config)

    def test_running_mail_is_reused_without_launch(self):
        g=self.gate();g.prepare();g.require_running()
        self.launch.assert_not_called()
        self.assertEqual(self.clock.now,2)

    def test_prepare_required_even_if_process_happens_to_exist(self):
        with self.assertRaisesRegex(RefreshError,'mail_preflight_required'):
            self.gate().require_running()

    def test_closed_mail_starts_once_through_host_hook_then_reprobes(self):
        self.instance=None
        def launch(): self.instance='mail-process-2';return True
        self.launch.side_effect=launch
        g=self.gate();g.prepare();g.require_running();g.prepare()
        self.launch.assert_called_once()

    def test_closed_mail_without_safe_launch_hook_pauses(self):
        self.instance=None;g=self.gate(open_background=None)
        with self.assertRaisesRegex(RefreshError,'mail_background_start_unavailable'):g.prepare()

    def test_foreground_only_launch_denied_without_fallback(self):
        self.instance=None;self.launch.return_value=False;g=self.gate()
        with self.assertRaisesRegex(RefreshError,'mail_background_start_denied'):g.prepare()
        self.launch.assert_called_once()

    def test_truthy_non_boolean_launch_result_is_not_success(self):
        self.instance=None;self.launch.return_value={'success':True};g=self.gate()
        with self.assertRaisesRegex(RefreshError,'mail_background_start_denied'):g.prepare()

    def test_failed_start_is_not_repeated(self):
        self.instance=None;self.launch.side_effect=RuntimeError('private details');g=self.gate()
        with self.assertRaisesRegex(RefreshError,'^mail_background_start_failed$'):g.prepare()
        with self.assertRaisesRegex(RefreshError,'mail_start_already_attempted'):g.prepare()
        self.launch.assert_called_once()

    def test_launch_success_without_running_process_times_out(self):
        self.instance=None;g=self.gate()
        with self.assertRaisesRegex(RefreshError,'mail_start_timeout'):g.prepare()
        self.launch.assert_called_once()
        self.assertLessEqual(self.clock.now,10)

    def test_user_quit_during_wait_pauses_without_reopening(self):
        g=self.gate();g.prepare();self.instance=None
        with self.assertRaisesRegex(RefreshError,'mail_stopped_resume_required'):g.require_running()
        with self.assertRaisesRegex(RefreshError,'mail_stopped_resume_required'):g.prepare()
        self.launch.assert_not_called()

    def test_process_restart_invalidates_ready_state(self):
        g=self.gate();g.prepare();self.instance='mail-process-2'
        with self.assertRaisesRegex(RefreshError,'mail_stopped_resume_required'):g.require_running()
        g.prepare();g.require_running()
        self.launch.assert_not_called()

    def test_user_can_reopen_then_explicitly_resume(self):
        g=self.gate();g.prepare();self.instance=None
        with self.assertRaises(RefreshError):g.require_running()
        self.instance='mail-process-2';g.prepare();g.require_running()
        self.launch.assert_not_called()

    def test_sleep_or_stale_lease_requires_fresh_preparation(self):
        g=self.gate();g.prepare();self.clock.now+=6
        with self.assertRaisesRegex(RefreshError,'mail_preflight_stale'):g.require_running()
        g.prepare();g.require_running()

    def test_sleep_during_initial_warmup_is_not_ready(self):
        def suspend(seconds):self.clock.now+=8
        g=self.gate(sleep=suspend)
        with self.assertRaisesRegex(RefreshError,'mail_preflight_interrupted'):g.prepare()
        with self.assertRaisesRegex(RefreshError,'mail_preflight_required'):g.require_running()

    def test_clock_reversal_invalidates(self):
        g=self.gate();g.prepare();self.clock.now=-1
        with self.assertRaisesRegex(RefreshError,'mail_preflight_stale'):g.require_running()

    def test_probe_error_pauses_without_open_attempt(self):
        p=Mock(side_effect=RuntimeError('do not expose detail'));g=self.gate(probe=p)
        with self.assertRaisesRegex(RefreshError,'^mail_probe_unavailable$'):g.prepare()
        self.launch.assert_not_called()

    def test_probe_unknown_not_treated_as_absent_or_ready(self):
        g=self.gate(probe=lambda:False)
        with self.assertRaisesRegex(RefreshError,'mail_probe_unavailable'):g.prepare()
        self.launch.assert_not_called()

    def test_repeated_checks_do_not_repeat_warmup(self):
        g=self.gate();g.prepare();first=self.clock.now
        for _ in range(3):g.require_running();g.prepare()
        self.assertEqual(self.clock.now,first)

    def test_invalid_timer_values_rejected(self):
        for config in [dict(warmup_seconds=-1),dict(timeout_seconds=1),dict(poll_seconds=0),dict(max_gap_seconds=float('nan'))]:
            with self.subTest(config=config):
                with self.assertRaisesRegex(RefreshError,'invalid_mail_preflight_config'):self.gate(**config)

    def test_no_process_closer_is_part_of_guard(self):
        g=self.gate();g.prepare()
        self.assertFalse(hasattr(g,'close_mail'))
        self.assertFalse(hasattr(g,'quit_mail'))


class MailLoginOrderingTests(unittest.TestCase):
    def setUp(self):
        self.state='mail-process'
        self.gate=MailForwardingGate(lambda:self.state,warmup_seconds=0)
        self.transport=Mock()
        self.transport.request.return_value=JSONResponse(200,{'code':0})
        self.client=GladosEmailLogin(self.transport,mail_gate=self.gate)
        self.attempt=LoginAttempt('id','person@example.com','codes@example.net',datetime.now(timezone.utc))

    def test_unprepared_request_sends_nothing_and_consumes_no_attempt(self):
        with self.assertRaisesRegex(RefreshError,'mail_preflight_required'):self.client.request_code(self.attempt)
        self.transport.request.assert_not_called()
        self.assertFalse(self.client._send_started)

    def test_preflight_can_recover_without_resetting_client(self):
        with self.assertRaises(RefreshError):self.client.request_code(self.attempt)
        self.client.prepare_delivery();self.client.request_code(self.attempt)
        self.transport.request.assert_called_once()

    def test_mail_closes_between_baseline_and_request_no_send(self):
        self.client.prepare_delivery();self.state=None
        with self.assertRaisesRegex(RefreshError,'mail_stopped_resume_required'):self.client.request_code(self.attempt)
        self.transport.request.assert_not_called()
        self.assertFalse(self.client._send_started)

    def test_mail_loss_after_send_does_not_replay_or_erase_pending_request(self):
        self.client.prepare_delivery();self.client.request_code(self.attempt);self.state=None
        with self.assertRaisesRegex(RefreshError,'mail_stopped_resume_required'):self.client.check_delivery()
        self.assertTrue(self.client._send_started)
        with self.assertRaisesRegex(RefreshError,'code_request_already_started'):self.client.request_code(self.attempt)
        self.transport.request.assert_called_once()

    def test_default_real_client_requires_explicit_mail_preflight(self):
        client=GladosEmailLogin(self.transport)
        with self.assertRaisesRegex(RefreshError,'mail_preflight_required'):client.request_code(self.attempt)
        self.transport.request.assert_not_called()


class MacProcessProbeTests(unittest.TestCase):
    def test_exact_executable_not_similar_process(self):
        text='100 Tue Sep 29 17:00:00 2026 /tmp/Mail\n200 Tue Sep 29 17:00:00 2026 '+MAIL_EXECUTABLE+'\n'
        with patch('login_refresh_mail.sys.platform','darwin'),patch('login_refresh_mail.subprocess.run',return_value=SimpleNamespace(stdout=text)) as run:
            self.assertEqual(mac_mail_instance(),'200:Tue:Sep:29:17:00:00:2026')
            self.assertEqual(run.call_args.args[0],['/bin/ps','-axo','pid=,lstart=,comm='])

    def test_absent_returns_none(self):
        with patch('login_refresh_mail.sys.platform','darwin'),patch('login_refresh_mail.subprocess.run',return_value=SimpleNamespace(stdout='')):
            self.assertIsNone(mac_mail_instance())

    def test_multiple_mail_instances_fail_closed(self):
        text='100 Tue Sep 29 17:00:00 2026 '+MAIL_EXECUTABLE+'\n200 Tue Sep 29 17:00:00 2026 '+MAIL_EXECUTABLE+'\n'
        with patch('login_refresh_mail.sys.platform','darwin'),patch('login_refresh_mail.subprocess.run',return_value=SimpleNamespace(stdout=text)):
            with self.assertRaisesRegex(RefreshError,'mail_instance_ambiguous'):mac_mail_instance()

    def test_failed_process_query_is_not_mail_closed(self):
        with patch('login_refresh_mail.sys.platform','darwin'),patch('login_refresh_mail.subprocess.run',side_effect=subprocess.TimeoutExpired('ps',5)):
            with self.assertRaisesRegex(RefreshError,'mail_probe_unavailable'):mac_mail_instance()

    def test_nonmac_no_unapproved_alternative(self):
        with patch('login_refresh_mail.sys.platform','linux'),patch('login_refresh_mail.subprocess.run') as run:
            with self.assertRaisesRegex(RefreshError,'mail_probe_unavailable'):mac_mail_instance()
            run.assert_not_called()


if __name__=='__main__':
    unittest.main()
