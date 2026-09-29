"""Account Center's own Gmail desktop OAuth and Keychain-backed token lifecycle.

Uses the official desktop loopback + S256 PKCE flow. It never opens a browser,
changes Mail, consumes a ChatGPT credential, sends email, or enables a scheduler.
The host must present the consent URL via its allowed user/DCF interaction route.
Client configuration comes from the user's Desktop OAuth client, not source code.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import http.server
import json
import math
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Protocol

from login_refresh_core import RefreshError, email_address
from login_refresh_http import GmailReadOnly, GMAIL_SCOPE, GMAIL_ORIGIN, NativeJSONTransport

AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN_URL = 'https://oauth2.googleapis.com/token'
MAX_RECORD = 32768


class SecretStore(Protocol):
    def get(self, key: str) -> dict | None: ...
    def put(self, key: str, value: dict): ...
    def delete(self, key: str): ...


class KeychainStore:
    """Invoke the bundled internal native component over anonymous pipes.

    The signed host verifies its bundle before constructing this adapter. No password
    value is ever a command argument. No fallback to plaintext files or another app's
    Keychain service is allowed. The native component independently checks record keys.
    """
    def __init__(self, executable: Path | str, *, run=subprocess.run):
        path = Path(executable)
        if not path.is_absolute() or not path.is_file() or path.is_symlink():
            raise RefreshError('secret_helper_unavailable')
        st = path.stat()
        if st.st_uid != os.getuid() or st.st_mode & 0o022 or not os.access(path, os.X_OK):
            raise RefreshError('secret_helper_unsafe_permissions')
        self.executable = str(path)
        self._run = run

    def _call(self, op, key, value=None):
        if not isinstance(key,str) or not re.fullmatch(r'(gmail-client|gmail-token|candidate-[A-F0-9]{16}|active-[A-F0-9]{16})',key):
            raise RefreshError('invalid_secret_key')
        request = {'op':op,'key':key}
        if value is not None:
            if not isinstance(value,dict):raise RefreshError('invalid_secret_record')
            request['value']=value
        try:
            data=json.dumps(request,allow_nan=False).encode()
        except (TypeError,ValueError):raise RefreshError('invalid_secret_record') from None
        if len(data)>MAX_RECORD:raise RefreshError('secret_record_too_large')
        try:
            result=self._run([self.executable],input=data,stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE,timeout=10,check=False)
            if result.returncode or len(result.stdout)>65536:raise ValueError
            response=json.loads(result.stdout)
            if not isinstance(response,dict):raise ValueError
        except (OSError,subprocess.SubprocessError,ValueError,TypeError):
            raise RefreshError('keychain_unavailable') from None
        if response.get('ok') is not True:
            reason=response.get('reason')
            if reason not in {'keychain_locked_or_approval_required','invalid_secret_record','invalid_secret_key'}:
                reason='keychain_unavailable'
            raise RefreshError(reason)
        return response

    def get(self,key):
        result=self._call('get',key)
        if result.get('found') is False:return None
        if result.get('found') is not True or not isinstance(result.get('value'),dict):
            raise RefreshError('invalid_secret_record')
        return result['value']

    def put(self,key,value):self._call('put',key,value)
    def delete(self,key):self._call('delete',key)


@dataclass(frozen=True)
class DesktopClient:
    client_id: str
    client_secret: str = field(repr=False)

    def __post_init__(self):
        if not isinstance(self.client_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,250}\.apps\.googleusercontent\.com',self.client_id):
            raise RefreshError('invalid_desktop_client')
        if not isinstance(self.client_secret,str) or not 1<=len(self.client_secret)<=1024 or any(ord(c)<32 for c in self.client_secret):
            raise RefreshError('invalid_desktop_client')

    @classmethod
    def from_download(cls, data: dict):
        if not isinstance(data,dict) or 'web' in data or not isinstance(data.get('installed'),dict):
            raise RefreshError('desktop_oauth_client_required')
        config=data['installed']
        if config.get('auth_uri') not in {AUTH_URL,'https://accounts.google.com/o/oauth2/auth'} or config.get('token_uri')!=TOKEN_URL:
            raise RefreshError('unapproved_oauth_endpoint')
        return cls(config.get('client_id'),config.get('client_secret'))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


class GoogleTokenEndpoint:
    """One HTTPS form POST, no redirect and no automatic replay."""
    def __init__(self,opener=None):
        self.opener=opener or urllib.request.build_opener(_NoRedirect())

    def exchange(self,fields: dict) -> dict:
        body=urllib.parse.urlencode(fields).encode()
        req=urllib.request.Request(TOKEN_URL,data=body,method='POST',headers={
            'Content-Type':'application/x-www-form-urlencoded','Accept':'application/json'})
        try:
            response=self.opener.open(req,timeout=18)
        except urllib.error.HTTPError as exc:
            try:
                status=exc.code
                raw=exc.read(8192)
                value=json.loads(raw)
                error=value.get('error') if isinstance(value,dict) else None
            except Exception:error=None
            finally:exc.close()
            if status==400 and error=='invalid_grant':raise RefreshError('mail_reauthorization_required') from None
            if error in {'invalid_client','unauthorized_client'}:raise RefreshError('desktop_oauth_client_invalid') from None
            raise RefreshError('oauth_endpoint_unavailable') from None
        except (OSError,urllib.error.URLError,TimeoutError):
            raise RefreshError('oauth_endpoint_unavailable') from None
        with response:
            try:
                data=response.read(65537)
                if len(data)>65536:raise ValueError
                value=json.loads(data)
                if not isinstance(value,dict) or response.status!=200:raise ValueError
                return value
            except (ValueError,TypeError,OSError):raise RefreshError('invalid_oauth_response') from None


def _secret(value):
    if not isinstance(value,str) or not 1<=len(value)<=16384 or any(ord(c)<33 or ord(c)>126 for c in value):
        raise RefreshError('invalid_oauth_response')
    return value


def _duration(value,maximum):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not 30<=value<=maximum:
        raise RefreshError('invalid_oauth_response')
    return float(value)


def token_record(payload,client:DesktopClient,mailbox:str,now:float,prior=None):
    if not isinstance(payload,dict) or type(now) not in (int,float) or not math.isfinite(now):
        raise RefreshError('invalid_oauth_response')
    kind=payload.get('token_type')
    if not isinstance(kind,str) or kind.casefold()!='bearer':raise RefreshError('unsupported_oauth_token_type')
    access=_secret(payload.get('access_token'))
    duration=_duration(payload.get('expires_in'),86400)
    supplied_scope=payload.get('scope')
    scopes=set(supplied_scope.split()) if isinstance(supplied_scope,str) else set(prior.get('scopes',[])) if prior else set()
    if scopes!={GMAIL_SCOPE}:raise RefreshError('gmail_readonly_scope_required')
    refresh=_secret(payload.get('refresh_token') or (prior.get('refresh_token') if prior else None))
    refresh_expiry=prior.get('refresh_expires_at') if prior else None
    if 'refresh_token_expires_in' in payload:
        refresh_expiry=now+_duration(payload['refresh_token_expires_in'],10*365*86400)
    return {'version':1,'client_id':client.client_id,'mailbox':email_address(mailbox),
            'access_token':access,'refresh_token':refresh,'access_expires_at':now+duration,
            'refresh_expires_at':refresh_expiry,'scopes':[GMAIL_SCOPE],'verified_at':now}


class OAuthProvider:
    """Validate the granted mailbox BEFORE overwriting a previously valid record.

    Normal queue execution is single-process/locked. This instance also serializes
    in-process refresh calls. Network outages keep the previous protected record.
    """
    def __init__(self,client:DesktopClient,mailbox:str,store:SecretStore,*,endpoint=None,
                 profile_check:Callable[[str],str]|None=None,clock=time.time):
        self.client,self.mailbox,self.store=client,email_address(mailbox),store
        self.endpoint=endpoint or GoogleTokenEndpoint()
        self.clock=clock
        self._lock=threading.Lock()
        self.profile_check=profile_check or self._profile

    @staticmethod
    def _profile(token):
        t=NativeJSONTransport([GMAIL_ORIGIN])
        try:
            r=t.request('GET',GMAIL_ORIGIN+'/gmail/v1/users/me/profile',headers={'Authorization':'Bearer '+token})
            if r.status!=200:raise RefreshError('mail_api_unavailable')
            return email_address(r.data.get('emailAddress',''))
        finally:t.close()

    def _verify_profile(self,record):
        if email_address(self.profile_check(record['access_token']))!=self.mailbox:
            raise RefreshError('wrong_mailbox')

    def accept_code(self,code,verifier,redirect_uri):
        # Only PendingConsent may call this after validating its state and callback.
        _secret(code)
        if not isinstance(verifier,str) or not re.fullmatch(r'[A-Za-z0-9._~-]{43,128}',verifier):
            raise RefreshError('invalid_pkce_verifier')
        try:
            callback=urllib.parse.urlsplit(redirect_uri)
            if (callback.scheme!='http' or callback.hostname!='127.0.0.1' or callback.username
                    or callback.password or callback.query or callback.fragment or not callback.port
                    or not re.fullmatch(r'/callback/[A-Za-z0-9_-]{16,80}',callback.path)):
                raise ValueError
        except (TypeError,ValueError):raise RefreshError('invalid_loopback_callback') from None
        with self._lock:
            record=token_record(self.endpoint.exchange({
                'grant_type':'authorization_code','client_id':self.client.client_id,
                'client_secret':self.client.client_secret,'code':code,
                'code_verifier':verifier,'redirect_uri':redirect_uri}),self.client,self.mailbox,self.clock())
            self._verify_profile(record)
            self.store.put('gmail-token',record)

    def access_token(self):
        with self._lock:
            record=self.store.get('gmail-token')
            if not record:raise RefreshError('mail_auth_required')
            try:
                if record.get('version')!=1 or record['client_id']!=self.client.client_id or email_address(record['mailbox'])!=self.mailbox:
                    raise RefreshError('mail_auth_binding_mismatch')
                _secret(record['access_token']);_secret(record['refresh_token'])
                if set(record.get('scopes',[]))!={GMAIL_SCOPE}:raise RefreshError('gmail_readonly_scope_required')
                expiry=record['access_expires_at'];refresh_expiry=record.get('refresh_expires_at')
                if isinstance(expiry,bool) or not isinstance(expiry,(int,float)) or not math.isfinite(expiry):raise ValueError
                if refresh_expiry is not None and (isinstance(refresh_expiry,bool) or not isinstance(refresh_expiry,(int,float)) or not math.isfinite(refresh_expiry)):raise ValueError
            except RefreshError:raise
            except (KeyError,TypeError,ValueError):raise RefreshError('invalid_secret_record') from None
            now=self.clock()
            if type(now) not in (int,float) or not math.isfinite(now):raise RefreshError('invalid_clock')
            if refresh_expiry is not None and refresh_expiry<=now+60:raise RefreshError('mail_reauthorization_required')
            if now+60<expiry<=now+86400:return record['access_token']
            response=self.endpoint.exchange({'grant_type':'refresh_token','client_id':self.client.client_id,
                                             'client_secret':self.client.client_secret,'refresh_token':record['refresh_token']})
            refreshed=token_record(response,self.client,self.mailbox,now,record)
            self._verify_profile(refreshed)
            self.store.put('gmail-token',refreshed)
            return refreshed['access_token']


class PendingConsent:
    """Short-lived IPv4 loopback callback. The host chooses when/how to show the URL.

    No external page resources, URL logging, browser launch or persistent Web storage.
    Invalid state/Host requests cannot consume or swap a real authorization response.
    """
    def __init__(self,provider:OAuthProvider,*,ttl=300,clock=time.monotonic):
        if type(ttl) not in (int,float) or not math.isfinite(ttl) or not 30<=ttl<=600:raise RefreshError('invalid_consent_ttl')
        self.provider,self.clock=provider,clock
        self.deadline=clock()+ttl
        self.state=secrets.token_urlsafe(32)
        self.verifier=secrets.token_urlsafe(48)
        self.path='/callback/'+secrets.token_urlsafe(18)
        self.done=False
        self.error=None
        self.closed=False
        owner=self
        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version='HTTP/1.0'
            def log_message(self,*args):pass
            def setup(self):
                super().setup();self.connection.settimeout(2)
            def do_GET(self):
                status=owner._receive(self.path,self.headers.get_all('Host',[]),self.client_address[0])
                body=b'Authorization handled. You may return to GLaDOS Account Center.' if status==200 else b'Authorization was not accepted. Return to the app.'
                self.send_response(status)
                self.send_header('Content-Type','text/plain; charset=utf-8')
                self.send_header('Content-Length',str(len(body)))
                self.send_header('Cache-Control','no-store')
                self.send_header('Referrer-Policy','no-referrer')
                self.send_header('Content-Security-Policy',"default-src 'none'")
                self.end_headers()
                try:self.wfile.write(body)
                except OSError:pass
            def do_POST(self):self.send_error(405)
        self.server=http.server.HTTPServer(('127.0.0.1',0),Handler)
        self.server.timeout=.5
        self.authority='127.0.0.1:'+str(self.server.server_port)
        self.redirect_uri='http://'+self.authority+self.path

    @property
    def authorization_url(self):
        if self.closed or self.done or self.clock()>=self.deadline:raise RefreshError('consent_expired')
        challenge=base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode()).digest()).rstrip(b'=').decode()
        return AUTH_URL+'?'+urllib.parse.urlencode({'client_id':self.provider.client.client_id,
            'redirect_uri':self.redirect_uri,'response_type':'code','scope':GMAIL_SCOPE,
            'access_type':'offline','prompt':'consent','login_hint':self.provider.mailbox,
            'state':self.state,'code_challenge':challenge,'code_challenge_method':'S256'})

    def _receive(self,target,hosts,peer):
        if self.closed or self.done or self.clock()>=self.deadline:return 410
        if hosts!=[self.authority] or peer!='127.0.0.1' or not isinstance(target,str) or len(target)>8192:return 400
        try:
            parsed=urllib.parse.urlsplit(target)
            if parsed.scheme or parsed.netloc or parsed.fragment or parsed.path!=self.path:return 404
            params=urllib.parse.parse_qs(parsed.query,keep_blank_values=True,max_num_fields=10)
            if any(len(value)!=1 for value in params.values()):return 400
            state=params.get('state',[''])[0]
            if not hmac.compare_digest(state,self.state):return 400
            if 'error' in params:
                self.done=True;self.error='consent_denied';return 200
            code=params.get('code',[''])[0]
            _secret(code)
        except (ValueError,TypeError,RefreshError):return 400
        self.done=True  # persist in-memory intent before the single token exchange
        try:self.provider.accept_code(code,self.verifier,self.redirect_uri)
        except RefreshError as exc:
            safe={'wrong_mailbox','mail_reauthorization_required','desktop_oauth_client_invalid',
                  'gmail_readonly_scope_required','oauth_endpoint_unavailable','keychain_locked_or_approval_required'}
            self.error=str(exc) if str(exc) in safe else 'consent_completion_failed'
        except Exception:self.error='consent_completion_failed'
        self.verifier='';self.state=''
        return 200

    def wait(self):
        try:
            while not self.done and not self.closed and self.clock()<self.deadline:
                try:self.server.handle_request()
                except (OSError,socket.timeout):continue
            if not self.done:raise RefreshError('consent_expired')
            if self.error:raise RefreshError(self.error)
        finally:self.close()

    def close(self):
        if not self.closed:
            self.closed=True;self.server.server_close()
        self.verifier='';self.state=''

    def __enter__(self):return self
    def __exit__(self,*args):self.close()
