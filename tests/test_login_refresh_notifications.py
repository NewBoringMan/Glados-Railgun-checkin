import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import Mock

from login_refresh_notifications import NativeNotifications, NotificationOutbox, NotificationFailure


class Sender:
    def __init__(self):self.authorization='authorized';self.sent=[];self.response={'ok':True,'accepted_by_system':True}
    def status(self):return self.authorization
    def send(self,*args):self.sent.append(args);return self.response


class OutboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'test.sqlite'
        self.db=sqlite3.connect(self.path,isolation_level=None);self.sender=Sender();self.now=10000.0
        self.outbox=NotificationOutbox(self.db,self.sender,clock=lambda:self.now)
    def tearDown(self):self.db.close();self.tmp.cleanup()
    def test_duplicate_state_enqueues_once(self):
        one=self.outbox.enqueue('2026-09:A:challenge','account_manual')
        self.assertEqual(one,self.outbox.enqueue('2026-09:A:challenge','account_manual'))
        self.assertEqual(self.outbox.status()['pending'],1)
    def test_notification_has_no_email_or_secret_content(self):
        self.outbox.enqueue('private@example.com:credential-fragment','account_manual')
        self.outbox.flush();serialized=json.dumps(self.sender.sent)
        self.assertNotIn('private@example.com',serialized);self.assertNotIn('credential-fragment',serialized)
    def test_denied_permission_preserves_pending_notice(self):
        self.sender.authorization='denied';self.outbox.enqueue('x','account_manual')
        self.assertEqual(self.outbox.flush()['accepted'],0);self.assertEqual(self.outbox.status()['pending'],1)
        self.assertEqual(self.sender.sent,[])
    def test_not_determined_never_prompts_or_sends(self):
        self.sender.authorization='not_determined';self.outbox.enqueue('x','account_manual');self.outbox.flush()
        self.assertEqual(self.sender.sent,[])
    def test_later_authorization_delivers_pending(self):
        self.sender.authorization='denied';self.outbox.enqueue('x','account_manual');self.outbox.flush()
        self.sender.authorization='authorized';self.assertEqual(self.outbox.flush()['accepted'],1)
        self.assertEqual(self.outbox.status()['pending'],0)
    def test_accepted_by_system_is_recorded_but_not_claimed_seen(self):
        self.outbox.enqueue('x','account_manual');r=self.outbox.flush()
        self.assertEqual(r['accepted'],1);self.assertNotIn('delivered',r);self.assertNotIn('seen',r)
    def test_accepted_notice_not_repeated_after_restart(self):
        self.outbox.enqueue('x','account_manual');self.outbox.flush();self.db.close()
        self.db=sqlite3.connect(self.path,isolation_level=None)
        self.outbox=NotificationOutbox(self.db,self.sender,clock=lambda:self.now)
        self.outbox.enqueue('x','account_manual');self.outbox.flush()
        self.assertEqual(len(self.sender.sent),1)
    def test_unknown_delivery_failure_retained_without_raw_message(self):
        self.sender.response={'ok':False,'reason':'SECRET'};self.outbox.enqueue('x','account_manual');self.outbox.flush()
        row=self.db.execute('SELECT accepted_at,last_error FROM refresh_notices').fetchone()
        self.assertIsNone(row[0]);self.assertEqual(row[1],'notification_service_unavailable')
    def test_service_failure_has_bounded_retry(self):
        self.sender.response={'ok':False,'reason':'notification_service_unavailable'}
        self.outbox.enqueue('x','account_manual');self.outbox.flush();self.outbox.flush()
        self.assertEqual(len(self.sender.sent),1)
        self.now+=301;self.outbox.flush();self.assertEqual(len(self.sender.sent),2)
    def test_delivery_exception_does_not_lose_notice(self):
        self.sender.send=Mock(side_effect=OSError('SECRET'))
        self.outbox.enqueue('x','account_manual');self.outbox.flush()
        self.assertEqual(self.outbox.status()['pending'],1)
    def test_partial_success_does_not_ack_other_pending_notices(self):
        self.outbox.enqueue('one','account_manual');self.outbox.enqueue('two','shared_dependency')
        self.outbox.flush(limit=1);self.assertEqual(self.outbox.status()['pending'],1)
    def test_three_attempt_failure_creates_actionable_notice(self):
        self.db.execute('CREATE TABLE jobs(cycle TEXT,account_key TEXT,phase TEXT,reason TEXT,attempts INTEGER)')
        self.db.execute("INSERT INTO jobs VALUES('2026-09','A','manual','mail_timeout',3)")
        self.outbox.observe_jobs();self.outbox.observe_jobs();self.assertEqual(self.outbox.status()['pending'],1)
    def test_challenge_notifies_without_requiring_three_failures(self):
        self.db.execute('CREATE TABLE jobs(cycle TEXT,account_key TEXT,phase TEXT,reason TEXT,attempts INTEGER)')
        self.db.execute("INSERT INTO jobs VALUES('2026-09','A','manual','challenge',1)")
        self.outbox.observe_jobs();self.assertEqual(self.outbox.status()['pending'],1)
    def test_missing_email_does_not_spam_on_every_scan(self):
        self.db.execute('CREATE TABLE jobs(cycle TEXT,account_key TEXT,phase TEXT,reason TEXT,attempts INTEGER)')
        self.db.execute("INSERT INTO jobs VALUES('2026-09','A','manual','missing_identity',0)")
        self.outbox.observe_jobs();self.assertEqual(self.outbox.status()['pending'],0)
    def test_retrying_account_is_not_terminal_notification(self):
        self.db.execute('CREATE TABLE jobs(cycle TEXT,account_key TEXT,phase TEXT,reason TEXT,attempts INTEGER)')
        self.db.execute("INSERT INTO jobs VALUES('2026-09','A','queued','mail_timeout',1)")
        self.outbox.observe_jobs();self.assertEqual(self.outbox.status()['pending'],0)
    def test_shared_failure_deduplicated_per_day(self):
        self.outbox.shared_failure('mail_auth_required');self.outbox.shared_failure('mail_auth_required')
        self.assertEqual(self.outbox.status()['pending'],1)
        self.now+=86400;self.outbox.shared_failure('mail_auth_required');self.assertEqual(self.outbox.status()['pending'],2)
    def test_unrecognized_exception_not_used_as_notification(self):
        self.outbox.shared_failure('cookie=SECRET');self.assertEqual(self.outbox.status()['pending'],0)
    def test_receipt_ambiguity_creates_verification_notice(self):
        self.outbox.shared_failure('publication_receipt_required')
        self.assertEqual(self.db.execute('SELECT kind FROM refresh_notices').fetchone()[0],'verification_pending')
    def test_invalid_notice_parameters_are_rejected(self):
        for scope,kind,count in [('', 'test',1),('x','other',1),('x','test',True),('x','test',501)]:
            with self.assertRaises(NotificationFailure):self.outbox.enqueue(scope,kind,count)
    def test_two_flushers_do_not_deliver_same_reservation(self):
        self.outbox.enqueue('x','account_manual')
        otherdb=sqlite3.connect(self.path,isolation_level=None)
        other=NotificationOutbox(otherdb,Sender(),clock=lambda:self.now)
        original=self.sender.send
        def send(*args):
            self.assertEqual(other.flush()['accepted'],0)
            return original(*args)
        self.sender.send=send
        try:self.outbox.flush()
        finally:otherdb.close()
        self.assertEqual(len(self.sender.sent),1)


class NativePipeTests(unittest.TestCase):
    def test_missing_helper_returns_unavailable_without_launch(self):
        run=Mock();sender=NativeNotifications('/nonexistent/RefreshNotifications',run=run)
        self.assertEqual(sender.status(),'unavailable');run.assert_not_called()
    def test_request_is_stdin_not_shell_arguments(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'RefreshNotifications';path.write_text('fixture');path.chmod(0o700)
            run=Mock(return_value=subprocess.CompletedProcess([],0,b'{"ok":true,"authorization":"authorized"}',b''))
            sender=NativeNotifications(path,run=run);self.assertEqual(sender.status(),'authorized')
            self.assertEqual(run.call_args.args[0],[str(path)]);self.assertEqual(json.loads(run.call_args.kwargs['input']),{'op':'status'})
    def test_unsafe_helper_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'RefreshNotifications';path.write_text('fixture');path.chmod(0o777)
            run=Mock();self.assertEqual(NativeNotifications(path,run=run).status(),'unavailable');run.assert_not_called()
    def test_invalid_helper_response_does_not_escape(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'RefreshNotifications';path.write_text('fixture');path.chmod(0o700)
            run=Mock(return_value=subprocess.CompletedProcess([],0,b'SECRET',b''))
            self.assertEqual(NativeNotifications(path,run=run).status(),'unavailable')


if __name__=='__main__':unittest.main()
