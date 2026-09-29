"""Serial login-maintenance runner inside Account Center, with resumable publication.

No GUI launch, challenge solving, cookie logging or exchange operation. A user must
configure Gmail and pass one real account/cloud acceptance before batch operation.
One invocation processes one due account; the host can resume remaining due work.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from login_refresh_core import LoginAttempt, RefreshError, account_key, email_address, select_code
from login_refresh_http import GladosEmailLogin, GmailReadOnly, NativeJSONTransport, GMAIL_ORIGIN, LOGIN_ORIGIN

UTC = timezone.utc
REPO = 'NewBoringMan/Glados-Railgun-checkin'
RUNNER_VERSION = 1
ACTIVE = ('preflight', 'awaiting_code', 'candidate_verified', 'publish_pending', 'verify_pending')


class GitHubCloud:
    """Only an existing Secret and the existing read-only status workflow are used."""
    def __init__(self, run=subprocess.run):
        self.run = run

    def command(self, args, data=None, limit=8*1024*1024):
        try:
            result = self.run(['/opt/homebrew/bin/gh', *args], input=data,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=45, check=False)
        except (OSError, subprocess.SubprocessError):
            raise RefreshError('cloud_operation_uncertain' if data is not None else 'github_unavailable') from None
        if result.returncode or len(result.stdout) > limit:
            raise RefreshError('cloud_operation_uncertain' if data is not None else 'github_unavailable')
        return result.stdout

    def api(self, endpoint, data=None):
        args = ['api', f'repos/{REPO}/{endpoint}', '-H', 'X-GitHub-Api-Version: 2026-03-10']
        if data is not None:
            args += ['-X', 'POST', '--input', '-']
        try:
            return json.loads(self.command(args, None if data is None else json.dumps(data).encode()))
        except (ValueError, TypeError):
            raise RefreshError('cloud_operation_uncertain') from None

    def head(self):
        value = self.api('git/ref/heads/master').get('object', {}).get('sha')
        if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{40}', value):
            raise RefreshError('github_unavailable')
        return value

    def existing_secret(self, key):
        key = account_key(key)
        name = 'GLADOS_ACCOUNT_' + key
        result = json.loads(self.command(['secret', 'list', '-R', REPO, '--json', 'name']))
        if not isinstance(result, list) or not any(item.get('name') == name for item in result):
            raise RefreshError('preexisting_secret_required')
        return name

    def upload(self, key, cookie):
        name = self.existing_secret(key)
        if (not isinstance(cookie, str) or not re.fullmatch(r'koa:sess=[^;\r\n]+; koa:sess\.sig=[^;\r\n]+;', cookie)
                or len(cookie) > 30000):
            raise RefreshError('invalid_secret_record')
        self.command(['secret', 'set', name, '-R', REPO], cookie.encode(), limit=65536)

    def dispatch_status(self, key):
        key = account_key(key)
        # The existing status workflow may cancel earlier runs; don't interrupt one.
        current = self.api('actions/workflows/gladosStatus.yml/runs?per_page=30')
        if any(r.get('status') in {'queued', 'in_progress', 'waiting', 'pending', 'requested'}
               for r in current.get('workflow_runs', [])):
            raise RefreshError('cloud_status_busy')
        result = self.api('actions/workflows/gladosStatus.yml/dispatches',
                          {'ref': 'master', 'inputs': {'account': key}})
        run_id = result.get('workflow_run_id')
        if type(run_id) is not int or run_id <= 0:
            raise RefreshError('cloud_dispatch_uncertain')
        return run_id

    def verify_status(self, run_id, key, expected_email, expected_sha, started_at):
        if type(run_id) is not int or run_id <= 0:
            raise RefreshError('cloud_dispatch_uncertain')
        meta = self.api(f'actions/runs/{run_id}')
        try:
            created = datetime.fromisoformat(meta['created_at'].replace('Z', '+00:00')).timestamp()
        except (KeyError, ValueError, TypeError):
            raise RefreshError('cloud_verification_failed') from None
        if (meta.get('head_sha') != expected_sha or meta.get('head_branch') != 'master'
                or meta.get('event') != 'workflow_dispatch' or created < int(started_at)):
            raise RefreshError('cloud_verification_failed')
        if meta.get('status') != 'completed':
            return False
        jobs = self.api(f'actions/runs/{run_id}/jobs?per_page=100').get('jobs', [])
        target = [j for j in jobs if j.get('name') == 'GLaDOS status ' + key]
        if len(target) != 1 or target[0].get('conclusion') != 'success':
            raise RefreshError('cloud_verification_failed')
        # Logs are parsed in memory; no raw output is printed or saved.
        raw = self.command(['run', 'view', str(run_id), '-R', REPO, '--job', str(target[0]['id']), '--log'])
        results = []
        for line in raw.decode('utf-8', 'replace').splitlines():
            if 'GLADOS_STATUS_JSON=' not in line:
                continue
            text = line.split('GLADOS_STATUS_JSON=', 1)[1]
            try:
                result, _ = json.JSONDecoder().raw_decode(text)
            except ValueError:
                continue
            if isinstance(result, dict) and result.get('account_key') == key:
                results.append(result)
        valid = [r for r in results if r.get('ok') is True and r.get('email')
                 and email_address(r['email']) == expected_email]
        if len(valid) != 1:
            raise RefreshError('cloud_identity_unverified')
        return True


def state_digest(snapshot):
    return hashlib.sha256(json.dumps({'accounts': snapshot['accounts'], 'policies': snapshot['policies']},
                                    sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class RefreshRunner:
    def __init__(self, service, *, cloud=None, gmail=None, login_factory=None, clock=None, sleep=time.sleep):
        self.service, self.store = service, service.store
        self.cloud = cloud or GitHubCloud()
        self.gmail = gmail
        self.login_factory = login_factory or (lambda: GladosEmailLogin(NativeJSONTransport([LOGIN_ORIGIN], cookies=True)))
        self.now = clock or (lambda: datetime.now(UTC))
        self.sleep = sleep
        self.store.db.executescript('''
CREATE TABLE IF NOT EXISTS refresh_runtime(cycle TEXT,account_key TEXT,metadata TEXT NOT NULL,PRIMARY KEY(cycle,account_key));
CREATE TABLE IF NOT EXISTS refresh_holds(account_key TEXT PRIMARY KEY,reason TEXT NOT NULL,created_at REAL NOT NULL);
''')

    def _meta(self, cycle, key):
        row = self.store.db.execute('SELECT metadata FROM refresh_runtime WHERE cycle=? AND account_key=?', (cycle,key)).fetchone()
        return json.loads(row[0]) if row else {}

    def _save_meta(self, cycle, key, value):
        # Strict non-secret schema. No email body, OTP, Cookie or OAuth token.
        allowed = {'attempt_id','requested_at','baseline_ids','target_email','generation','snapshot_digest',
                   'source_sha','publication_started','uploaded','run_id','dispatch_started','dispatch_uncertain'}
        if set(value) - allowed:
            raise RefreshError('invalid_runner_state')
        self.store.db.execute('INSERT INTO refresh_runtime VALUES(?,?,?) ON CONFLICT(cycle,account_key) DO UPDATE SET metadata=excluded.metadata',
                              (cycle,key,json.dumps(value,allow_nan=False)))

    def _target(self,key):
        row = self.store.identity(key)
        if row:
            return row['email']
        pending = self.store.db.execute('SELECT email FROM identity_pending WHERE account_key=?',(key,)).fetchone()
        return email_address(pending[0]) if pending else None

    def hold(self,key,reason):
        self.store.db.execute('INSERT INTO refresh_holds VALUES(?,?,?) ON CONFLICT(account_key) DO UPDATE SET reason=excluded.reason,created_at=excluded.created_at',
                              (key,reason,self.now().timestamp()))

    def accepted(self):
        value = self.service.setting('live_acceptance', {})
        return isinstance(value,dict) and value.get('runner_version') == RUNNER_VERSION and value.get('cloud_verified') is True

    def one(self,key=None,*,batch=False,output=lambda value:None):
        if batch and not self.accepted():
            raise RefreshError('live_acceptance_required')
        if self.service.secrets is None:
            raise RefreshError('secret_helper_unavailable')
        snapshot,_ = self.service.repo.load(allow_cache=False)
        keys = sorted(k for k,r in snapshot['accounts'].items() if r.get('enabled') is True)
        if key is not None:
            key = account_key(key)
            if key not in keys:
                raise RefreshError('unknown_account')
            keys = [key]
        cycle = self.now().astimezone(ZoneInfo('Asia/Taipei')).strftime('%Y-%m')
        with self.store.exclusive_run():
            # Resume any interrupted in-flight work before attempting another login.
            active = self.store.db.execute("SELECT cycle,account_key FROM jobs WHERE phase IN ('preflight','awaiting_code','candidate_verified','publish_pending','verify_pending') ORDER BY due LIMIT 1").fetchone()
            if active:
                if key is not None and key != active['account_key']:
                    raise RefreshError('unfinished_job_requires_resume')
                cycle,key = active['cycle'],active['account_key']
                if key not in snapshot['accounts']:
                    raise RefreshError('unknown_account')
                return self._execute(cycle,key,snapshot,output)
            for k in keys:
                target = self._target(k)
                held = self.store.db.execute('SELECT reason FROM refresh_holds WHERE account_key=?',(k,)).fetchone()
                reason = held[0] if held else '' if target else 'missing_identity'
                self.store.db.execute('INSERT OR IGNORE INTO jobs(cycle,account_key,phase,due,reason) VALUES(?,?,?,?,?)',
                                      (cycle,k,'manual' if reason else 'queued',self.now().timestamp(),reason))
            if key is not None:
                row = self.store.job(cycle,key)
                if row['phase'] in {'manual','done'}:
                    return self.summary(cycle)
            if key is not None:
                row = self.store.job(cycle,key)
                paused = self.store.db.execute("SELECT value FROM settings WHERE key='paused_until'").fetchone()
                if row['due'] > self.now().timestamp() or (paused and float(paused[0]) > self.now().timestamp()):
                    return self.summary(cycle)
                self.store._advance(cycle,key,'queued','preflight')
                job = self.store.job(cycle,key)
            else:
                job = self.store.claim(cycle,self.now())
            if job is None:
                return self.summary(cycle)
            return self._execute(cycle,job['account_key'],snapshot,output)

    def _execute(self,cycle,key,snapshot,output):
        phase = self.store.job(cycle,key)['phase']
        if phase in {'candidate_verified','publish_pending','verify_pending'}:
            return self._publish_resume(cycle,key,snapshot,output)
        if phase == 'awaiting_code':
            row = self.store.job(cycle,key)
            if self.now().timestamp() < row['due']:
                return {'ok':True,'event':'waiting','message':'上次验证码请求仍在有效期内；暂停重发，等待其结束。'}
            self.store.fail(cycle,key,'code_expired',self.now())
            return self.summary(cycle)
        client = self.login_factory()
        try:
            client.prepare_delivery()
            gmail = self.gmail
            if gmail is None:
                provider = self.service.provider()
                gmail = GmailReadOnly(NativeJSONTransport([GMAIL_ORIGIN]),provider.access_token,provider.mailbox)
            gmail.verify_mailbox()
            target = self._target(key)
            if not target:
                raise RefreshError('missing_identity')
            self.cloud.existing_secret(key)
            baseline = gmail.message_ids(after=self.now()-timedelta(minutes=15))
            client.check_delivery()
            requested = self.now()
            attempt = LoginAttempt(str(uuid.uuid4()),target,gmail.mailbox,requested,frozenset(baseline),frozenset({target}))
            meta = {'attempt_id':attempt.attempt_id,'requested_at':requested.timestamp(),
                    'baseline_ids':list(baseline),'target_email':target,'generation':str(uuid.uuid4()),
                    'snapshot_digest':state_digest(snapshot),'source_sha':self.cloud.head()}
            self._save_meta(cycle,key,meta)
            # All shared preflights above finish before consuming one login attempt.
            self.store.request_started(cycle,key,requested)
            client.request_code(attempt)
            output({'ok':True,'event':'waiting_code','account_key':key,'message':'验证码已请求；正在等待本次转发邮件。'})
            candidate = None
            while (self.now()-requested).total_seconds() < 560:
                client.check_delivery()
                ids = gmail.message_ids(after=requested-timedelta(seconds=2))
                envelopes = [gmail.read_message(mid) for mid in ids if mid not in attempt.baseline_ids]
                code = select_code(envelopes,attempt,self.now())
                if code is not None:
                    candidate = client.submit(code,known_account_keys={key},now=self.now())
                    break
                self.sleep(10)
            if candidate is None:
                self.store.fail(cycle,key,'mail_timeout',self.now())
                return self.summary(cycle)
            if candidate.account_key != key or candidate.email != target:
                raise RefreshError('identity_mismatch')
            if candidate.authorization:
                # The current cloud client has not been adapted to a second credential
                # context; never silently drop it and claim cookies alone are accepted.
                raise RefreshError('authorization_context_requires_manual')
            self.service.secrets.put('candidate-'+key,{'email':target,'key':key,'cookie':candidate.cookie_header,
                                                      'generation':meta['generation'],'verified_at':self.now().timestamp()})
            self.store.remember_verified_identity(key,target,self.now())
            self.store.candidate_verified(cycle,key,target)
            return self._publish_resume(cycle,key,snapshot,output)
        except RefreshError as exc:
            reason = str(exc)
            row = self.store.job(cycle,key)
            if row['phase'] in {'candidate_verified','publish_pending','verify_pending'}:
                raise
            manual = reason in {'challenge','identity_mismatch','permission_or_challenge','unregistered_authenticated_account',
                                'authorization_context_requires_manual','multiple_fresh_codes'}
            if manual:
                self.hold(key,'challenge' if reason in {'challenge','permission_or_challenge'} else 'identity_mismatch')
                self.store.fail(cycle,key,'challenge' if reason in {'challenge','permission_or_challenge'} else 'identity_mismatch',self.now())
            elif row['phase'] == 'preflight':
                self.store.db.execute("UPDATE jobs SET phase='queued',due=? WHERE cycle=? AND account_key=?",
                                      ((self.now()+timedelta(minutes=15)).timestamp(),cycle,key))
                self.store.pause_shared(self.now()+timedelta(minutes=15))
            else:
                # Preserve the outstanding request's expiry on an ambiguous or shared
                # failure. Resumption must not immediately issue another code.
                self.store.pause_shared(self.now()+timedelta(minutes=15))
            self.service.put_setting('last_refresh_result',{'reason':reason if reason in {'challenge','identity_mismatch'} else 'dependency_unavailable',
                                                          'account_key':key,'at':self.now().timestamp()})
            raise
        finally:
            close = getattr(client.transport,'close',None)
            if close:
                close()

    def _publish_resume(self,cycle,key,snapshot,output):
        meta = self._meta(cycle,key)
        candidate = self.service.secrets.get('candidate-'+key)
        if (not candidate or candidate.get('key') != key or candidate.get('generation') != meta.get('generation')
                or email_address(candidate.get('email','')) != self._target(key)):
            raise RefreshError('verified_candidate_required')
        current,_ = self.service.repo.load(allow_cache=False)
        if state_digest(current) != meta.get('snapshot_digest') or self.cloud.head() != meta.get('source_sha'):
            raise RefreshError('production_changed_recheck_required')
        phase = self.store.job(cycle,key)['phase']
        if phase == 'candidate_verified':
            meta['publication_started'] = self.now().timestamp()
            self._save_meta(cycle,key,meta)
            self.store.publish_started(cycle,key)
            self.cloud.upload(key,candidate['cookie'])
            meta['uploaded'] = True
            self._save_meta(cycle,key,meta)
            self.store.published(cycle,key)
        elif phase == 'publish_pending':
            # A prior upload may have succeeded; reconcile through a NEW cloud read.
            # Do not upload another credential or start another login here.
            self.store.published(cycle,key)
        if meta.get('dispatch_uncertain') and not meta.get('run_id'):
            raise RefreshError('cloud_dispatch_uncertain')
        if not meta.get('run_id'):
            meta['dispatch_started'] = self.now().timestamp()
            meta['dispatch_uncertain'] = True
            self._save_meta(cycle,key,meta)
            try:
                run_id = self.cloud.dispatch_status(key)
            except RefreshError as exc:
                if str(exc) == 'cloud_status_busy':
                    meta['dispatch_uncertain'] = False
                    self._save_meta(cycle,key,meta)
                raise
            meta.update(run_id=run_id,dispatch_uncertain=False)
            self._save_meta(cycle,key,meta)
        output({'ok':True,'event':'verifying','account_key':key,'message':'正在执行单账号云端只读验证；不触发兑换。'})
        for _ in range(18):
            if self.cloud.verify_status(meta['run_id'],key,candidate['email'],meta['source_sha'],meta['dispatch_started']):
                self.store.cloud_verified(cycle,key,candidate['email'])
                self.service.secrets.put('active-'+key,candidate)
                self.service.secrets.delete('candidate-'+key)
                self.store.db.execute('DELETE FROM identity_pending WHERE account_key=?',(key,))
                self.service.put_setting('live_acceptance',{'runner_version':RUNNER_VERSION,'cloud_verified':True,
                                                          'account_key':key,'run_id':meta['run_id'],'at':self.now().timestamp()})
                self.service.put_setting('last_refresh_result',{'reason':'success','account_key':key,'at':self.now().timestamp()})
                return self.summary(cycle)
            self.sleep(10)
        return {'ok':True,'event':'verification_pending','message':'云端验证尚未结束；状态已保存，下次继续验证，不重复登录。'}

    def summary(self,cycle):
        rows = self.store.snapshot(cycle)
        for r in rows:
            if r['phase'] == 'manual' and r['attempts'] >= 3:
                self.hold(r['account_key'],'three_attempts_failed')
        counts = {name:sum(r['phase'] == name for r in rows) for name in ('done','queued','manual')}
        result = {'ok':True,'event':'refresh_summary','cycle':cycle,**counts,
                  'message':f"本轮已完成 {counts['done']} 个，待执行/重试 {counts['queued']} 个，需要人工处理 {counts['manual']} 个。"}
        self.service.put_setting('last_refresh_summary',result)
        return result
