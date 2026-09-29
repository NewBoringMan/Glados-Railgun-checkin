"""Offline OAuth/pipe tests. Never contacts Google or reads real credentials."""
import base64
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from unittest.mock import Mock

from login_refresh_core import RefreshError
from login_refresh_oauth import (
    AUTH_URL,TOKEN_URL,GMAIL_SCOPE,DesktopClient,GoogleTokenEndpoint,KeychainStore,
    OAuthProvider,PendingConsent,token_record,
)

CLIENT=DesktopClient('12345678-example.apps.googleusercontent.com','synthetic-client-secret')
MAILBOX='codes@example.net'
NOW=1000000.0
REDIRECT='http://127.0.0.1:45678/callback/abcdefghijklmnopqrst'
VERIFIER='v'*64


def token(**changes):
    value={'access_token':'synthetic-access','refresh_token':'synthetic-refresh',
           'expires_in':3600,'token_type':'Bearer','scope':GMAIL_SCOPE}
    value.update(changes);return value


class MemoryStore:
    def __init__(self,record=None):self.data={} if record is None else {'gmail-token':copy.deepcopy(record)};self.writes=0
    def get(self,key):return copy.deepcopy(self.data.get(key))
    def put(self,key,value):self.data[key]=copy.deepcopy(value);self.writes+=1
    def delete(self,key):self.data.pop(key,None)


class Endpoint:
    def __init__(self,value=None,error=None):self.value=token() if value is None else value;self.error=error;self.calls=[]
    def exchange(self,fields):
        self.calls.append(fields)
        if self.error:raise self.error
        return copy.deepcopy(self.value)


def provider(store=None,endpoint=None,profile=None):
    return OAuthProvider(CLIENT,MAILBOX,store or MemoryStore(),endpoint=endpoint or Endpoint(),
                         profile_check=profile or (lambda access:MAILBOX),clock=lambda:NOW)


class ClientTests(unittest.TestCase):
    def test_native_configuration(self):
        c=DesktopClient.from_download({'installed':{'client_id':CLIENT.client_id,'client_secret':CLIENT.client_secret,
                                      'auth_uri':AUTH_URL,'token_uri':TOKEN_URL}})
        self.assertEqual(c,CLIENT)

    def test_web_client_not_treated_as_desktop(self):
        with self.assertRaisesRegex(RefreshError,'desktop_oauth_client_required'):DesktopClient.from_download({'web':{}})

    def test_untrusted_endpoint_rejected(self):
        with self.assertRaisesRegex(RefreshError,'unapproved_oauth_endpoint'):
            DesktopClient.from_download({'installed':{'client_id':CLIENT.client_id,'client_secret':'x',
                                                     'auth_uri':'https://evil.invalid','token_uri':TOKEN_URL}})

    def test_invalid_id_or_secret_rejected(self):
        for cid,secret in [('invalid','x'),(CLIENT.client_id,''),(CLIENT.client_id,'x\ny'),(CLIENT.client_id,None)]:
            with self.subTest(cid=cid):
                with self.assertRaises(RefreshError):DesktopClient(cid,secret)

    def test_client_secret_hidden_in_repr(self):self.assertNotIn(CLIENT.client_secret,repr(CLIENT))


class TokenTests(unittest.TestCase):
    def test_initial_token_record(self):
        r=token_record(token(),CLIENT,MAILBOX,NOW)
        self.assertEqual(r['access_expires_at'],NOW+3600)
        self.assertEqual(r['mailbox'],MAILBOX)

    def test_no_refresh_token_cannot_enable_unattended_mode(self):
        t=token();t.pop('refresh_token')
        with self.assertRaisesRegex(RefreshError,'invalid_oauth_response'):token_record(t,CLIENT,MAILBOX,NOW)

    def test_refresh_preserves_existing_refresh_token_and_scope(self):
        prior=token_record(token(),CLIENT,MAILBOX,NOW)
        response=token();response.pop('scope');response.pop('refresh_token')
        self.assertEqual(token_record(response,CLIENT,MAILBOX,NOW+20,prior)['refresh_token'],'synthetic-refresh')

    def test_rotated_refresh_token_replaces_old(self):
        prior=token_record(token(),CLIENT,MAILBOX,NOW)
        self.assertEqual(token_record(token(refresh_token='rotated'),CLIENT,MAILBOX,NOW,prior)['refresh_token'],'rotated')

    def test_extra_permissions_rejected(self):
        with self.assertRaisesRegex(RefreshError,'gmail_readonly_scope_required'):
            token_record(token(scope=GMAIL_SCOPE+' https://mail.google.com/'),CLIENT,MAILBOX,NOW)

    def test_missing_scope_rejected_for_initial_consent(self):
        t=token();t.pop('scope')
        with self.assertRaisesRegex(RefreshError,'gmail_readonly_scope_required'):token_record(t,CLIENT,MAILBOX,NOW)

    def test_timed_refresh_expiry_recorded(self):
        self.assertEqual(token_record(token(refresh_token_expires_in=600),CLIENT,MAILBOX,NOW)['refresh_expires_at'],NOW+600)

    def test_malformed_response_values_are_rejected(self):
        for k,v in [('expires_in',True),('expires_in',float('nan')),('expires_in',-1),('expires_in',10**12),
                    ('access_token','secret\r\nInjected'),('access_token',None),('token_type',False),('token_type','MAC')]:
            with self.subTest(key=k,value=v):
                with self.assertRaises(RefreshError):token_record(token(**{k:v}),CLIENT,MAILBOX,NOW)

    def test_wrong_mailbox_preserves_existing_record(self):
        old=token_record(token(),CLIENT,MAILBOX,NOW);store=MemoryStore(old)
        p=provider(store=store,profile=lambda t:'wrong@example.net')
        with self.assertRaisesRegex(RefreshError,'wrong_mailbox'):p.accept_code('synthetic-code',VERIFIER,REDIRECT)
        self.assertEqual(store.get('gmail-token'),old);self.assertEqual(store.writes,0)

    def test_consent_checks_profile_before_saving(self):
        calls=[];store=MemoryStore()
        def check(t):calls.append(store.writes);return MAILBOX
        p=provider(store=store,profile=check);p.accept_code('synthetic-code',VERIFIER,REDIRECT)
        self.assertEqual(calls,[0]);self.assertEqual(store.writes,1)

    def test_valid_access_token_does_not_trigger_oauth_request(self):
        ep=Endpoint();p=provider(store=MemoryStore(token_record(token(),CLIENT,MAILBOX,NOW)),endpoint=ep)
        self.assertEqual(p.access_token(),'synthetic-access');self.assertEqual(ep.calls,[])

    def test_expired_access_token_is_refreshed_not_reconsented(self):
        old=token_record(token(),CLIENT,MAILBOX,NOW-4000);ep=Endpoint(token(access_token='new-access'))
        p=provider(store=MemoryStore(old),endpoint=ep)
        self.assertEqual(p.access_token(),'new-access')
        self.assertEqual(ep.calls[0]['grant_type'],'refresh_token');self.assertNotIn('code',ep.calls[0])

    def test_outage_does_not_destroy_saved_credential(self):
        old=token_record(token(),CLIENT,MAILBOX,NOW-4000);store=MemoryStore(old)
        p=provider(store=store,endpoint=Endpoint(error=RefreshError('oauth_endpoint_unavailable')))
        with self.assertRaisesRegex(RefreshError,'oauth_endpoint_unavailable'):p.access_token()
        self.assertEqual(store.get('gmail-token'),old);self.assertEqual(store.writes,0)

    def test_expired_refresh_token_requires_human_not_a_loop(self):
        old=token_record(token(refresh_token_expires_in=600),CLIENT,MAILBOX,NOW-1000);ep=Endpoint()
        p=provider(store=MemoryStore(old),endpoint=ep)
        with self.assertRaisesRegex(RefreshError,'mail_reauthorization_required'):p.access_token()
        self.assertEqual(ep.calls,[])

    def test_missing_credential_requires_consent(self):
        with self.assertRaisesRegex(RefreshError,'mail_auth_required'):provider().access_token()

    def test_record_bound_to_client_and_mailbox(self):
        for key,value in [('client_id','wrong'),('mailbox','different@example.net')]:
            r=token_record(token(),CLIENT,MAILBOX,NOW);r[key]=value
            with self.assertRaisesRegex(RefreshError,'mail_auth_binding_mismatch'):provider(store=MemoryStore(r)).access_token()

    def test_corrupt_record_rejected(self):
        for key,value in [('access_expires_at',True),('access_expires_at',float('nan')),('refresh_expires_at','bad')]:
            r=token_record(token(),CLIENT,MAILBOX,NOW);r[key]=value
            with self.assertRaisesRegex(RefreshError,'invalid_secret_record'):provider(store=MemoryStore(r)).access_token()

    def test_bad_verifier_or_callback_not_sent(self):
        for verifier,callback in [('short',REDIRECT),(VERIFIER,'https://evil.invalid/callback'),
                                  (VERIFIER,'http://127.0.0.1:1234/callback/x?secret=x')]:
            ep=Endpoint();p=provider(endpoint=ep)
            with self.assertRaises(RefreshError):p.accept_code('synthetic-code',verifier,callback)
            self.assertEqual(ep.calls,[])

    def test_concurrent_refreshes_share_one_result(self):
        ep=Endpoint();store=MemoryStore(token_record(token(),CLIENT,MAILBOX,NOW-4000));p=provider(store=store,endpoint=ep)
        results=[];threads=[threading.Thread(target=lambda:results.append(p.access_token())) for _ in range(8)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertEqual(len(ep.calls),1);self.assertEqual(len(results),8)


class CallbackTests(unittest.TestCase):
    def setUp(self):self.p=provider();self.pending=PendingConsent(self.p)
    def tearDown(self):self.pending.close()
    def route(self,**params):
        data={'state':self.pending.state,'code':'synthetic-code'};data.update(params)
        return self.pending.path+'?'+urllib.parse.urlencode(data)

    def test_loopback_only_and_pkce_s256(self):
        u=urllib.parse.urlsplit(self.pending.authorization_url);q=urllib.parse.parse_qs(u.query)
        self.assertEqual(u.scheme+'://'+u.netloc+u.path,AUTH_URL)
        self.assertEqual(self.pending.server.server_address[0],'127.0.0.1')
        self.assertEqual(q['scope'],[GMAIL_SCOPE]);self.assertEqual(q['code_challenge_method'],['S256'])
        challenge=base64.urlsafe_b64encode(hashlib.sha256(self.pending.verifier.encode()).digest()).rstrip(b'=').decode()
        self.assertEqual(q['code_challenge'],[challenge]);self.assertNotIn(self.pending.verifier,self.pending.authorization_url)

    def test_wrong_state_not_consumed(self):
        self.assertEqual(self.pending._receive(self.route(state='bad'),[self.pending.authority],'127.0.0.1'),400)
        self.assertFalse(self.pending.done);self.assertEqual(self.p.store.writes,0)

    def test_bad_host_or_peer_rejected(self):
        for hosts,peer in [(['evil.example'],'127.0.0.1'),([self.pending.authority,self.pending.authority],'127.0.0.1'),([self.pending.authority],'192.0.2.1')]:
            self.assertEqual(self.pending._receive(self.route(),hosts,peer),400)
        self.assertFalse(self.pending.done)

    def test_duplicate_state_rejected(self):
        route=self.route()+'&state='+self.pending.state
        self.assertEqual(self.pending._receive(route,[self.pending.authority],'127.0.0.1'),400)
        self.assertFalse(self.pending.done)

    def test_wrong_callback_path_rejected(self):
        self.assertEqual(self.pending._receive('/other?state='+self.pending.state,[self.pending.authority],'127.0.0.1'),404)

    def test_consumed_callback_cannot_be_replayed(self):
        route=self.route()
        self.assertEqual(self.pending._receive(route,[self.pending.authority],'127.0.0.1'),200)
        self.assertEqual(self.pending._receive(route,[self.pending.authority],'127.0.0.1'),410)
        self.assertEqual(self.p.store.writes,1);self.assertEqual(self.pending.verifier,'')

    def test_consent_denial_does_not_exchange_code(self):
        self.assertEqual(self.pending._receive(self.route(error='access_denied'),[self.pending.authority],'127.0.0.1'),200)
        self.assertEqual(self.pending.error,'consent_denied');self.assertEqual(self.p.endpoint.calls,[])

    def test_expired_flow_rejects_callback(self):
        self.pending.deadline=self.pending.clock()-1
        self.assertEqual(self.pending._receive(self.route(),[self.pending.authority],'127.0.0.1'),410)

    def test_failed_exchange_is_not_replayed(self):
        self.p.endpoint.error=RefreshError('oauth_endpoint_unavailable');route=self.route()
        self.pending._receive(route,[self.pending.authority],'127.0.0.1')
        self.assertEqual(self.pending.error,'oauth_endpoint_unavailable')
        self.assertEqual(self.pending._receive(route,[self.pending.authority],'127.0.0.1'),410)
        self.assertEqual(len(self.p.endpoint.calls),1)

    def test_unknown_error_does_not_leak_secret(self):
        self.p.endpoint.error=ValueError('secret-access-token')
        self.pending._receive(self.route(),[self.pending.authority],'127.0.0.1')
        self.assertEqual(self.pending.error,'consent_completion_failed')

    def test_real_local_callback_socket_and_cleanup(self):
        failures=[]
        def wait():
            try:self.pending.wait()
            except Exception as e:failures.append(type(e).__name__)
        thread=threading.Thread(target=wait);thread.start()
        url='http://'+self.pending.authority+self.route()
        # This is loopback synthetic data only; external networking is not used.
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url,timeout=3) as r:
            text=r.read().decode();self.assertEqual(r.status,200);self.assertEqual(r.headers['Cache-Control'],'no-store')
            self.assertNotIn('synthetic-code',text);self.assertNotIn(self.p.client.client_secret,text)
        thread.join(5)
        self.assertFalse(thread.is_alive());self.assertEqual(failures,[]);self.assertTrue(self.pending.closed)


class EndpointTests(unittest.TestCase):
    def test_form_post_only_to_official_endpoint(self):
        opener=Mock();r=io.BytesIO(json.dumps(token()).encode());r.status=200;opener.open.return_value=r
        endpoint=GoogleTokenEndpoint(opener);self.assertEqual(endpoint.exchange({'code':'synthetic-code'})['token_type'],'Bearer')
        req=opener.open.call_args.args[0];self.assertEqual(req.full_url,TOKEN_URL);self.assertEqual(req.method,'POST')
        self.assertNotIn('synthetic-code',req.full_url);self.assertEqual(req.headers['Content-type'],'application/x-www-form-urlencoded')

    def test_invalid_grant_means_reauthorization(self):
        opener=Mock();opener.open.side_effect=urllib.error.HTTPError(TOKEN_URL,400,'bad',{},io.BytesIO(b'{"error":"invalid_grant"}'))
        with self.assertRaisesRegex(RefreshError,'mail_reauthorization_required'):GoogleTokenEndpoint(opener).exchange({})
        self.assertEqual(opener.open.call_count,1)

    def test_remote_error_description_not_exposed(self):
        opener=Mock();opener.open.side_effect=urllib.error.HTTPError(TOKEN_URL,500,'secret',{},io.BytesIO(b'{"error_description":"secret-value"}'))
        with self.assertRaisesRegex(RefreshError,'^oauth_endpoint_unavailable$'):GoogleTokenEndpoint(opener).exchange({})

    def test_network_error_no_automatic_retries(self):
        opener=Mock();opener.open.side_effect=TimeoutError()
        with self.assertRaisesRegex(RefreshError,'oauth_endpoint_unavailable'):GoogleTokenEndpoint(opener).exchange({})
        self.assertEqual(opener.open.call_count,1)


class PipeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.binary=Path(self.temp.name)/'RefreshSecretStore'
        self.binary.write_text('synthetic executable fixture');self.binary.chmod(0o700)
        self.run=Mock(return_value=subprocess.CompletedProcess([],0,b'{"ok":true,"found":false}',b''))
        self.store=KeychainStore(self.binary,run=self.run)
    def tearDown(self):self.temp.cleanup()

    def test_value_goes_to_stdin_never_arguments(self):
        self.store.put('gmail-token',{'access_token':'synthetic-value'})
        self.assertEqual(self.run.call_args.args[0],[str(self.binary)])
        self.assertIn(b'synthetic-value',self.run.call_args.kwargs['input'])
        self.assertEqual(self.run.call_args.kwargs['stderr'],subprocess.PIPE)

    def test_missing_item_returns_none(self):self.assertIsNone(self.store.get('gmail-token'))

    def test_unrelated_key_rejected_without_process(self):
        with self.assertRaisesRegex(RefreshError,'invalid_secret_key'):self.store.get('other-app-key')
        self.run.assert_not_called()

    def test_helper_denial_stays_controlled(self):
        self.run.return_value=subprocess.CompletedProcess([],0,b'{"ok":false,"reason":"keychain_locked_or_approval_required"}',b'')
        with self.assertRaisesRegex(RefreshError,'keychain_locked_or_approval_required'):self.store.get('gmail-token')

    def test_helper_error_body_not_exposed(self):
        self.run.return_value=subprocess.CompletedProcess([],0,b'{"ok":false,"reason":"secret-value"}',b'')
        with self.assertRaisesRegex(RefreshError,'^keychain_unavailable$'):self.store.get('gmail-token')

    def test_symlink_or_world_writable_helper_rejected(self):
        link=self.binary.with_name('link');link.symlink_to(self.binary)
        with self.assertRaisesRegex(RefreshError,'secret_helper_unavailable'):KeychainStore(link)
        self.binary.chmod(0o777)
        with self.assertRaisesRegex(RefreshError,'unsafe_permissions'):KeychainStore(self.binary)

    def test_oversized_records_rejected_before_launch(self):
        with self.assertRaisesRegex(RefreshError,'secret_record_too_large'):self.store.put('gmail-token',{'value':'x'*40000})
        self.run.assert_not_called()

    def test_corrupt_helper_response_rejected(self):
        self.run.return_value=subprocess.CompletedProcess([],0,b'not-json-secret',b'')
        with self.assertRaisesRegex(RefreshError,'keychain_unavailable'):self.store.get('gmail-token')


if __name__=='__main__':unittest.main()
