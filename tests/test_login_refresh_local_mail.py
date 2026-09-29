import base64
from datetime import datetime,timedelta,timezone
from email.message import EmailMessage
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from login_refresh_core import RefreshError,LoginAttempt,parse_candidate
from login_refresh_local_mail import LocalMailInbox
NOW=datetime(2026,9,30,4,0,tzinfo=timezone.utc)
BOX='codes@example.net'; TARGET='person@example.com'; MID='mail:42'

class FakeNative:
    def __init__(self):self.calls=[];self.response=None;self.code=0
    def __call__(self,args,**kwargs):
        req=json.loads(kwargs['input']);self.calls.append((args,req,kwargs));data=self.response
        if data is None:
            if req['op'] in {'permission','authorize'}:data={'ok':True,'permission':'granted'}
            elif req['op']=='verify':data={'ok':True,'mailbox':BOX,'permission':'granted','inbox_found':True}
            elif req['op']=='list':data={'ok':True,'mailbox':BOX,'message_ids':[MID]}
            elif req['op']=='check':data={'ok':True,'check_requested':True,'delivery_complete':False}
        return subprocess.CompletedProcess(args,self.code,json.dumps(data).encode(),b'')

def fixture():
    m=EmailMessage();m['From']=TARGET;m['To']=BOX;m['Subject']='Fwd: GLaDOS Authentication Code'
    m['Authentication-Results']='mx.google.com; dkim=pass header.i=@example.com; dmarc=pass header.from=example.com'
    issued=(NOW-timedelta(seconds=30)).strftime('%a, %d %b %Y %H:%M:%S +0000')
    m.set_content(f'From: GLaDOS <noreply@glados.network>\nTo: {TARGET}\nDate: {issued}\nSubject: GLaDOS Authentication Code\n\nYour verification code is: 001234\n')
    return m.as_bytes()

class LocalMailTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'LocalMailReader'
        self.path.write_text('fixture');self.path.chmod(0o700)
        self.run=FakeNative();self.reader=LocalMailInbox(BOX,self.path,run=self.run,clock=lambda:NOW)
    def tearDown(self):self.tmp.cleanup()
    def raw_response(self,raw=None):
        self.run.response={'ok':True,'mailbox':BOX,'message_id':MID,'source_base64':base64.b64encode(raw or fixture()).decode(),'received_at':(NOW-timedelta(seconds=20)).timestamp()}
    def test_verify_selected_mailbox_without_oauth(self):
        self.reader.verify_mailbox();self.assertEqual(self.run.calls[0][1],{'op':'verify','mailbox':BOX});self.assertEqual(self.run.calls[0][0],[str(self.path)])
    def test_permission_query_does_not_authorize(self):
        self.assertEqual(self.reader.permission(),'granted');self.assertEqual(self.run.calls[0][1],{'op':'permission'})
    def test_explicit_authorization_then_mailbox_check(self):
        self.reader.authorize();self.assertEqual([x[1]['op'] for x in self.run.calls],['authorize','verify'])
    def test_denial_is_not_retried(self):
        self.run.response={'ok':True,'permission':'denied'}
        with self.assertRaisesRegex(RefreshError,'mail_permission_required'):self.reader.authorize()
        self.assertEqual(len(self.run.calls),1)
    def test_closed_mail_does_not_fall_back_to_osascript_or_google(self):
        self.run.response={'ok':False,'reason':'mail_not_running'};self.run.code=1
        with self.assertRaisesRegex(RefreshError,'mail_not_running'):self.reader.verify_mailbox()
        self.assertEqual(len(self.run.calls),1)
    def test_wrong_selected_account_rejected(self):
        self.run.response={'ok':True,'mailbox':'wrong@example.net','permission':'granted','inbox_found':True}
        with self.assertRaisesRegex(RefreshError,'wrong_mailbox'):self.reader.verify_mailbox()
    def test_missing_account_is_explicit(self):
        self.run.response={'ok':False,'reason':'mail_receiver_not_found'};self.run.code=1
        with self.assertRaisesRegex(RefreshError,'mail_receiver_not_found'):self.reader.verify_mailbox()
    def test_ids_are_native_message_ids(self):
        self.assertEqual(self.reader.message_ids(after=NOW-timedelta(minutes=2)),(MID,));self.assertEqual(self.run.calls[0][1]['op'],'list')
    def test_truncated_window_rejected(self):
        self.run.response={'ok':True,'mailbox':BOX,'message_ids':[MID,'mail:43']}
        with self.assertRaisesRegex(RefreshError,'mail_window_too_large'):self.reader.message_ids(after=NOW,limit=1)
    def test_duplicate_ids_removed(self):
        self.run.response={'ok':True,'mailbox':BOX,'message_ids':[MID,MID]};self.assertEqual(self.reader.message_ids(after=NOW),(MID,))
    def test_message_id_path_injection_blocked_before_call(self):
        with self.assertRaisesRegex(RefreshError,'invalid_mail_id'):self.reader.read_message('../secret')
        self.assertEqual(self.run.calls,[])
    def test_wrong_reply_id_rejected(self):
        self.raw_response();self.run.response['message_id']='mail:99'
        with self.assertRaisesRegex(RefreshError,'mail_id_mismatch'):self.reader.read_message(MID)
    def test_no_entire_mailbox_historical_scan(self):
        with self.assertRaisesRegex(RefreshError,'invalid_mail_window'):self.reader.message_ids(after=NOW-timedelta(days=2))
        self.assertEqual(self.run.calls,[])
    def test_raw_received_time_is_not_original_issue_time(self):
        self.raw_response();result=self.reader.read_message(MID);self.assertEqual(result.received_at,NOW-timedelta(seconds=20))
        attempt=LoginAttempt('attempt',TARGET,BOX,NOW-timedelta(minutes=1),allowed_forwarders=frozenset({TARGET}))
        candidate=parse_candidate(result,attempt,NOW);self.assertEqual(candidate.code,'001234');self.assertEqual(candidate.issued_at,NOW-timedelta(seconds=30))
    def test_old_forwarded_code_still_rejected_by_original_date(self):
        raw=fixture().replace((NOW-timedelta(seconds=30)).strftime('%a, %d %b %Y %H:%M:%S +0000').encode(),(NOW-timedelta(days=1)).strftime('%a, %d %b %Y %H:%M:%S +0000').encode())
        self.raw_response(raw);attempt=LoginAttempt('attempt',TARGET,BOX,NOW-timedelta(minutes=1),allowed_forwarders=frozenset({TARGET}))
        with self.assertRaisesRegex(RefreshError,'not_issued_for_attempt'):parse_candidate(self.reader.read_message(MID),attempt,NOW)
    def test_missing_transport_authentication_not_invented(self):
        raw=fixture().replace(b'Authentication-Results:',b'X-Untrusted-Authentication-Results:');self.raw_response(raw);self.assertIsNone(self.reader.read_message(MID).authenticated_sender)
    def test_source_omitted_from_repr(self):
        self.raw_response();self.assertNotIn('001234',repr(self.reader.read_message(MID)))
    def test_invalid_base64_rejected(self):
        self.raw_response();self.run.response['source_base64']='bad!'
        with self.assertRaisesRegex(RefreshError,'invalid_mail_source'):self.reader.read_message(MID)
    def test_bool_timestamp_rejected(self):
        self.raw_response();self.run.response['received_at']=True
        with self.assertRaisesRegex(RefreshError,'invalid_mail_source'):self.reader.read_message(MID)
    def test_oversized_source_rejected(self):
        self.raw_response();self.run.response['source_base64']='x'*(512*1024*4//3+9)
        with self.assertRaisesRegex(RefreshError,'invalid_mail_source'):self.reader.read_message(MID)
    def test_check_mail_is_not_claimed_as_delivery(self):
        self.reader.check_new_mail();self.assertEqual(self.run.calls[0][1]['op'],'check')
    def test_timeout_hides_native_output(self):
        def fail(*args,**kwargs):raise subprocess.TimeoutExpired('private-data',2)
        self.reader.run=fail
        with self.assertRaisesRegex(RefreshError,'^mail_data_unavailable$'):self.reader.verify_mailbox()
    def test_unsafe_executable_rejected(self):
        self.path.chmod(0o777)
        with self.assertRaisesRegex(RefreshError,'mail_reader_unsafe_permissions'):self.reader.verify_mailbox()
        self.assertEqual(self.run.calls,[])
    def test_symlink_executable_rejected(self):
        link=self.path.with_name('link');link.symlink_to(self.path)
        with self.assertRaisesRegex(RefreshError,'mail_reader_unavailable'):LocalMailInbox(BOX,link).verify_mailbox()
    def test_unknown_native_error_not_exposed(self):
        self.run.response={'ok':False,'reason':'secret-code-001234'};self.run.code=1
        with self.assertRaisesRegex(RefreshError,'^mail_data_unavailable$'):self.reader.verify_mailbox()

if __name__=='__main__':unittest.main()
