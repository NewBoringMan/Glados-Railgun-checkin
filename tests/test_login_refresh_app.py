"""App/runner/scheduler integration tests with synthetic accounts and zero network."""
import copy
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import plistlib
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from login_refresh_app import AppService, AccountRepository, public_error
from login_refresh_core import RefreshError
from login_refresh_runner import RefreshRunner, GitHubCloud, state_digest
from login_refresh_schedule import LocalSchedule, LABEL

A='A'*16; B='B'*16; NOW=datetime(2026,9,29,12,0,tzinfo=timezone.utc)
EMAIL='alice@example.com'; BOX='codes@example.net'
REGISTRY={'accounts':{A:{'label':'Example A','enabled':True,'autoExchange':True},B:{'label':'Example B','enabled':True,'autoExchange':True}},
          'policies':{'version':1,'default':'auto','accounts':{B:'plan200'}}}


class Repository:
    def __init__(self):self.snapshot=copy.deepcopy(REGISTRY);self.fail=False
    def load(self,allow_cache=False):
        if self.fail and not allow_cache:raise RefreshError('github_unavailable')
        return copy.deepcopy(self.snapshot),'last_saved' if self.fail else 'github'


class Secrets:
    def __init__(self):self.values={};self.writes=[]
    def get(self,key):return copy.deepcopy(self.values.get(key))
    def put(self,key,value):self.values[key]=copy.deepcopy(value);self.writes.append(key)
    def delete(self,key):self.values.pop(key,None)


class FakeCloud:
    def __init__(self):self.uploads=[];self.runs=[];self.verify=True;self.error=None;self.version='f'*40
    def existing_secret(self,key):return 'GLADOS_ACCOUNT_'+key
    def head(self):return self.version
    def upload(self,key,cookie):
        self.uploads.append((key,cookie))
        if self.error:raise self.error
    def dispatch_status(self,key):self.runs.append(key);return 123
    def verify_status(self,*args):return self.verify


class FakeLogin:
    def __init__(self,key=A):
        self.key=key;self.calls=[];self.error=None;self.closed=False
        self.transport=SimpleNamespace(close=self.close)
    def prepare_delivery(self):self.calls.append('prepare')
    def check_delivery(self):self.calls.append('check_mail')
    def request_code(self,attempt):
        self.calls.append('request')
        if self.error:raise self.error
    def submit(self,code,**kwargs):
        self.calls.append('submit')
        return SimpleNamespace(account_key=self.key,email=EMAIL,authorization=None,cookie_header='koa:sess=synthetic; koa:sess.sig=signature;')
    def close(self):self.closed=True


class FakeGmail:
    mailbox=BOX
    def __init__(self):self.calls=0;self.fail=False
    def verify_mailbox(self):
        if self.fail:raise RefreshError('mail_auth_required')
    def message_ids(self,**kwargs):self.calls+=1;return () if self.calls==1 else ('fake-message',)
    def read_message(self,mid):return 'synthetic-envelope'


class AppTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.repo=Repository();self.secret=Secrets()
        self.app=AppService(self.root,self.repo,self.secret,mail_probe=lambda:'mail-instance')
    def tearDown(self):self.app.close();self.temp.cleanup()
    def test_failed_cache_does_not_erase_identity(self):
        self.app.store.remember_verified_identity(A,EMAIL,NOW)
        (self.root/'status-cache.json').write_text(json.dumps([{'account_key':A,'ok':False,'email':''}]))
        snap=self.app.snapshot();row=next(r for r in snap['accounts'] if r['key']==A)
        self.assertEqual(row['email'],EMAIL);self.assertFalse(row['last_status_ok'])
    def test_unknown_email_can_be_saved_only_as_pending(self):
        self.app.save_email(A,EMAIL)
        row=next(r for r in self.app.snapshot()['accounts'] if r['key']==A)
        self.assertEqual(row['identity_kind'],'pending');self.assertIsNone(self.app.store.identity(A));self.assertEqual(self.secret.writes,[])
    def test_confirmed_identity_cannot_be_overwritten(self):
        self.app.store.remember_verified_identity(A,EMAIL,NOW)
        with self.assertRaisesRegex(RefreshError,'read_only_identity'):self.app.save_email(A,'wrong@example.com')
    def test_pending_duplicate_email_rejected(self):
        self.app.save_email(A,EMAIL)
        with self.assertRaisesRegex(RefreshError,'duplicate_identity'):self.app.save_email(B,EMAIL)
    def test_email_for_removed_account_rejected(self):
        with self.assertRaisesRegex(RefreshError,'unknown_account'):self.app.save_email('C'*16,EMAIL)
    def test_no_email_edit_from_stale_registry(self):
        self.repo.fail=True
        with self.assertRaisesRegex(RefreshError,'github_unavailable'):self.app.save_email(A,EMAIL)
    def test_offline_snapshot_keeps_saved_emails(self):
        self.app.store.remember_verified_identity(A,EMAIL,NOW);self.repo.fail=True
        self.assertEqual(self.app.snapshot()['confirmed_count'],1)
    def test_expired_secret_does_not_mean_accepted_automation(self):
        self.assertFalse(self.app.snapshot()['automation_ready']);self.assertFalse(self.app.snapshot()['schedule_enabled'])
    def test_replacing_oauth_client_invalidates_displayed_authorization(self):
        self.app.configure_mailbox(BOX)
        self.secret.values={'gmail-client':{'client_id':'new'},'gmail-token':{'client_id':'old','mailbox':BOX}}
        self.assertFalse(self.app.snapshot()['gmail']['authorized'])
    def test_sensitive_error_not_exposed(self):
        self.assertNotIn('SECRET',json.dumps(public_error(RuntimeError('SECRET'))))
    def test_exchange_policies_unchanged_by_snapshot_or_email_save(self):
        before=copy.deepcopy(self.repo.snapshot)
        self.app.snapshot();self.app.save_email(A,EMAIL);self.assertEqual(self.repo.snapshot,before)
    def test_monthly_requires_real_acceptance(self):
        with self.assertRaisesRegex(RefreshError,'live_acceptance_required'):self.app.set_monthly(True)
    def test_disabled_tick_never_starts_login(self):
        result=self.app.dispatch({'action':'scheduled_tick'});self.assertEqual(result['event'],'schedule_disabled')
        self.assertEqual(self.app.store.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0],0)
    def test_snapshot_allowed_while_queue_has_lock(self):
        with self.app.store.exclusive_run():
            self.assertEqual(len(self.app.snapshot()['accounts']),2)


class RunnerTests(AppTests):
    def setUp(self):
        super().setUp();self.app.save_email(A,EMAIL)
        self.cloud=FakeCloud();self.gmail=FakeGmail();self.login=FakeLogin();self.current=NOW
        self.runner=RefreshRunner(self.app,cloud=self.cloud,gmail=self.gmail,login_factory=lambda:self.login,
                                  clock=lambda:self.current,sleep=self.sleep)
    def sleep(self,seconds):self.current+=timedelta(seconds=seconds)
    def run_success(self,key=A,batch=False):
        with patch('login_refresh_runner.select_code',return_value=object()):return self.runner.one(key,batch=batch)
    def test_full_single_account_sequence_and_cloud_acceptance(self):
        result=self.run_success();self.assertEqual(result['done'],1);self.assertEqual(len(self.cloud.uploads),1)
        self.assertEqual(self.cloud.runs,[A]);self.assertTrue(self.runner.accepted());self.assertTrue(self.login.closed)
        self.assertEqual(self.app.store.identity(A)['email'],EMAIL);self.assertNotIn('candidate-'+A,self.secret.values)
        self.assertIn('active-'+A,self.secret.values)
    def test_batch_cannot_bypass_initial_acceptance(self):
        with self.assertRaisesRegex(RefreshError,'live_acceptance_required'):self.runner.one(batch=True)
        self.assertEqual(self.login.calls,[])
    def test_gmail_failure_precedes_any_code_request(self):
        self.gmail.fail=True
        with self.assertRaisesRegex(RefreshError,'mail_auth_required'):self.runner.one(A)
        self.assertNotIn('request',self.login.calls)
        row=self.app.store.job('2026-09',A);self.assertEqual(row['attempts'],0);self.assertEqual(row['phase'],'queued')
    def test_mail_failure_precedes_code_request(self):
        self.login.prepare_delivery=lambda:(_ for _ in ()).throw(RefreshError('mail_background_start_unavailable'))
        with self.assertRaisesRegex(RefreshError,'mail_background_start_unavailable'):self.runner.one(A)
        self.assertNotIn('request',self.login.calls);self.assertEqual(self.app.store.job('2026-09',A)['attempts'],0)
    def test_challenge_stops_one_account_no_retry(self):
        self.login.error=RefreshError('challenge')
        with self.assertRaisesRegex(RefreshError,'challenge'):self.runner.one(A)
        row=self.app.store.job('2026-09',A);self.assertEqual(row['phase'],'manual');self.assertEqual(row['attempts'],1)
        self.runner.one(A);self.assertEqual(self.login.calls.count('request'),1);self.assertEqual(self.cloud.uploads,[])
    def test_challenge_hold_survives_next_month(self):
        self.login.error=RefreshError('challenge')
        with self.assertRaises(RefreshError):self.runner.one(A)
        self.current+=timedelta(days=35);self.runner.one(A)
        self.assertEqual(self.login.calls.count('request'),1)
    def test_identity_mismatch_never_uploads(self):
        self.login.key=B
        with self.assertRaisesRegex(RefreshError,'identity_mismatch'):self.run_success()
        self.assertEqual(self.cloud.uploads,[]);self.assertIsNone(self.app.store.identity(A))
    def test_other_queued_account_not_selected_by_single_button(self):
        self.app.save_email(B,'bob@example.com')
        self.app.store.db.execute("INSERT INTO jobs(cycle,account_key,due) VALUES('2026-09',?,?)",(B,NOW.timestamp()))
        self.run_success(A);self.assertEqual([x[0] for x in self.cloud.uploads],[A])
    def test_other_active_account_not_silently_resumed(self):
        self.app.store.db.execute("INSERT INTO jobs(cycle,account_key,phase,due) VALUES('2026-09',?,'preflight',?)",(B,NOW.timestamp()))
        with self.assertRaisesRegex(RefreshError,'unfinished_job_requires_resume'):self.runner.one(A)
        self.assertEqual(self.login.calls,[])
    def test_success_is_not_repeated_same_month(self):
        self.run_success();self.runner.one(A);self.assertEqual(self.login.calls.count('request'),1)
    def test_cloud_upload_timeout_retains_candidate(self):
        self.cloud.error=RefreshError('cloud_operation_uncertain')
        with self.assertRaisesRegex(RefreshError,'cloud_operation_uncertain'):self.run_success()
        self.assertEqual(self.app.store.job('2026-09',A)['phase'],'publish_pending')
        self.assertIn('candidate-'+A,self.secret.values);self.assertFalse(self.runner.accepted())
    def test_ambiguous_upload_reconciles_without_relogin_or_second_upload(self):
        self.cloud.error=RefreshError('cloud_operation_uncertain')
        with self.assertRaises(RefreshError):self.run_success()
        self.cloud.error=None;self.runner.one(A)
        self.assertEqual(self.login.calls.count('request'),1);self.assertEqual(len(self.cloud.uploads),1)
        self.assertTrue(self.runner.accepted())
    def test_cloud_still_running_retains_verify_phase(self):
        self.cloud.verify=False;result=self.run_success()
        self.assertEqual(result['event'],'verification_pending');self.assertFalse(self.runner.accepted())
        self.assertEqual(self.app.store.job('2026-09',A)['phase'],'verify_pending')
    def test_cloud_status_busy_does_not_relogin(self):
        self.cloud.dispatch_status=lambda key:(_ for _ in ()).throw(RefreshError('cloud_status_busy'))
        with self.assertRaisesRegex(RefreshError,'cloud_status_busy'):self.run_success()
        self.assertEqual(self.login.calls.count('request'),1)
        self.assertFalse(self.runner._meta('2026-09',A)['dispatch_uncertain'])
    def test_state_change_blocks_credential_publication(self):
        original=self.login.submit
        def submit(*args,**kwargs):
            value=original(*args,**kwargs);self.repo.snapshot['policies']['accounts'][A]='plan200';return value
        self.login.submit=submit
        with self.assertRaisesRegex(RefreshError,'production_changed_recheck_required'):self.run_success()
        self.assertEqual(self.cloud.uploads,[])
    def test_no_code_causes_only_one_attempt(self):
        with patch('login_refresh_runner.select_code',return_value=None):self.runner.one(A)
        row=self.app.store.job('2026-09',A);self.assertEqual(row['attempts'],1);self.assertEqual(row['phase'],'queued')
        self.assertEqual(self.login.calls.count('request'),1)
    def test_interrupted_wait_is_not_replayed(self):
        self.login.error=RefreshError('network_unavailable')
        with self.assertRaises(RefreshError):self.runner.one(A)
        self.runner.one(A);self.assertEqual(self.login.calls.count('request'),1)
    def test_runtime_metadata_has_no_credential_values(self):
        self.run_success();value=json.dumps(self.runner._meta('2026-09',A))
        self.assertNotIn('synthetic',value);self.assertNotIn('cookie',value);self.assertNotIn('signature',value)


class CloudTests(unittest.TestCase):
    def test_secret_value_is_only_stdin(self):
        calls=[]
        def run(args,**kwargs):
            calls.append((args,kwargs));result=b'[{"name":"GLADOS_ACCOUNT_'+A.encode()+b'"}]' if 'list' in args else b''
            return subprocess.CompletedProcess(args,0,result,b'')
        cloud=GitHubCloud(run);cookie='koa:sess=synthetic; koa:sess.sig=signature;';cloud.upload(A,cookie)
        self.assertNotIn(cookie,str(calls[-1][0]));self.assertEqual(calls[-1][1]['input'],cookie.encode())
    def test_no_secret_is_not_recreated(self):
        cloud=GitHubCloud(lambda args,**kw:subprocess.CompletedProcess(args,0,b'[]',b''))
        with self.assertRaisesRegex(RefreshError,'preexisting_secret_required'):cloud.upload(A,'koa:sess=x; koa:sess.sig=y;')
    def test_stale_run_is_rejected_before_log_fetch(self):
        cloud=GitHubCloud();cloud.api=lambda *a,**k:{'created_at':'2026-01-01T00:00:00Z','head_sha':'f'*40,'head_branch':'master','event':'workflow_dispatch'}
        with self.assertRaisesRegex(RefreshError,'cloud_verification_failed'):cloud.verify_status(123,A,EMAIL,'f'*40,NOW.timestamp())
    def test_running_other_status_job_not_cancelled(self):
        cloud=GitHubCloud();calls=[]
        def api(*a,**k):calls.append(a);return {'workflow_runs':[{'status':'in_progress'}]}
        cloud.api=api
        with self.assertRaisesRegex(RefreshError,'cloud_status_busy'):cloud.dispatch_status(A)
        self.assertEqual(len(calls),1)


class ScheduleTests(unittest.TestCase):
    def test_only_installed_bundle_can_produce_job_spec(self):
        with tempfile.TemporaryDirectory() as d:
            contents=Path(d)/'app/Contents';resource=contents/'Resources/LoginRefresh';resource.mkdir(parents=True)
            (resource/'login_refresh_app.py').write_text('# fixture')
            (contents/'Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier':'com.enoch.glados-account-center','GLaDOSRefreshPython':'/opt/homebrew/bin/python3'}))
            scheduler=LocalSchedule(contents,home=Path(d));spec=scheduler.spec()
            self.assertEqual(spec['Label'],LABEL);self.assertEqual(spec['ProgramArguments'][-1],'--tick')
            self.assertNotIn('open',spec['ProgramArguments']);self.assertEqual(spec['StandardOutPath'],'/dev/null')
    def test_uninstalled_source_rejected_without_mutation(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(RefreshError,'installed_bundle_required'):LocalSchedule(Path(d),home=Path(d)).spec()
            self.assertFalse((Path(d)/'Library/LaunchAgents').exists())


if __name__=='__main__':unittest.main()
