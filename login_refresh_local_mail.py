"""Read-only receipt of GLaDOS codes from the user's existing Apple Mail account.

No Gmail OAuth/client JSON, IMAP password, AppleScript, Mail database, GUI scraping,
external mailbox connection or private email file is used. The native component
uses Mail's documented application data interface and requires normal macOS consent.
All raw message data is captured only into this worker's memory.
"""
from __future__ import annotations
import base64
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import subprocess
from typing import Callable
from login_refresh_core import MailEnvelope, RefreshError, aware, email_address
from login_refresh_http import trusted_outer_sender

UTC = timezone.utc
MAX_SOURCE = 512 * 1024
MAX_RESPONSE = 1024 * 1024
MAIL_ID = re.compile(r'mail:[1-9][0-9]{0,14}\Z')
ERRORS = frozenset({
    'mail_not_running', 'mail_instance_ambiguous', 'mail_permission_required',
    'mail_permission_unavailable', 'mail_receiver_not_found', 'mail_inbox_unavailable',
    'mail_data_unavailable', 'mail_check_failed', 'invalid_mail_window',
    'invalid_mail_id', 'mail_window_too_large', 'mail_source_unavailable',
    'invalid_mail_size', 'mail_message_unavailable', 'invalid_email',
    'installed_bundle_required', 'invalid_mail_request',
})

class LocalMailInbox:
    """Native receipt adapter. Background reads never prompt, launch or focus Mail."""
    def __init__(self, mailbox: str, executable: Path | str | None = None, *,
                 run: Callable = subprocess.run, clock: Callable = lambda: datetime.now(UTC)):
        self.mailbox = email_address(mailbox)
        self.executable = Path(executable) if executable is not None else (
            Path(__file__).resolve().parents[2] / 'MacOS/LocalMailReader')
        self.run, self.clock = run, clock

    def _call(self, op: str, **fields):
        p = self.executable
        if not p.is_absolute() or p.is_symlink() or not p.is_file() or not os.access(p, os.X_OK):
            raise RefreshError('mail_reader_unavailable')
        st = p.stat()
        if st.st_uid != os.getuid() or st.st_mode & 0o022:
            raise RefreshError('mail_reader_unsafe_permissions')
        allowed = {'permission':set(), 'authorize':set(), 'verify':set(), 'check':set(),
                   'list':{'after'}, 'read':{'message_id'}}
        if op=='check' and set(fields)=={'origin_email'}:
            fields['origin_email']=email_address(fields['origin_email'])
            allowed['check']={'origin_email'}
        if op not in allowed or set(fields) != allowed[op]:
            raise RefreshError('invalid_mail_request')
        request = {'op':op, **fields}
        if op not in {'permission','authorize'}:
            request['mailbox'] = self.mailbox
        try:
            result = self.run([str(p)], input=json.dumps(request, allow_nan=False).encode(),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
                              timeout=120 if op == 'authorize' else 45)
            if not result.stdout or len(result.stdout) > MAX_RESPONSE:
                raise ValueError
            data = json.loads(result.stdout)
            if not isinstance(data,dict):
                raise ValueError
        except (ValueError, TypeError, OSError, subprocess.SubprocessError):
            raise RefreshError('mail_data_unavailable') from None
        if result.returncode or data.get('ok') is not True:
            reason = data.get('reason')
            raise RefreshError(reason if reason in ERRORS else 'mail_data_unavailable')
        if op not in {'permission','authorize','check'}:
            if email_address(data.get('mailbox','')) != self.mailbox:
                raise RefreshError('wrong_mailbox')
        return data

    def permission(self) -> str:
        value = self._call('permission').get('permission')
        if value not in {'granted','required','denied','unavailable'}:
            raise RefreshError('mail_permission_unavailable')
        return value

    def authorize(self):
        data = self._call('authorize')
        if data.get('permission') != 'granted':
            raise RefreshError('mail_permission_required')
        self.verify_mailbox()

    def verify_mailbox(self):
        data = self._call('verify')
        if data.get('permission') != 'granted' or data.get('inbox_found') is not True:
            raise RefreshError('mail_receiver_not_found')

    def check_new_mail(self, origin_email=None):
        data = self._call('check',**({'origin_email':email_address(origin_email)} if origin_email else {}))
        if data.get('check_requested') is not True:
            raise RefreshError('mail_check_failed')

    def message_ids(self, *, after: datetime, limit: int = 100) -> tuple[str,...]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise RefreshError('invalid_mail_limit')
        delta = (aware(self.clock()) - aware(after)).total_seconds()
        if not -15 <= delta <= 86400:
            raise RefreshError('invalid_mail_window')
        data = self._call('list', after=aware(after).timestamp())
        ids = data.get('message_ids')
        if not isinstance(ids,list) or len(ids) > limit:
            raise RefreshError('mail_window_too_large')
        if any(not isinstance(mid,str) or not MAIL_ID.fullmatch(mid) for mid in ids):
            raise RefreshError('invalid_mail_id')
        return tuple(dict.fromkeys(ids))

    def read_message(self, message_id: str) -> MailEnvelope:
        if not isinstance(message_id,str) or not MAIL_ID.fullmatch(message_id):
            raise RefreshError('invalid_mail_id')
        data = self._call('read', message_id=message_id)
        if data.get('message_id') != message_id:
            raise RefreshError('mail_id_mismatch')
        encoded, stamp = data.get('source_base64'), data.get('received_at')
        if (not isinstance(encoded,str) or not encoded or len(encoded) > MAX_SOURCE * 4 // 3 + 8
                or type(stamp) not in (int,float) or not math.isfinite(stamp) or stamp <= 0):
            raise RefreshError('invalid_mail_source')
        try:
            raw = base64.b64decode(encoded, validate=True)
            received = datetime.fromtimestamp(stamp,UTC)
            if not raw or len(raw) > MAX_SOURCE:
                raise ValueError
        except (ValueError, OverflowError, OSError):
            raise RefreshError('invalid_mail_source') from None
        # Legacy field name gmail_id now carries a Mail ID. The selected Gmail
        # account's synchronized RAW source already includes transport auth headers;
        # no Gmail API request is made and body text is not treated as authentication.
        sender = trusted_outer_sender(raw)
        return MailEnvelope(message_id,self.mailbox,received,sender,raw)
