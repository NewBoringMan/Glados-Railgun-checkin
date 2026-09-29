"""Offline adapter tests. All addresses, IDs, cookies and codes are fictitious."""
import base64
import hashlib
import io
import json
import unittest
import urllib.error
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from unittest.mock import Mock

from login_refresh_core import CodeCandidate, LoginAttempt, RefreshError
from login_refresh_http import (
    GMAIL_ORIGIN, LOGIN_ORIGIN, LOGIN_SITE, GladosEmailLogin as NativeLoginClient, GmailReadOnly,
    HTTPFailure, JSONResponse, NativeJSONTransport, trusted_outer_sender,
)

from login_refresh_mail import MailForwardingGate


def GladosEmailLogin(transport, **kwargs):
    """HTTP-only tests use an explicitly prepared synthetic Mail prerequisite."""
    gate = MailForwardingGate(lambda: 'synthetic-mail-instance', warmup_seconds=0)
    client = NativeLoginClient(transport, mail_gate=gate, **kwargs)
    client.prepare_delivery()
    return client


NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
TARGET = 'person@example.com'
MAILBOX = 'codes@example.net'
UID = '12345'
KEY = hashlib.sha256(('glados-user:' + UID).encode()).hexdigest()[:16].upper()
ATTEMPT = LoginAttempt('test-attempt', TARGET, MAILBOX, NOW-timedelta(minutes=1))
CANDIDATE = CodeCandidate('abc12345', TARGET, NOW-timedelta(seconds=30), 'test-attempt', '001234')


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, *, headers=None, payload=None):
        self.calls.append((method, url, headers, payload))
        if not self.responses:
            raise AssertionError('unexpected request')
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result if isinstance(result, JSONResponse) else JSONResponse(200, result)

    def credential_header(self):
        return 'koa:sess=synthetic; koa:sess.sig=synthetic-signature;'


def good_responses():
    return [
        {'code':0, 'method':'email'},
        {'code':0, 'data':{'userId':UID}},
        {'code':0, 'data':{'email':TARGET, 'userId':UID}},
        {'code':0, 'points':123},
    ]


def raw_mail(auth=None, sender=TARGET):
    m = EmailMessage()
    m['From'] = sender
    m['To'] = MAILBOX
    m['Subject'] = 'Fwd: GLaDOS Authentication Code'
    if auth is not None:
        m['Authentication-Results'] = auth
    m.set_content('No real login information in this synthetic fixture.')
    return m.as_bytes()


VALID_AUTH = ('mx.google.com; dkim=pass header.i=@example.com header.s=test; '
              'dmarc=pass (p=NONE) header.from=example.com')


class LoginAdapterTests(unittest.TestCase):
    def test_successful_native_contract_and_stable_key(self):
        t = FakeTransport(good_responses())
        client = GladosEmailLogin(t)
        client.request_code(ATTEMPT)
        result = client.submit(CANDIDATE, known_account_keys={KEY}, now=NOW)
        self.assertEqual(result.email, TARGET)
        self.assertEqual(result.account_key, KEY)
        self.assertEqual(result.points, 123)
        self.assertEqual(t.calls[0][1], LOGIN_ORIGIN+'/api/authorization')
        self.assertEqual(t.calls[0][3], {'address':TARGET,'site':LOGIN_SITE})
        self.assertEqual(t.calls[1][3], {'method':'email','site':LOGIN_SITE,'email':TARGET,'mailcode':'001234'})
        self.assertFalse(any('/checkin' in c[1] or '/exchange' in c[1] for c in t.calls))

    def test_authorization_is_not_fabricated(self):
        t=FakeTransport([{'code':0}]); c=GladosEmailLogin(t); c.request_code(ATTEMPT)
        self.assertNotIn('Authorization',t.calls[0][2])

    def test_authorized_first_party_context_is_preserved(self):
        authorization='fp1.'+'a'*48
        t=FakeTransport([{'code':0}]); c=GladosEmailLogin(t,authorization=authorization)
        c.request_code(ATTEMPT)
        self.assertEqual(t.calls[0][2]['Authorization'],authorization)

    def test_invalid_authorization_rejected(self):
        with self.assertRaisesRegex(RefreshError,'invalid_first_party_authorization'):
            GladosEmailLogin(FakeTransport([]),authorization='header\r\nInjection: yes')

    def test_captcha_is_terminal_without_retry(self):
        t=FakeTransport([{'code':0,'captcha_required':True,'sitekey':'test'}]); c=GladosEmailLogin(t)
        with self.assertRaisesRegex(HTTPFailure,'challenge'): c.request_code(ATTEMPT)
        with self.assertRaisesRegex(RefreshError,'already_started'): c.request_code(ATTEMPT)
        with self.assertRaisesRegex(RefreshError,'no_accepted'): c.submit(CANDIDATE,known_account_keys={KEY},now=NOW)
        self.assertEqual(len(t.calls),1)

    def test_permission_denied_does_not_probe_other_domains(self):
        t=FakeTransport([{'code':-2,'message':'No permission'}]); c=GladosEmailLogin(t)
        with self.assertRaisesRegex(HTTPFailure,'business_rejected'): c.request_code(ATTEMPT)
        self.assertEqual(len(t.calls),1)

    def test_timeout_is_not_replayed(self):
        t=FakeTransport([HTTPFailure('network_unavailable',ambiguous=True)]);c=GladosEmailLogin(t)
        with self.assertRaises(HTTPFailure) as error: c.request_code(ATTEMPT)
        self.assertTrue(error.exception.ambiguous)
        with self.assertRaisesRegex(RefreshError,'already_started'): c.request_code(ATTEMPT)
        self.assertEqual(len(t.calls),1)

    def test_boolean_success_code_not_accepted(self):
        c=GladosEmailLogin(FakeTransport([{'code':False}]))
        with self.assertRaisesRegex(HTTPFailure,'business_rejected'):c.request_code(ATTEMPT)

    def test_same_candidate_cannot_be_submitted_twice(self):
        t=FakeTransport(good_responses());c=GladosEmailLogin(t);c.request_code(ATTEMPT)
        c.submit(CANDIDATE,known_account_keys={KEY},now=NOW)
        with self.assertRaisesRegex(RefreshError,'already_submitted'):c.submit(CANDIDATE,known_account_keys={KEY},now=NOW)
        self.assertEqual(len(t.calls),4)

    def test_wrong_attempt_or_recipient_does_not_reach_login(self):
        for candidate in [replace(CANDIDATE,attempt_id='other'),replace(CANDIDATE,target_email='other@example.com')]:
            with self.subTest(candidate=candidate.gmail_id):
                t=FakeTransport([{'code':0}]); c=GladosEmailLogin(t);c.request_code(ATTEMPT)
                with self.assertRaisesRegex(RefreshError,'candidate_attempt_mismatch'):
                    c.submit(candidate,known_account_keys={KEY},now=NOW)
                self.assertEqual(len(t.calls),1)

    def test_expired_code_not_submitted(self):
        t=FakeTransport([{'code':0}]);c=GladosEmailLogin(t);c.request_code(ATTEMPT)
        with self.assertRaisesRegex(RefreshError,'code_expired'):
            c.submit(CANDIDATE,known_account_keys={KEY},now=NOW+timedelta(minutes=10))
        self.assertEqual(len(t.calls),1)

    def test_future_code_not_submitted(self):
        t=FakeTransport([{'code':0}]);c=GladosEmailLogin(t);c.request_code(ATTEMPT)
        with self.assertRaisesRegex(RefreshError,'future_code'):
            c.submit(replace(CANDIDATE,issued_at=NOW+timedelta(seconds=30)),known_account_keys={KEY},now=NOW)

    def test_unicode_digits_not_accepted(self):
        t=FakeTransport([{'code':0}]);c=GladosEmailLogin(t);c.request_code(ATTEMPT)
        with self.assertRaisesRegex(RefreshError,'invalid_code'):
            c.submit(replace(CANDIDATE,code='１２３４５６'),known_account_keys={KEY},now=NOW)

    def test_unregistered_id_never_becomes_a_new_account(self):
        t=FakeTransport(good_responses());c=GladosEmailLogin(t);c.request_code(ATTEMPT)
        with self.assertRaisesRegex(RefreshError,'unregistered_authenticated_account'):
            c.submit(CANDIDATE,known_account_keys={'F'*16},now=NOW)

    def test_mismatched_server_email_rejected(self):
        responses=good_responses();responses[2]['data']['email']='wrong@example.com'
        t=FakeTransport(responses);c=GladosEmailLogin(t);c.request_code(ATTEMPT)
        with self.assertRaisesRegex(RefreshError,'identity_mismatch'):
            c.submit(CANDIDATE,known_account_keys={KEY},now=NOW)

    def test_mismatched_user_ids_rejected(self):
        responses=good_responses();responses[2]['data']['userId']='different'
        t=FakeTransport(responses);c=GladosEmailLogin(t);c.request_code(ATTEMPT)
        with self.assertRaisesRegex(RefreshError,'identity_mismatch'):
            c.submit(CANDIDATE,known_account_keys={KEY},now=NOW)

    def test_status_can_supply_missing_email_without_checkin(self):
        responses=good_responses();responses[2]={'code':0,'data':{'userId':UID}}
        responses.insert(3,{'code':0,'data':{'email':TARGET,'userId':UID}})
        t=FakeTransport(responses);c=GladosEmailLogin(t);c.request_code(ATTEMPT)
        self.assertEqual(c.submit(CANDIDATE,known_account_keys={KEY},now=NOW).email,TARGET)
        self.assertEqual(t.calls[3][1],LOGIN_ORIGIN+'/api/user/status')

    def test_missing_server_identity_is_not_assumed_from_login_form(self):
        responses=good_responses();responses[2]={'code':0,'data':{'userId':UID}}
        responses.insert(3,{'code':0,'data':{}})
        t=FakeTransport(responses);c=GladosEmailLogin(t);c.request_code(ATTEMPT)
        with self.assertRaisesRegex(RefreshError,'identity_mismatch'):
            c.submit(CANDIDATE,known_account_keys={KEY},now=NOW)

    def test_invalid_points_rejected(self):
        for value in ['nan','inf',-1,1.5,True,None]:
            with self.subTest(value=value):
                responses=good_responses();responses[3]={'code':0,'points':value}
                t=FakeTransport(responses);c=GladosEmailLogin(t);c.request_code(ATTEMPT)
                with self.assertRaises(RefreshError):c.submit(CANDIDATE,known_account_keys={KEY},now=NOW)

    def test_conflicting_points_rejected(self):
        responses=good_responses();responses[3]={'code':0,'points':5,'data':{'points':6}}
        t=FakeTransport(responses);c=GladosEmailLogin(t);c.request_code(ATTEMPT)
        with self.assertRaisesRegex(RefreshError,'conflicting_points'):
            c.submit(CANDIDATE,known_account_keys={KEY},now=NOW)

    def test_credentials_not_in_result_repr(self):
        t=FakeTransport(good_responses());c=GladosEmailLogin(t);c.request_code(ATTEMPT)
        result=c.submit(CANDIDATE,known_account_keys={KEY},now=NOW)
        self.assertNotIn('synthetic',repr(result))
        self.assertNotIn('cookie',repr(result))


class GmailAdapterTests(unittest.TestCase):
    def profile(self):return {'emailAddress':MAILBOX}

    def test_profile_must_match_expected_mailbox(self):
        t=FakeTransport([{'emailAddress':'other@example.net'}]);c=GmailReadOnly(t,lambda:'fake-access',MAILBOX)
        with self.assertRaisesRegex(RefreshError,'wrong_mailbox'):c.verify_mailbox()

    def test_query_is_read_only_and_token_is_not_in_url(self):
        t=FakeTransport([self.profile(),{'messages':[{'id':'abc12345'}]}]);c=GmailReadOnly(t,lambda:'fake-access',MAILBOX)
        self.assertEqual(c.message_ids(after=NOW),('abc12345',))
        self.assertTrue(all(call[0]=='GET' for call in t.calls))
        self.assertTrue(all('fake-access' not in call[1] for call in t.calls))
        self.assertTrue(all(call[1].startswith(GMAIL_ORIGIN+'/') for call in t.calls))

    def test_server_redirect_preserving_original_to_is_not_filtered_out(self):
        from urllib.parse import parse_qs, urlsplit
        t=FakeTransport([self.profile(),{}]);c=GmailReadOnly(t,lambda:'fake-access',MAILBOX)
        c.message_ids(after=NOW)
        query=parse_qs(urlsplit(t.calls[-1][1]).query)['q'][0]
        self.assertNotIn('to:',query)
        self.assertIn('GLaDOS Authentication Code',query)

    def test_empty_mailbox_is_valid(self):
        t=FakeTransport([self.profile(),{}]);c=GmailReadOnly(t,lambda:'fake-access',MAILBOX)
        self.assertEqual(c.message_ids(after=NOW),())

    def test_pagination_is_complete_or_explicitly_blocked(self):
        t=FakeTransport([self.profile(),{'messages':[{'id':'abc12345'}],'nextPageToken':'next'},self.profile(),{'messages':[{'id':'abc12346'}]}])
        c=GmailReadOnly(t,lambda:'fake-access',MAILBOX)
        self.assertEqual(c.message_ids(after=NOW),('abc12345','abc12346'))

    def test_oversized_mail_window_is_not_silently_truncated(self):
        t=FakeTransport([self.profile(),{'messages':[{'id':'abc12345'}],'nextPageToken':'next'}]);c=GmailReadOnly(t,lambda:'fake-access',MAILBOX)
        with self.assertRaisesRegex(RefreshError,'mail_window_too_large'):c.message_ids(after=NOW,limit=1)

    def test_repeated_page_token_terminates(self):
        t=FakeTransport([self.profile(),{'nextPageToken':'same'},self.profile(),{'nextPageToken':'same'}]);c=GmailReadOnly(t,lambda:'fake-access',MAILBOX)
        with self.assertRaisesRegex(RefreshError,'invalid_mail_pagination'):c.message_ids(after=NOW)

    def test_raw_message_uses_internal_arrival_time(self):
        raw=raw_mail(VALID_AUTH)
        t=FakeTransport([self.profile(),{'id':'abc12345','raw':base64.urlsafe_b64encode(raw).decode(),'internalDate':str(int(NOW.timestamp()*1000))}])
        c=GmailReadOnly(t,lambda:'fake-access',MAILBOX);result=c.read_message('abc12345')
        self.assertEqual(result.authenticated_sender,TARGET)
        self.assertEqual(result.received_at,NOW)
        self.assertEqual(result.raw,raw)

    def test_read_message_cannot_skip_mailbox_validation(self):
        t=FakeTransport([{'emailAddress':'wrong@example.net'}]);c=GmailReadOnly(t,lambda:'fake-access',MAILBOX)
        with self.assertRaisesRegex(RefreshError,'wrong_mailbox'):c.read_message('abc12345')
        self.assertEqual(len(t.calls),1)

    def test_path_injection_rejected_before_request(self):
        t=FakeTransport([]);c=GmailReadOnly(t,lambda:'fake-access',MAILBOX)
        with self.assertRaisesRegex(RefreshError,'invalid_gmail_id'):c.read_message('../profile')
        self.assertEqual(t.calls,[])

    def test_token_missing_is_manual_authorization(self):
        t=FakeTransport([]);c=GmailReadOnly(t,lambda:'',MAILBOX)
        with self.assertRaisesRegex(RefreshError,'mail_auth_required'):c.verify_mailbox()

    def test_response_id_mismatch_rejected(self):
        t=FakeTransport([self.profile(),{'id':'abcd9999','raw':'dGVzdA','internalDate':str(int(NOW.timestamp()*1000))}]);c=GmailReadOnly(t,lambda:'fake-access',MAILBOX)
        with self.assertRaisesRegex(RefreshError,'mail_id_mismatch'):c.read_message('abc12345')

    def test_invalid_base64_rejected(self):
        t=FakeTransport([self.profile(),{'id':'abc12345','raw':'!!!!','internalDate':'123'}]);c=GmailReadOnly(t,lambda:'fake-access',MAILBOX)
        with self.assertRaisesRegex(RefreshError,'invalid_gmail_message'):c.read_message('abc12345')

    def test_authenticated_outer_sender(self):
        self.assertEqual(trusted_outer_sender(raw_mail(VALID_AUTH)),TARGET)

    def test_spf_alignment_accepted_with_dmarc(self):
        auth='mx.google.com; spf=pass smtp.mailfrom=person@example.com; dmarc=pass header.from=example.com'
        self.assertEqual(trusted_outer_sender(raw_mail(auth)),TARGET)

    def test_dkim_d_alignment_supported(self):
        auth='mx.google.com; dkim=pass header.d=example.com; dmarc=pass header.from=example.com'
        self.assertEqual(trusted_outer_sender(raw_mail(auth)),TARGET)

    def test_wrong_authserv_or_domain_not_trusted(self):
        for auth in [VALID_AUTH.replace('mx.google.com','untrusted.example'),VALID_AUTH.replace('header.from=example.com','header.from=evil.example'),VALID_AUTH.replace('dkim=pass','dkim=fail'),VALID_AUTH.replace('dmarc=pass','dmarc=fail')]:
            with self.subTest(auth=auth):self.assertIsNone(trusted_outer_sender(raw_mail(auth)))

    def test_authentication_text_in_body_does_not_count(self):
        raw=raw_mail()+('\nAuthentication-Results: '+VALID_AUTH).encode()
        self.assertIsNone(trusted_outer_sender(raw))

    def test_later_forged_pass_does_not_override_first_failed_result(self):
        raw=raw_mail(VALID_AUTH.replace('dmarc=pass','dmarc=fail'))
        raw=raw.replace(b'\n\n',b'\nAuthentication-Results: '+VALID_AUTH.encode()+b'\n\n',1)
        self.assertIsNone(trusted_outer_sender(raw))

    def test_multiple_from_headers_are_ambiguous(self):
        raw=b'From: attacker@example.com\n'+raw_mail(VALID_AUTH)
        self.assertIsNone(trusted_outer_sender(raw))


class NativeTransportTests(unittest.TestCase):
    def test_unapproved_origins_rejected_without_network(self):
        t=NativeJSONTransport([LOGIN_ORIGIN])
        for url in ['https://evil.example/api','http://glados.cloud/api','https://glados.cloud@evil.example/api','https://glados.cloud:8443/api','https://glados.cloud/api#x']:
            with self.subTest(url=url):
                with self.assertRaises(RefreshError):t.request('GET',url)

    def test_cookie_jar_does_not_read_browser_profile(self):
        t=NativeJSONTransport([LOGIN_ORIGIN],cookies=True)
        self.assertEqual(len(t.jar),0)
        with self.assertRaisesRegex(RefreshError,'session_cookie_contract_changed'):t.credential_header()
        t.close()

    def test_redirect_never_followed(self):
        t=NativeJSONTransport([LOGIN_ORIGIN]);t.opener=Mock()
        t.opener.open.side_effect=urllib.error.HTTPError(LOGIN_ORIGIN,302,'redirect',{},None)
        with self.assertRaisesRegex(HTTPFailure,'redirect_blocked'):t.request('POST',LOGIN_ORIGIN+'/api/authorization',payload={})
        self.assertEqual(t.opener.open.call_count,1)

    def test_rate_limit_returned_without_retry(self):
        t=NativeJSONTransport([LOGIN_ORIGIN]);t.opener=Mock()
        t.opener.open.side_effect=urllib.error.HTTPError(LOGIN_ORIGIN,429,'slow',{'Retry-After':'120'},None)
        with self.assertRaises(HTTPFailure) as error:t.request('POST',LOGIN_ORIGIN+'/api/authorization',payload={})
        self.assertEqual(error.exception.retry_after,120)
        self.assertEqual(t.opener.open.call_count,1)

    def test_write_timeout_is_explicitly_ambiguous(self):
        t=NativeJSONTransport([LOGIN_ORIGIN]);t.opener=Mock();t.opener.open.side_effect=TimeoutError()
        with self.assertRaises(HTTPFailure) as error:t.request('POST',LOGIN_ORIGIN+'/api/authorization',payload={})
        self.assertTrue(error.exception.ambiguous)

    def test_invalid_json_does_not_leak_raw_body(self):
        t=NativeJSONTransport([LOGIN_ORIGIN]);t.opener=Mock();response=io.BytesIO(b'<html>secret-auth-value</html>');response.status=200;t.opener.open.return_value=response
        with self.assertRaises(HTTPFailure) as error:t.request('GET',LOGIN_ORIGIN+'/api/user/session')
        self.assertNotIn('secret',str(error.exception))

    def test_interrupted_post_response_is_not_replayed(self):
        t=NativeJSONTransport([LOGIN_ORIGIN]);t.opener=Mock()
        response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        response.read.side_effect=TimeoutError();t.opener.open.return_value=response
        with self.assertRaises(HTTPFailure) as error:t.request('POST',LOGIN_ORIGIN+'/api/authorization',payload={})
        self.assertTrue(error.exception.ambiguous)
        self.assertEqual(t.opener.open.call_count,1)

    def test_header_injection_rejected(self):
        t=NativeJSONTransport([LOGIN_ORIGIN])
        with self.assertRaisesRegex(RefreshError,'invalid_header'):t.request('GET',LOGIN_ORIGIN,headers={'Authorization':'a\r\nb'})

    def test_json_response_hides_data_in_repr(self):
        self.assertNotIn('private',repr(JSONResponse(200,{'cookie':'private'})))


if __name__ == '__main__':
    unittest.main()
