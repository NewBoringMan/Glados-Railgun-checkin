"""Internal JSON-lines entry point used by the existing Account Center UI.

No listener, browser automation, mail UI scraping, credential logging, or new app.
Network/credential mutations require an explicit action from the host. Automatic
batch execution remains acceptance-gated; a missing dependency is not a green check.
"""
from __future__ import annotations
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
import uuid

# -I excludes the script directory; explicitly admit only this sealed bundle folder.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from login_refresh_core import RefreshError, RefreshStore, account_key, email_address
from login_refresh_mail import mac_mail_instance
from login_refresh_oauth import DesktopClient, KeychainStore, OAuthProvider, PendingConsent
from login_refresh_notifications import NativeNotifications, NotificationOutbox

REPO = 'NewBoringMan/Glados-Railgun-checkin'
SUPPORT = Path.home() / 'Library/Application Support/GLaDOS Account Center'
MAX_JSON = 1024 * 1024

MESSAGES = {
    'github_unavailable':'无法连接 GitHub；保留已保存的邮箱，不修改账号。',
    'unknown_account':'此账号已不在 GitHub 账号列表中，请刷新后再试。',
    'invalid_email':'请输入完整邮箱地址。',
    'identity_mismatch':'邮箱与已确认的账号身份不一致，未覆盖原记录。',
    'duplicate_identity':'该邮箱已经绑定另一个账号，请核对后再保存。',
    'mail_auth_required':'请先导入 Google 桌面授权配置并授权收码邮箱。',
    'mail_reauthorization_required':'收码邮箱授权已失效，需要重新授权。',
    'mail_auth_binding_mismatch':'授权邮箱与当前收码邮箱不一致，请重新授权。',
    'mail_background_start_unavailable':'Mail 未运行。请打开 Mail；发码已暂停，未消耗账号重试次数。',
    'mail_probe_unavailable':'无法确认 Mail 是否运行，已暂停发码。',
    'keychain_locked_or_approval_required':'系统钥匙串未允许后台读取，请在本机解锁或完成授权后重试。',
    'keychain_unavailable':'凭据存储暂不可用，未回退到明文文件。',
    'secret_helper_unavailable':'应用内凭据组件缺失，请检查安装完整性。',
    'desktop_oauth_client_required':'请选择 Google 下载的桌面应用 OAuth JSON 配置，不是 Web 客户端配置。',
    'invalid_desktop_client':'Google 桌面授权配置不完整或格式不正确。',
    'unapproved_oauth_endpoint':'配置中的授权地址不是允许的 Google 官方地址。',
    'desktop_oauth_client_invalid':'Google 客户端配置无效，请核对桌面应用配置。',
    'gmail_readonly_scope_required':'需要且仅允许 Gmail 只读授权；未保存不匹配的权限。',
    'wrong_mailbox':'实际授权的邮箱不符，原有凭据没有被覆盖。',
    'oauth_endpoint_unavailable':'Google 授权服务暂不可用，请稍后重试；原授权未删除。',
    'consent_expired':'本次授权已超时，请重新开始。',
    'consent_denied':'授权已取消，未改动原有登录信息。',
    'live_acceptance_required':'自动刷新尚未通过单账号真实登录验收，不能启用批量或月度执行。',
    'challenge':'网站要求人机验证，请在正常登录页面完成；不会自动反复重试。',
    'already_running':'另一项账号维护任务正在执行，请等待完成。',
    'source_file_unavailable':'所选配置文件无法读取。',
    'invalid_request':'无法识别此操作，请更新 Account Center 后重试。',
    'read_only_identity':'这是已确认的邮箱身份，不能直接改成另一邮箱；请使用正常登录重新核对。',
    'missing_identity':'此账号尚未保存邮箱，请先填写并核对。',
    'account_disabled_during_refresh':'此账号已被停用，暂停其未完成维护，不继续发码或更新凭据。',
    'notification_permission_required':'请先在登录维护窗口开启系统通知并发送一次测试，避免失败后无法主动提醒。',
    'unfinished_job_requires_resume':'已有账号等待收码或云端验证，请先继续该账号，避免串号。',
    'preexisting_secret_required':'未找到此账号原有的 GitHub Secret，已停止，未创建新账号。',
    'cloud_operation_uncertain':'GitHub 操作结果暂不明确，已保留恢复记录；不会重复登录或盲目覆盖。',
    'publication_receipt_required':'上次凭据上传未收到明确成功回执；需要人工核对，不能用旧登录仍有效来冒充更新成功。',
    'cloud_status_busy':'已有状态查询正在运行，稍后继续本次验证，不重新发码。',
    'cloud_dispatch_uncertain':'云端验证任务是否创建暂不明确，需要核对后继续，不重复发送。',
    'cloud_verification_failed':'云端验证未通过；新登录不能标为恢复成功，请核对任务结果。',
    'cloud_identity_unverified':'云端结果尚不能确认账号邮箱；没有把账号标为恢复成功。',
    'production_changed_recheck_required':'执行期间账号、策略或生产版本发生变化；已暂停发布以免覆盖其他修改。',
    'verified_candidate_required':'缺少可恢复的已验证登录信息，已停止发布。',
    'authorization_context_requires_manual':'当前登录还依赖额外的官方认证上下文，需要人工核对，未丢弃该要求。',
    'mail_preflight_required':'发码前必须先准备 Mail，未发出验证码。',
    'mail_preflight_stale':'Mail 检查过期或电脑刚刚唤醒，已暂停并保留原请求。',
    'mail_stopped_resume_required':'Mail 已退出或重启；暂停队列，不反复抢开窗口。',
    'permission_or_challenge':'服务要求人工验证或拒绝权限，已停止自动尝试。',
    'login_business_rejected':'登录服务拒绝了本次请求；未将其他账号一并判定失效。',
    'network_unavailable':'网络暂不可用，已保留请求状态，不立即重复发码。',
    'rate_limited':'服务暂时限流，已暂停；不会更换域名或反复尝试。',
    'installed_bundle_required':'需要从正式安装的 Account Center 启用该功能。',
    'schedule_registration_failed':'本机定时任务注册失败，未宣称每月刷新已启用。',
    'schedule_path_conflict':'同名本机任务与当前配置不一致，未覆盖其他配置。',
}


def public_error(exc):
    reason = str(exc) if isinstance(exc, RefreshError) else 'internal_error'
    if reason not in MESSAGES:
        reason = 'internal_error'
    return {'ok':False,'reason':reason,'message':MESSAGES.get(reason,'操作未完成，未宣称账号已恢复。请查看依赖状态后重试。')}


def emit(value):
    print(json.dumps(value, ensure_ascii=False, allow_nan=False), flush=True)


class AccountRepository:
    """Use existing GitHub CLI authorization, exact repository paths, no token output."""
    def __init__(self, support=SUPPORT, run=subprocess.run):
        self.support, self.run = Path(support), run

    def _read(self, path):
        try:
            result=self.run(['/opt/homebrew/bin/gh','api',f'repos/{REPO}/contents/{path}?ref=master',
                             '-H','Accept: application/vnd.github.raw+json'],
                            stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=20,check=False)
            if result.returncode or len(result.stdout)>MAX_JSON:raise ValueError
            data=json.loads(result.stdout)
            if not isinstance(data,dict):raise ValueError
            return data
        except (OSError,subprocess.SubprocessError,ValueError,TypeError):
            raise RefreshError('github_unavailable') from None

    def load(self, allow_cache=False):
        source='github'
        try:
            accounts=self._read('.github/glados/accounts.json')['accounts']
            policies=self._read('.github/glados/account_policies.json')
            if not isinstance(accounts,dict) or not accounts or len(accounts)>500:raise ValueError
            for key,record in accounts.items():
                account_key(key)
                if not isinstance(record,dict):raise ValueError
            snapshot={'accounts':accounts,'policies':policies,'fetched_at':time.time()}
            self.support.mkdir(parents=True,exist_ok=True,mode=0o700)
            self._save(snapshot)
        except (RefreshError,ValueError,KeyError):
            if not allow_cache:raise RefreshError('github_unavailable') from None
            snapshot=self._cached()
            if not snapshot:raise RefreshError('github_unavailable') from None
            source='last_saved'
        return snapshot,source

    def _save(self,snapshot):
        path=self.support/'refresh-account-directory.json'
        if path.is_symlink():raise RefreshError('invalid_request')
        # Metadata cache contains keys/labels/policies only, never emails or secrets.
        raw=json.dumps(snapshot,ensure_ascii=False).encode()
        temp=path.with_name(path.name+'.new-'+str(os.getpid()))
        fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        try:
            with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
            os.replace(temp,path)
        finally:
            if temp.exists():temp.unlink()

    def _cached(self):
        path=self.support/'refresh-account-directory.json'
        if path.is_symlink() or not path.is_file() or path.stat().st_size>MAX_JSON:return None
        try:
            value=json.loads(path.read_text())
            if not isinstance(value,dict) or not isinstance(value.get('accounts'),dict) or not isinstance(value.get('policies'),dict):return None
            for key,record in value['accounts'].items():
                account_key(key)
                if not isinstance(record,dict):return None
            return value
        except (ValueError,OSError):return None


class AppService:
    def __init__(self,support=SUPPORT,repository=None,secrets_store=None,mail_probe=mac_mail_instance,notifications=None):
        self.support=Path(support)
        self.store=RefreshStore(self.support/'login-refresh.sqlite')
        self.repo=repository or AccountRepository(self.support)
        self.secrets=secrets_store
        self.mail_probe=mail_probe
        notification_helper=Path(__file__).resolve().parents[2]/'MacOS/RefreshNotifications'
        self.notices=NotificationOutbox(self.store.db,notifications or NativeNotifications(notification_helper))
        self.store.db.executescript('''
CREATE TABLE IF NOT EXISTS identity_pending(account_key TEXT PRIMARY KEY,email TEXT NOT NULL UNIQUE,updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS ui_settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
''')

    def close(self):self.store.close()

    def setting(self,key,default=None):
        row=self.store.db.execute('SELECT value FROM ui_settings WHERE key=?',(key,)).fetchone()
        return json.loads(row[0]) if row else default

    def put_setting(self,key,value):
        self.store.db.execute('INSERT INTO ui_settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                              (key,json.dumps(value,ensure_ascii=False,allow_nan=False)))

    def snapshot(self):
        snapshot,source=self.repo.load(allow_cache=True)
        cache={}
        path=self.support/'status-cache.json'
        if path.is_file() and not path.is_symlink() and path.stat().st_size<=MAX_JSON:
            try:
                for row in json.loads(path.read_text()):
                    if isinstance(row,dict) and isinstance(row.get('account_key'),str):cache[row['account_key'].upper()]=row
            except (OSError,ValueError,TypeError):pass
        # Only import authenticated historical cache values; never clear on failure.
        conflicts=0
        try:
            with self.store.exclusive_run():
                for key,row in cache.items():
                    if key not in snapshot['accounts'] or row.get('ok') is not True or not row.get('email'):continue
                    try:self.store.remember_verified_identity(key,row['email'],datetime.fromtimestamp(path.stat().st_mtime,timezone.utc))
                    except RefreshError:conflicts+=1
        except RefreshError as exc:
            if str(exc) != 'already_running':raise
        rows=[]
        for key,record in snapshot['accounts'].items():
            known=self.store.identity(key)
            pending=self.store.db.execute('SELECT email FROM identity_pending WHERE account_key=?',(key,)).fetchone()
            row=cache.get(key,{})
            maintenance=self.store.db.execute('SELECT phase,reason,attempts FROM jobs WHERE account_key=? ORDER BY cycle DESC LIMIT 1',(key,)).fetchone()
            maintenance_reason=maintenance['reason'] if maintenance else ''
            policy=snapshot['policies'].get('accounts',{}).get(key,snapshot['policies'].get('default','auto'))
            rows.append({'key':key,'label':str(record.get('label',key)),
                         'email':known['email'] if known else pending[0] if pending else '',
                         'identity_kind':'confirmed' if known else 'pending' if pending else 'missing',
                         'enabled':record.get('enabled') is True,'auto_exchange':record.get('autoExchange') is True,
                         'policy':str(policy),'points':row.get('points_total') if isinstance(row.get('points_total'),(int,float)) else None,
                         'last_status_ok':row.get('ok') is True,
                         'maintenance_phase':maintenance['phase'] if maintenance else 'not_started',
                         'maintenance_attempts':maintenance['attempts'] if maintenance else 0,
                         'maintenance_message':MESSAGES.get(maintenance_reason,'三次尝试均失败，需要人工处理。' if maintenance and maintenance['attempts']>=3 and maintenance['phase']=='manual' else '')})
        rows.sort(key=lambda r:((r['email'] or r['label']).casefold(),r['key']))
        try:mail_running=bool(self.mail_probe());mail_error=''
        except RefreshError:mail_running=False;mail_error='mail_probe_unavailable'
        gmail={'configured':False,'authorized':False,'reason':'mail_auth_required'}
        if self.secrets is not None:
            try:
                client=self.secrets.get('gmail-client');token=self.secrets.get('gmail-token')
                mailbox=self.setting('mailbox','')
                matched=bool(client and token and token.get('mailbox')==mailbox and token.get('client_id')==client.get('client_id'))
                gmail={'configured':bool(client),'authorized':matched,
                       'reason':'' if matched else 'mail_auth_required'}
                if gmail['authorized'] and token.get('refresh_expires_at') and token['refresh_expires_at']<=time.time()+60:
                    gmail.update(authorized=False,reason='mail_reauthorization_required')
            except RefreshError as exc:gmail['reason']=str(exc) if str(exc) in MESSAGES else 'keychain_unavailable'
        acceptance=self.setting('live_acceptance',{})
        from login_refresh_runner import RUNNER_VERSION
        accepted=isinstance(acceptance,dict) and acceptance.get('runner_version')==RUNNER_VERSION and acceptance.get('cloud_verified') is True
        return {'ok':True,'event':'snapshot','accounts':rows,'repository_source':source,
                'mail_running':mail_running,'mail_reason':mail_error,'mailbox':self.setting('mailbox',''),
                'gmail':gmail,'identity_conflicts':conflicts,'confirmed_count':sum(r['identity_kind']=='confirmed' for r in rows),
                'schedule_enabled':self.setting('monthly_enabled',False) is True,
                'automation_ready':accepted and gmail['authorized'],
                'automation_reason':'已通过单账号验证；批量队列可继续处理已保存邮箱的账号。' if accepted else '请先完成收码授权并通过一个账号的真实登录与云端验证。',
                'last_live_gate':'网站要求人机验证时，该账号暂停并交给人工；不会自动绕过。',
                'last_summary':self.setting('last_refresh_summary',{}),
                'last_result':self.setting('last_refresh_result',{}),
                'notifications':self.notices.status()}

    def save_email(self,key,value):
        key,email=account_key(key),email_address(value)
        snapshot,_=self.repo.load(allow_cache=False)
        if key not in snapshot['accounts']:raise RefreshError('unknown_account')
        with self.store.exclusive_run():
            known=self.store.identity(key)
            if known:
                if known['email']!=email:raise RefreshError('read_only_identity')
                return {'ok':True,'message':'此邮箱已经保存。'}
            if self.store.db.execute('SELECT account_key FROM identity WHERE email=? AND account_key<>?',(email,key)).fetchone():
                raise RefreshError('duplicate_identity')
            other=self.store.db.execute('SELECT account_key FROM identity_pending WHERE email=? AND account_key<>?',(email,key)).fetchone()
            if other:raise RefreshError('duplicate_identity')
            self.store.db.execute('INSERT INTO identity_pending VALUES(?,?,?) ON CONFLICT(account_key) DO UPDATE SET email=excluded.email,updated_at=excluded.updated_at',
                                  (key,email,time.time()))
        return {'ok':True,'message':'邮箱已保存到本机；标记为待登录核实，未修改 GitHub 或 Cookie。'}

    def configure_mailbox(self,value):
        self.put_setting('mailbox',email_address(value))
        return {'ok':True,'message':'收码邮箱已保存。需授权同一 Gmail 邮箱。'}

    def import_client(self,value):
        if self.secrets is None:raise RefreshError('secret_helper_unavailable')
        client=DesktopClient.from_download(value)
        self.secrets.put('gmail-client',{'client_id':client.client_id,'client_secret':client.client_secret})
        return {'ok':True,'message':'Google 桌面配置已存入系统钥匙串；下一步授权收码邮箱。'}

    def provider(self):
        if self.secrets is None:raise RefreshError('secret_helper_unavailable')
        client=self.secrets.get('gmail-client')
        if not client:raise RefreshError('mail_auth_required')
        mailbox=self.setting('mailbox','')
        if not mailbox:raise RefreshError('invalid_email')
        return OAuthProvider(DesktopClient(client['client_id'],client['client_secret']),mailbox,self.secrets)

    def consent(self,output=emit):
        # Explicit user request only. URL is passed straight to the current host's
        # memory; no global browser open, focus takeover or file/console logging.
        with PendingConsent(self.provider()) as pending:
            output({'ok':True,'event':'consent_url','url':pending.authorization_url,
                    'message':'请点击“打开授权网页”，完成 Google 授权后返回。'})
            pending.wait()
        return {'ok':True,'event':'authorized','message':'收码邮箱授权成功，凭据已保存在系统钥匙串。'}

    def check_mailbox(self):
        provider=self.provider()
        token=provider.access_token()
        if email_address(provider.profile_check(token))!=provider.mailbox:raise RefreshError('wrong_mailbox')
        return {'ok':True,'message':'收码邮箱授权及在线身份检查通过。此检查未发出验证码。'}

    def maintain(self,key=None,batch=False,output=emit):
        from login_refresh_runner import RefreshRunner
        runner=RefreshRunner(self)
        return runner.one(key,batch=batch,output=output)

    def set_monthly(self,enabled):
        from login_refresh_runner import RefreshRunner
        from login_refresh_schedule import LocalSchedule
        if type(enabled) is not bool:raise RefreshError('invalid_request')
        if enabled:
            if not RefreshRunner(self).accepted():raise RefreshError('live_acceptance_required')
            self.check_mailbox()
            if self.notices.status()['authorization'] not in {'authorized','provisional'}:
                raise RefreshError('notification_permission_required')
            if not self.mail_probe():raise RefreshError('mail_background_start_unavailable')
            LocalSchedule().enable()
            self.put_setting('monthly_enabled',True)
            return {'ok':True,'message':'每月串行维护已启用；Mail 或网络不可用时暂停，不会批量消耗重试次数。'}
        self.put_setting('monthly_enabled',False)
        LocalSchedule().disable()
        return {'ok':True,'message':'每月自动维护已停用；已保存邮箱、登录资料和 GitHub 签到不受影响。'}

    def collect_notifications(self,reason=None):
        self.notices.observe_jobs()
        if reason:self.notices.shared_failure(reason)
        return self.notices.flush()

    def dispatch(self,request):
        action=request.get('action')
        if action=='snapshot':return self.snapshot()
        if action=='save_email':return self.save_email(request.get('key',''),request.get('email',''))
        if action=='configure_mailbox':return self.configure_mailbox(request.get('mailbox',''))
        if action=='import_client':return self.import_client(request.get('config'))
        if action=='authorize_mailbox':return self.consent()
        if action=='check_mailbox':return self.check_mailbox()
        if action=='check_notifications':
            status=self.notices.status()
            return {'ok':True,'event':'notification_status',**status,
                    'message':'通知已获授权。' if status['authorization'] in {'authorized','provisional'} else '请先在本窗口开启通知权限；未投递提醒仍保留在本机。'}
        if action=='test_notification':
            self.notices.enqueue('user-test:'+str(uuid.uuid4()),'test')
            sent=self.notices.flush(limit=5)
            return {'ok':sent['accepted']>0,'event':'notification_test',
                    'message':'通知请求已交给系统；是否显示受系统通知设置影响。' if sent['accepted'] else '通知未获系统接收；请检查权限，测试记录保留待投递。'}
        if action=='refresh_one':return self.maintain(request.get('key',''))
        if action=='refresh_all':return self.maintain(batch=True)
        if action=='enable_monthly':return self.set_monthly(True)
        if action=='disable_monthly':return self.set_monthly(False)
        if action=='scheduled_tick':
            if self.setting('monthly_enabled',False) is not True:return {'ok':True,'event':'schedule_disabled'}
            return self.maintain(batch=True)
        raise RefreshError('invalid_request')


def main():
    service=None
    request={}
    failure_reason=None
    try:
        if sys.argv[1:]==['--tick']:
            request={'action':'scheduled_tick'}
        elif len(sys.argv)==1:
            line=sys.stdin.buffer.readline(MAX_JSON+1)
            if len(line)>MAX_JSON:raise RefreshError('invalid_request')
            request=json.loads(line)
        else:raise RefreshError('invalid_request')
        if not isinstance(request,dict):raise RefreshError('invalid_request')
        # Bundled layout: Resources/LoginRefresh/*.py; MacOS/RefreshSecretStore.
        root=Path(__file__).resolve().parent
        helper=root.parent.parent/'MacOS/RefreshSecretStore'
        secrets_store=None
        if helper.exists():secrets_store=KeychainStore(helper)
        service=AppService(secrets_store=secrets_store)
        emit(service.dispatch(request))
    except Exception as exc:
        failure_reason=str(exc) if isinstance(exc,RefreshError) else 'internal_error'
        emit(public_error(exc))
    finally:
        if service:
            try:
                if request.get('action') in {'refresh_one','refresh_all','scheduled_tick'}:
                    service.collect_notifications(failure_reason)
            except Exception:
                # A notification failure must not falsify login state or expose data.
                # The durable outbox remains pending for the next authorized delivery.
                pass
            service.close()


if __name__=='__main__':main()
