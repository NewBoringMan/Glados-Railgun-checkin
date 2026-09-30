"""Private durable notice outbox. Mail errors use the same existing notification path.

No email/code/cookie/token values enter a notification. Enqueueing is not delivery.
Denied permissions retain pending records instead of claiming a visible notification.
"""
from __future__ import annotations
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import time
KINDS={'account_manual','shared_dependency','verification_pending','test'}
AUTHORISED={'authorized','provisional'}
class NotificationFailure(ValueError):pass
class NativeNotifications:
    def __init__(self,executable,*,run=subprocess.run):self.executable=Path(executable);self.run=run
    def request(self,payload):
        path=self.executable
        if not path.is_absolute() or path.is_symlink() or not path.is_file() or not os.access(path,os.X_OK):return {'ok':False,'reason':'notification_component_unavailable'}
        stat=path.stat()
        if stat.st_uid!=os.getuid() or stat.st_mode&0o022:return {'ok':False,'reason':'notification_component_unavailable'}
        try:
            result=self.run([str(path)],input=json.dumps(payload).encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=12,check=False)
            if result.returncode or len(result.stdout)>32768:raise ValueError
            response=json.loads(result.stdout)
            if not isinstance(response,dict):raise ValueError
            return response
        except (OSError,ValueError,subprocess.SubprocessError):return {'ok':False,'reason':'notification_service_unavailable'}
    def status(self):
        result=self.request({'op':'status'})
        return result['authorization'] if result.get('ok') is True and result.get('authorization') in {'authorized','provisional','denied','not_determined'} else 'unavailable'
    def send(self,identifier,kind,count):return self.request({'op':'send','id':identifier,'kind':kind,'count':count})
class NotificationOutbox:
    def __init__(self,db,transport,*,clock=time.time):
        self.db,self.transport,self.clock=db,transport,clock
        self.db.executescript('''
CREATE TABLE IF NOT EXISTS refresh_notices (
 notice_id TEXT PRIMARY KEY, kind TEXT NOT NULL, item_count INTEGER NOT NULL,
 created_at REAL NOT NULL, next_attempt REAL NOT NULL, accepted_at REAL,
 delivery_attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT NOT NULL DEFAULT '');
''')
    def enqueue(self,scope,kind,count=1):
        if not isinstance(scope,str) or not 1<=len(scope)<=256 or kind not in KINDS or type(count) is not int or not 1<=count<=500:raise NotificationFailure('invalid_notice')
        now=self.clock()
        if type(now) not in (int,float) or not math.isfinite(now):raise NotificationFailure('invalid_clock')
        identifier='glados-refresh-'+hashlib.sha256((kind+'\0'+scope).encode()).hexdigest()[:32]
        self.db.execute('INSERT OR IGNORE INTO refresh_notices (notice_id,kind,item_count,created_at,next_attempt) VALUES(?,?,?,?,?)',(identifier,kind,count,now,now))
        return identifier
    def observe_jobs(self):
        tables={row[0] for row in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'jobs' not in tables:return
        for row in self.db.execute("SELECT cycle,account_key,reason,attempts FROM jobs WHERE phase='manual'"):
            cycle,key,reason,attempts=tuple(row)
            if reason=='missing_identity':continue
            if attempts>=3 or reason in {'challenge','identity_mismatch','forwarding_missing','mail_auth_required'}:self.enqueue(f'{cycle}:{key}:{reason}','account_manual')
    def shared_failure(self,reason):
        allowed={'mail_not_running','mail_permission_required','mail_permission_unavailable','mail_reader_unavailable','mail_receiver_not_found','mail_inbox_unavailable','mail_auth_required','mail_reauthorization_required','mail_background_start_unavailable','mail_stopped_resume_required','mail_probe_unavailable','keychain_unavailable'}
        if reason in allowed:
            day=time.strftime('%Y-%m-%d',time.gmtime(self.clock()));self.enqueue(day+':'+reason,'shared_dependency')
        elif reason in {'cloud_operation_uncertain','cloud_dispatch_uncertain','publication_receipt_required','cloud_identity_unverified','verified_candidate_required'}:
            day=time.strftime('%Y-%m-%d',time.gmtime(self.clock()));self.enqueue(day+':'+reason,'verification_pending')
    def status(self):
        pending=self.db.execute('SELECT COUNT(*) FROM refresh_notices WHERE accepted_at IS NULL').fetchone()[0]
        return {'authorization':self.transport.status(),'pending':pending}
    def flush(self,limit=5):
        if type(limit) is not int or not 1<=limit<=10:raise NotificationFailure('invalid_flush_limit')
        authorization=self.transport.status()
        if authorization not in AUTHORISED:return {'accepted':0,'authorization':authorization}
        accepted=0
        for _ in range(limit):
            now=self.clock();self.db.execute('BEGIN IMMEDIATE')
            try:
                row=self.db.execute('SELECT notice_id,kind,item_count FROM refresh_notices WHERE accepted_at IS NULL AND next_attempt<=? ORDER BY created_at,notice_id LIMIT 1',(now,)).fetchone()
                if row is None:self.db.execute('COMMIT');break
                identifier,kind,count=tuple(row)
                self.db.execute('UPDATE refresh_notices SET next_attempt=?,delivery_attempts=delivery_attempts+1 WHERE notice_id=?',(now+300,identifier));self.db.execute('COMMIT')
            except BaseException:self.db.execute('ROLLBACK');raise
            try:response=self.transport.send(identifier,kind,count)
            except Exception:response={'ok':False,'reason':'notification_service_unavailable'}
            if response.get('ok') is True and response.get('accepted_by_system') is True:
                self.db.execute('UPDATE refresh_notices SET accepted_at=?,last_error=? WHERE notice_id=?',(self.clock(),'',identifier));accepted+=1
            else:
                reason=response.get('reason')
                if reason not in {'notification_permission_required','notification_denied','notification_service_unavailable'}:reason='notification_service_unavailable'
                self.db.execute('UPDATE refresh_notices SET last_error=? WHERE notice_id=?',(reason,identifier))
        return {'accepted':accepted,'authorization':authorization}
