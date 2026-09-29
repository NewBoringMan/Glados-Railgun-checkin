"""Recover non-secret email identities from a hash-verified successful local snapshot.

Does not alter status-cache, remote labels, current health, Cookies, or Secrets.
A historical identity remains usable for display even when live authentication fails.
Its provenance is explicitly historical; no current verification is implied.
"""
from __future__ import annotations
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from login_refresh_core import RefreshError, RefreshStore, account_key, aware, email_address


class IdentityRecovery:
    @staticmethod
    def inspect(snapshot: Path | str, expected_sha256: str, registered_keys: Iterable[str]):
        path=Path(snapshot)
        if not path.is_file() or path.is_symlink():raise RefreshError('identity_source_unavailable')
        if path.stat().st_size>2*1024*1024:raise RefreshError('identity_source_too_large')
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=expected_sha256:raise RefreshError('identity_source_changed')
        try:rows=json.loads(raw)
        except ValueError:raise RefreshError('invalid_identity_snapshot') from None
        if not isinstance(rows,list):raise RefreshError('invalid_identity_snapshot')
        known={account_key(k) for k in registered_keys}
        candidates={}; emails={};ignored=0
        for row in rows:
            if not isinstance(row,dict) or row.get('ok') is not True or not row.get('email'):
                ignored+=1;continue
            key=account_key(row.get('account_key',''));email=email_address(row['email'])
            if key not in known:ignored+=1;continue
            if (key in candidates and candidates[key]!=email) or (email in emails and emails[email]!=key):
                raise RefreshError('identity_source_conflict')
            candidates[key]=email;emails[email]=key
        return candidates,ignored

    @staticmethod
    def apply(store:RefreshStore,candidates:dict[str,str],*,source_sha256:str,snapshot_at:datetime,now:datetime):
        # Conflict-check the entire set before inserting; never partly apply a batch.
        source_time=aware(snapshot_at).timestamp();import_time=aware(now).timestamp()
        if source_time>import_time:raise RefreshError('future_identity_snapshot')
        store.db.execute('CREATE TABLE IF NOT EXISTS identity_origin ('
                         'account_key TEXT PRIMARY KEY, source_kind TEXT NOT NULL, '
                         'source_sha256 TEXT NOT NULL, snapshot_at REAL NOT NULL, imported_at REAL NOT NULL)')
        inserted=existing=0
        with store.exclusive_run():
            with store._tx():
                for key,email in candidates.items():
                    account_key(key);email_address(email)
                    old=store.identity(key)
                    other=store.db.execute('SELECT account_key FROM identity WHERE email=?',(email,)).fetchone()
                    if (old and old['email']!=email) or (other and other['account_key']!=key):
                        raise RefreshError('identity_destination_conflict')
                for key,email in candidates.items():
                    if store.identity(key):existing+=1;continue
                    store.db.execute('INSERT INTO identity(account_key,email,verified_at) VALUES(?,?,?)',(key,email,source_time))
                    store.db.execute('INSERT INTO identity_origin VALUES(?,?,?,?,?)',
                                     (key,'historical_successful_status',source_sha256,source_time,import_time))
                    inserted+=1
        return {'imported':inserted,'already_present':existing,'live_health_changed':False}
