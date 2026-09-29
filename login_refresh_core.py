"""Login-refresh safety core. No GUI, login, secret upload, or scheduler side effects.

Integration must supply Gmail-verified transport metadata and authenticated GLaDOS
identity results. This module deliberately cannot enable production automation.
"""
from __future__ import annotations

import calendar
import contextlib
import fcntl
import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable

UTC = timezone.utc
SUBJECT = "glados authentication code"
ORIGINAL_SENDERS = frozenset({"noreply@glados.network"})
KEY = re.compile(r"[A-F0-9]{16}\Z")
EMAIL = re.compile(r"[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+\Z")
CODE = re.compile(r"(?:Your\s+verification\s+code\s+is|验证码(?:为|是)?)[\s:：]*([0-9]{6})(?![0-9])", re.I)
HEADER = re.compile(r"^(From|To|Date|Subject|发件人|寄件人|收件人|日期|主题|主旨)\s*[:：]\s*(.*)$", re.I)
NAMES = {"from":"from", "发件人":"from", "寄件人":"from", "to":"to", "收件人":"to", "date":"date", "日期":"date", "subject":"subject", "主题":"subject", "主旨":"subject"}


class RefreshError(ValueError):
    """Contains a fixed reason code only; never include received message contents."""


def email_address(value: str) -> str:
    if not isinstance(value, str) or not EMAIL.fullmatch(value.strip()):
        raise RefreshError("invalid_email")
    # GLaDOS email login is treated case-insensitively; do not strip dots or +tags.
    return value.strip().casefold()


def account_key(value: str) -> str:
    if not isinstance(value, str) or not KEY.fullmatch(value.upper()):
        raise RefreshError("invalid_account_key")
    return value.upper()


def aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise RefreshError("timezone_required")
    return value.astimezone(UTC)


def next_month(value: datetime) -> datetime:
    """One calendar month, clamping month-end; not a claim about cookie lifetime."""
    aware(value)
    year = value.year + (value.month == 12)
    month = value.month % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def parse_date(value: str) -> datetime:
    value = value.strip()
    # Apple Mail's Chinese forwarded header, as observed in a redacted sample.
    match = re.fullmatch(r"(\d{4})年(\d{1,2})月(\d{1,2})日\s+GMT([+-]\d{1,2})(?::(\d{2}))?\s+(\d{1,2}):(\d{2})(?::(\d{2}))?", value)
    try:
        if match:
            y, mo, d, offset, minutes, h, mi, sec = match.groups()
            offset_minutes = int(offset) * 60 + (-1 if offset.startswith('-') else 1) * int(minutes or 0)
            return datetime(int(y), int(mo), int(d), int(h), int(mi), int(sec or 0), tzinfo=timezone(timedelta(minutes=offset_minutes))).astimezone(UTC)
        try:
            return aware(parsedate_to_datetime(value))
        except (ValueError, TypeError):
            return aware(datetime.fromisoformat(value.replace('Z', '+00:00')))
    except (ValueError, TypeError, OverflowError) as exc:
        raise RefreshError("invalid_original_date") from None


def addresses(value: str) -> set[str]:
    try:
        return {email_address(address) for _, address in getaddresses([value]) if address}
    except (ValueError, TypeError):
        raise RefreshError("invalid_mail_address") from None


def canonical_subject(value: str) -> str:
    return re.sub(r"^(?:(?:fw|fwd|转发|轉寄)\s*[:：]\s*)+", "", value.strip(), flags=re.I).strip().casefold()


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style'}:
            self.hidden += 1
        if tag in {'br', 'p', 'div', 'li', 'tr', 'blockquote'}:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in {'script', 'style'}:
            self.hidden = max(0, self.hidden - 1)
        if tag in {'p', 'div', 'li', 'tr', 'blockquote'}:
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def message_text(message) -> str:
    part = message.get_body(preferencelist=('plain', 'html'))
    if part is None:
        raise RefreshError("missing_mail_body")
    try:
        text = part.get_content()
        if part.get_content_type() == 'text/html':
            parser = _Text()
            parser.feed(text)
            text = ''.join(parser.parts)
        return text.replace('\r\n', '\n').replace('\r', '\n')
    except (LookupError, UnicodeError, TypeError):
        raise RefreshError("invalid_mail_encoding") from None


@dataclass(frozen=True)
class MailEnvelope:
    """Metadata is from a Gmail RAW adapter, never from scraped HTML.

    authenticated_sender MUST be established by the adapter using Gmail's trusted
    Authentication-Results for the outer message, not a header in the forwarded body.
    It is deliberately required: absent/unknown authentication fails closed.
    """
    gmail_id: str
    mailbox: str
    received_at: datetime
    authenticated_sender: str | None
    raw: bytes = field(repr=False)


@dataclass(frozen=True)
class LoginAttempt:
    attempt_id: str
    target_email: str
    mailbox: str
    requested_at: datetime
    baseline_ids: frozenset[str] = frozenset()
    allowed_forwarders: frozenset[str] = frozenset()
    ttl_seconds: int = 600
    safety_margin_seconds: int = 30

    def validate(self):
        email_address(self.target_email)
        email_address(self.mailbox)
        aware(self.requested_at)
        if not self.attempt_id or not 60 <= self.ttl_seconds <= 600:
            raise RefreshError("invalid_attempt")
        if not 0 <= self.safety_margin_seconds < self.ttl_seconds:
            raise RefreshError("invalid_attempt")


@dataclass(frozen=True)
class CodeCandidate:
    gmail_id: str
    target_email: str
    issued_at: datetime
    attempt_id: str
    code: str = field(repr=False)


def parse_candidate(envelope: MailEnvelope, attempt: LoginAttempt, now: datetime) -> CodeCandidate:
    attempt.validate()
    now, requested, arrived = aware(now), aware(attempt.requested_at), aware(envelope.received_at)
    target = email_address(attempt.target_email)
    if email_address(envelope.mailbox) != email_address(attempt.mailbox):
        raise RefreshError("wrong_mailbox")
    if envelope.gmail_id in attempt.baseline_ids or arrived < requested or arrived > now + timedelta(seconds=15):
        raise RefreshError("not_new_for_attempt")
    if requested > now or now >= requested + timedelta(seconds=attempt.ttl_seconds):
        raise RefreshError("attempt_expired")
    if not envelope.raw or len(envelope.raw) > 512 * 1024:
        raise RefreshError("invalid_mail_size")
    try:
        outer = BytesParser(policy=policy.default).parsebytes(envelope.raw)
    except Exception:
        raise RefreshError("invalid_mime") from None
    if canonical_subject(str(outer.get('Subject', ''))) != SUBJECT:
        raise RefreshError("wrong_subject")
    senders = addresses(str(outer.get('From', '')))
    if len(senders) != 1 or envelope.authenticated_sender is None:
        raise RefreshError("untrusted_sender")
    sender = next(iter(senders))
    if email_address(envelope.authenticated_sender) != sender:
        raise RefreshError("untrusted_sender")
    permitted = {email_address(x) for x in attempt.allowed_forwarders}
    if sender in ORIGINAL_SENDERS:
        original = outer
        text = message_text(original)
        from_address = sender
        to_addresses = addresses(str(original.get('To', '')))
        issued = parse_date(str(original.get('Date', '')))
    else:
        if sender not in permitted:
            raise RefreshError("untrusted_forwarder")
        attached = [p for p in outer.walk() if p.get_content_type() == 'message/rfc822']
        if len(attached) > 1:
            raise RefreshError("ambiguous_forward")
        if attached:
            payload = attached[0].get_payload()
            if not isinstance(payload, list) or len(payload) != 1:
                raise RefreshError("ambiguous_forward")
            original = payload[0]
            original_senders = addresses(str(original.get('From', '')))
            if len(original_senders) != 1:
                raise RefreshError("wrong_original_sender")
            from_address = next(iter(original_senders))
            to_addresses = addresses(str(original.get('To', '')))
            issued = parse_date(str(original.get('Date', '')))
            if canonical_subject(str(original.get('Subject', ''))) != SUBJECT:
                raise RefreshError("wrong_original_subject")
            text = message_text(original)
        else:
            text = message_text(outer)
            fields: dict[str, str] = {}
            for line in text.splitlines():
                match = HEADER.match(line.strip().lstrip('> ').strip())
                if match:
                    name = NAMES[match.group(1).casefold()]
                    if name in fields:
                        raise RefreshError("ambiguous_forward")
                    fields[name] = match.group(2).strip()
            if set(fields) != {'from', 'to', 'date', 'subject'}:
                raise RefreshError("incomplete_original_headers")
            original_senders = addresses(fields['from'])
            if len(original_senders) != 1:
                raise RefreshError("wrong_original_sender")
            from_address = next(iter(original_senders))
            to_addresses = addresses(fields['to'])
            issued = parse_date(fields['date'])
            if canonical_subject(fields['subject']) != SUBJECT:
                raise RefreshError("wrong_original_subject")
    if from_address not in ORIGINAL_SENDERS:
        raise RefreshError("wrong_original_sender")
    if to_addresses != {target}:
        raise RefreshError("wrong_original_recipient")
    # Do not use the forwarding arrival timestamp as the OTP's issuance time.
    # Mail Date fields normally have second precision; tolerate only that quantization.
    if issued < requested.replace(microsecond=0) or issued > now + timedelta(seconds=15):
        raise RefreshError("not_issued_for_attempt")
    if now >= issued + timedelta(seconds=attempt.ttl_seconds - attempt.safety_margin_seconds):
        raise RefreshError("code_expired")
    codes = set(CODE.findall(text))
    if len(codes) != 1:
        raise RefreshError("missing_or_ambiguous_code")
    return CodeCandidate(envelope.gmail_id, target, issued, attempt.attempt_id, codes.pop())


def select_code(envelopes: Iterable[MailEnvelope], attempt: LoginAttempt, now: datetime) -> CodeCandidate | None:
    candidates = []
    for envelope in envelopes:
        try:
            candidates.append(parse_candidate(envelope, attempt, now))
        except RefreshError:
            continue
    if not candidates:
        return None
    if len({c.code for c in candidates}) != 1:
        raise RefreshError("multiple_fresh_codes")
    return max(candidates, key=lambda c: (c.issued_at, c.gmail_id))


SCHEMA = """
CREATE TABLE IF NOT EXISTS identity (
 account_key TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE,
 verified_at REAL NOT NULL, revision INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS jobs (
 cycle TEXT NOT NULL, account_key TEXT NOT NULL,
 phase TEXT NOT NULL DEFAULT 'queued', round INTEGER NOT NULL DEFAULT 1,
 attempts INTEGER NOT NULL DEFAULT 0, due REAL NOT NULL,
 reason TEXT NOT NULL DEFAULT '', PRIMARY KEY(cycle, account_key));
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


class RefreshStore:
    """Persistent, non-secret identity and queue state. Use one lock for a full run.

    RefreshStore does NOT perform authentication or claim a successful cloud check.
    Network adapters must perform and verify those actions before transitions.
    """
    RETRYABLE = frozenset({'mail_timeout', 'code_expired', 'temporary_login_failure'})
    MANUAL = frozenset({'challenge', 'identity_mismatch', 'forwarding_missing', 'mail_auth_required'})

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        flags = os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0)
        fd = os.open(self.path, flags, 0o600)
        try:
            if os.fstat(fd).st_uid != os.getuid():
                raise RefreshError('foreign_state_file')
            os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
        self.db = sqlite3.connect(self.path, isolation_level=None, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=DELETE')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript(SCHEMA)
        self._lock = None

    def close(self):
        if self._lock is not None:
            raise RefreshError('close_while_running')
        self.db.close()

    @contextlib.contextmanager
    def exclusive_run(self):
        if self._lock is not None:
            raise RefreshError('already_running')
        fd = os.open(str(self.path) + '.lock', os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RefreshError('already_running') from None
            self._lock = fd
            yield self
        finally:
            if self._lock == fd:
                self._lock = None
                fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _require_lock(self):
        if self._lock is None:
            raise RefreshError('execution_lock_required')

    @contextlib.contextmanager
    def _tx(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    def remember_verified_identity(self, key: str, email: str, verified_at: datetime):
        key, email = account_key(key), email_address(email)
        with self._tx():
            old = self.db.execute('SELECT email FROM identity WHERE account_key=?', (key,)).fetchone()
            if old and old['email'] != email:
                raise RefreshError('identity_mismatch')
            other = self.db.execute('SELECT account_key FROM identity WHERE email=?', (email,)).fetchone()
            if other and other['account_key'] != key:
                raise RefreshError('duplicate_identity')
            self.db.execute('INSERT INTO identity(account_key,email,verified_at) VALUES(?,?,?) '
                            'ON CONFLICT(account_key) DO UPDATE SET verified_at=excluded.verified_at',
                            (key, email, aware(verified_at).timestamp()))

    def import_successful_cache(self, rows: Iterable[dict], verified_at: datetime) -> dict[str, int]:
        result = {'imported': 0, 'ignored': 0, 'conflicts': 0}
        for row in rows:
            if not isinstance(row, dict) or row.get('ok') is not True or not row.get('email'):
                result['ignored'] += 1
                continue
            try:
                self.remember_verified_identity(row.get('account_key', ''), row['email'], verified_at)
                result['imported'] += 1
            except RefreshError:
                result['conflicts'] += 1
        return result

    def identity(self, key: str) -> dict | None:
        row = self.db.execute('SELECT * FROM identity WHERE account_key=?', (account_key(key),)).fetchone()
        return dict(row) if row else None

    def display_name(self, key: str, fallback: str = '') -> str:
        record = self.identity(key)
        return record['email'] if record else fallback or f'GLaDOS {account_key(key)[:6]}'

    def ensure_cycle(self, cycle: str, keys: Iterable[str], now: datetime):
        self._require_lock()
        if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', cycle):
            raise RefreshError('invalid_cycle')
        with self._tx():
            for key in sorted({account_key(k) for k in keys}):
                known = self.identity(key) is not None
                self.db.execute('INSERT OR IGNORE INTO jobs(cycle,account_key,phase,due,reason) VALUES(?,?,?,?,?)',
                                (cycle, key, 'queued' if known else 'manual', aware(now).timestamp(), '' if known else 'missing_identity'))

    def snapshot(self, cycle: str) -> list[dict]:
        return [dict(row) for row in self.db.execute('SELECT * FROM jobs WHERE cycle=? ORDER BY round, account_key', (cycle,))]

    def claim(self, cycle: str, now: datetime) -> dict | None:
        self._require_lock()
        if self.db.execute("SELECT 1 FROM jobs WHERE phase IN ('preflight','awaiting_code','candidate_verified','publish_pending','verify_pending') LIMIT 1").fetchone():
            raise RefreshError('unfinished_job_requires_resume')
        paused = self.db.execute("SELECT value FROM settings WHERE key='paused_until'").fetchone()
        if paused and float(paused['value']) > aware(now).timestamp():
            return None
        with self._tx():
            row = self.db.execute("SELECT * FROM jobs WHERE cycle=? AND phase='queued' ORDER BY round,due,account_key LIMIT 1", (cycle,)).fetchone()
            if not row or row['due'] > aware(now).timestamp():
                return None
            self.db.execute("UPDATE jobs SET phase='preflight' WHERE cycle=? AND account_key=?", (cycle, row['account_key']))
            return self.job(cycle, row['account_key'])

    def job(self, cycle: str, key: str) -> dict:
        row = self.db.execute('SELECT * FROM jobs WHERE cycle=? AND account_key=?', (cycle, account_key(key))).fetchone()
        if not row:
            raise RefreshError('unknown_job')
        return dict(row)

    def _advance(self, cycle: str, key: str, expected: str, new: str):
        self._require_lock()
        cursor = self.db.execute('UPDATE jobs SET phase=? WHERE cycle=? AND account_key=? AND phase=?', (new, cycle, account_key(key), expected))
        if cursor.rowcount != 1:
            raise RefreshError('invalid_transition')

    def request_started(self, cycle: str, key: str, now: datetime):
        """Persist intent BEFORE exactly one code request. Ambiguous sends are not replayed."""
        self._require_lock()
        with self._tx():
            row = self.job(cycle, key)
            if row['phase'] != 'preflight' or row['attempts'] >= 3:
                raise RefreshError('invalid_transition')
            self.db.execute("UPDATE jobs SET phase='awaiting_code',attempts=attempts+1,due=? WHERE cycle=? AND account_key=?",
                            ((aware(now)+timedelta(minutes=10)).timestamp(), cycle, account_key(key)))

    def candidate_verified(self, cycle: str, key: str, authenticated_email: str):
        self._require_lock()
        record = self.identity(key)
        if record is None:
            raise RefreshError('missing_identity')
        if self.job(cycle, key)['phase'] != 'awaiting_code':
            raise RefreshError('invalid_transition')
        if record['email'] != email_address(authenticated_email):
            self.fail(cycle, key, 'identity_mismatch', datetime.now(UTC))
            raise RefreshError('identity_mismatch')
        self._advance(cycle, key, 'awaiting_code', 'candidate_verified')

    def publish_started(self, cycle: str, key: str):
        """Candidate must already be safely held in Keychain before this transition."""
        self._advance(cycle, key, 'candidate_verified', 'publish_pending')

    def published(self, cycle: str, key: str):
        self._advance(cycle, key, 'publish_pending', 'verify_pending')

    def cloud_verified(self, cycle: str, key: str, authenticated_email: str):
        if not self.identity(key) or self.identity(key)['email'] != email_address(authenticated_email):
            raise RefreshError('identity_mismatch')
        self._advance(cycle, key, 'verify_pending', 'done')

    def pause_shared(self, until: datetime):
        self._require_lock()
        self.db.execute("INSERT INTO settings VALUES('paused_until',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(aware(until).timestamp()),))

    def fail(self, cycle: str, key: str, reason: str, now: datetime):
        self._require_lock()
        if reason not in self.RETRYABLE | self.MANUAL:
            raise RefreshError('unknown_failure_reason')
        row = self.job(cycle, key)
        if row['phase'] in {'done', 'manual', 'queued'}:
            raise RefreshError('invalid_transition')
        if row['phase'] in {'candidate_verified', 'publish_pending', 'verify_pending'}:
            raise RefreshError('resume_verification_do_not_relogin')
        if reason in self.RETRYABLE and row['phase'] != 'awaiting_code':
            raise RefreshError('no_login_attempt_to_retry')
        terminal = reason in self.MANUAL or row['attempts'] >= 3
        retry_at = aware(now) + timedelta(minutes=15 if row['attempts'] <= 1 else 120)
        self.db.execute('UPDATE jobs SET phase=?,round=round+1,due=?,reason=? WHERE cycle=? AND account_key=?',
                        ('manual' if terminal else 'queued', retry_at.timestamp(), reason, cycle, account_key(key)))

    def recovery_action(self, cycle: str, key: str, now: datetime) -> str:
        """A decision, not automatic mutation or a claim that an external write succeeded."""
        phase = self.job(cycle, key)['phase']
        if phase in {'publish_pending', 'verify_pending'}:
            return 'reconcile_cloud_without_new_login'
        if phase == 'candidate_verified':
            return 'resume_keychain_candidate'
        if phase == 'awaiting_code':
            return 'wait_for_request_expiry' if aware(now).timestamp() < self.job(cycle, key)['due'] else 'expire_attempt_then_retry'
        if phase == 'preflight':
            return 'resume_preflight'
        return phase
