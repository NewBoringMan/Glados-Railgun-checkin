"""Bounded native HTTP adapters for the candidate Account Center refresh module.

No browser automation, device-fingerprint generation, credential publication, or
scheduler activation. URL contracts were inspected in the site's own client on
2026-09-29. A single authorization request was accepted by glados.cloud; a full
fresh-email login has NOT yet been accepted in the target environment.
"""
from __future__ import annotations

import base64
import hashlib
import http.cookiejar
import json
import math
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from typing import Callable, Iterable, Mapping, Protocol

from login_refresh_core import (
    CodeCandidate, LoginAttempt, MailEnvelope, RefreshError, aware, email_address,
)

UTC = timezone.utc
GMAIL_ORIGIN = "https://gmail.googleapis.com"
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
# No cross-domain probing with a code or credential. New origins need explicit review.
LOGIN_ORIGIN = "https://glados.cloud"
LOGIN_SITE = "glados.network"
MAX_RESPONSE = 1024 * 1024
MAX_RAW_MAIL = 512 * 1024


class HTTPFailure(RefreshError):
    def __init__(self, reason: str, *, ambiguous: bool = False, retry_after: int | None = None):
        super().__init__(reason)
        self.reason = reason
        self.ambiguous = ambiguous
        self.retry_after = retry_after


@dataclass(frozen=True)
class JSONResponse:
    status: int
    data: dict = field(repr=False)
    headers: Mapping[str, str] = field(default_factory=dict, repr=False)


class Transport(Protocol):
    def request(self, method: str, url: str, *, headers: dict | None = None,
                payload: dict | None = None) -> JSONResponse: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class NativeJSONTransport:
    """Exact-origin HTTPS client. Uses normal TLS verification and never retries.

    The optional cookie jar must be dedicated to ONE login attempt. No user browser
    profile is read or altered. Request/response contents are never logged here.
    """
    def __init__(self, origins: Iterable[str], *, cookies: bool = False, timeout: float = 18):
        self.origins = frozenset(origins)
        if not self.origins or any(self._origin(x) != x for x in self.origins):
            raise RefreshError("invalid_transport_origin")
        if not 1 <= timeout <= 30:
            raise RefreshError("invalid_transport_timeout")
        self.timeout = timeout
        self.jar = http.cookiejar.CookieJar() if cookies else None
        handlers = [_NoRedirect()]
        if self.jar is not None:
            handlers.append(urllib.request.HTTPCookieProcessor(self.jar))
        self.opener = urllib.request.build_opener(*handlers)

    @staticmethod
    def _origin(url: str) -> str:
        try:
            p = urllib.parse.urlsplit(url)
            if (p.scheme != "https" or not p.hostname or p.username or p.password
                    or p.port not in (None, 443) or p.fragment):
                raise ValueError
            return f"https://{p.hostname}"
        except (ValueError, TypeError):
            raise RefreshError("invalid_transport_origin") from None

    def request(self, method: str, url: str, *, headers=None, payload=None) -> JSONResponse:
        if self._origin(url) not in self.origins:
            raise RefreshError("unapproved_transport_origin")
        if method not in {"GET", "POST"} or (method == "GET" and payload is not None):
            raise RefreshError("invalid_transport_method")
        request_headers = {"Accept": "application/json"}
        request_headers.update(headers or {})
        if any("\r" in str(v) or "\n" in str(v) for v in request_headers.values()):
            raise RefreshError("invalid_header")
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if body is not None:
            request_headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=body, headers=request_headers, method=method)
        try:
            response = self.opener.open(request, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            try:
                retry = (exc.headers or {}).get("Retry-After", "")
                delay = int(retry) if retry.isdigit() else None
                status = exc.code
            finally:
                exc.close()
            reason = ("redirect_blocked" if 300 <= status < 400 else
                      "rate_limited" if status == 429 else
                      "permission_or_challenge" if status in {401, 403} else
                      "remote_http_error")
            raise HTTPFailure(reason, ambiguous=(method == "POST" and status >= 500), retry_after=delay) from None
        except (urllib.error.URLError, socket.timeout, TimeoutError, OSError):
            raise HTTPFailure("network_unavailable", ambiguous=method == "POST") from None
        with response:
            try:
                raw = response.read(MAX_RESPONSE + 1)
                if len(raw) > MAX_RESPONSE:
                    raise HTTPFailure("response_too_large", ambiguous=method == "POST")
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise ValueError
            except (socket.timeout, TimeoutError, OSError):
                raise HTTPFailure("response_interrupted", ambiguous=method == "POST") from None
            except (ValueError, UnicodeError):
                raise HTTPFailure("invalid_json_response", ambiguous=method == "POST") from None
            return JSONResponse(response.status, value)

    def credential_header(self) -> str:
        """Return exactly the session cookies applicable to the pinned origin."""
        if self.jar is None:
            raise RefreshError("cookie_jar_required")
        eligible = urllib.request.Request(LOGIN_ORIGIN + "/api/user/session")
        self.jar.add_cookie_header(eligible)
        raw = eligible.get_header("Cookie", "")
        values: dict[str, str] = {}
        for part in raw.split(";"):
            name, separator, value = part.strip().partition("=")
            if separator and name in {"koa:sess", "koa:sess.sig"}:
                if name in values or not value or any(x in value for x in "\r\n;"):
                    raise RefreshError("ambiguous_session_cookie")
                values[name] = value
        if set(values) != {"koa:sess", "koa:sess.sig"}:
            raise RefreshError("session_cookie_contract_changed")
        return f"koa:sess={values['koa:sess']}; koa:sess.sig={values['koa:sess.sig']};"

    def close(self):
        if self.jar is not None:
            self.jar.clear()


def _objects(payload: dict):
    data = payload.get("data")
    if isinstance(data, dict):
        yield data
        if isinstance(data.get("user"), dict):
            yield data["user"]
    if isinstance(payload.get("user"), dict):
        yield payload["user"]
    yield payload


def _identity_fields(payload: dict) -> tuple[str | None, str | None]:
    emails, user_ids = set(), set()
    for obj in _objects(payload):
        for key in ("email", "userEmail", "accountEmail"):
            value = obj.get(key)
            if isinstance(value, str) and value.strip():
                emails.add(email_address(value))
        value = obj.get("userId", obj.get("user_id"))
        if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value).strip():
            user_ids.add(str(value).strip())
    if len(emails) > 1 or len(user_ids) > 1:
        raise RefreshError("conflicting_authenticated_identity")
    return next(iter(emails), None), next(iter(user_ids), None)


def _check_login_response(response: JSONResponse) -> dict:
    data = response.data
    if response.status != 200:
        raise HTTPFailure("unexpected_http_status")
    if data.get("captcha_required"):
        raise HTTPFailure("challenge")
    code = data.get("code")
    if type(code) is not int or code != 0:
        raise HTTPFailure("login_business_rejected")
    return data


@dataclass(frozen=True)
class VerifiedLogin:
    email: str
    account_key: str
    user_id: str = field(repr=False)
    points: int
    cookie_header: str = field(repr=False)
    authorization: str | None = field(default=None, repr=False)
    origin: str = LOGIN_ORIGIN


class GladosEmailLogin:
    """One normal email login, with zero retry or challenge bypass behavior.

    Existing first-party Authorization may be supplied by an authorized native
    capture adapter, but this class never generates or changes a device fingerprint.
    Absence of this header is supported only where the server accepts it normally.
    """
    def __init__(self, transport: Transport, *, authorization: str | None = None):
        if authorization is not None and not re.fullmatch(r"fp1\.[0-9a-f]{16,64}", authorization):
            raise RefreshError("invalid_first_party_authorization")
        self.transport = transport
        self.authorization = authorization
        self._attempt: LoginAttempt | None = None
        self._send_started = False
        self._submit_started = False
        self._terminal = False

    def _headers(self):
        headers = {"Origin": LOGIN_ORIGIN, "Referer": LOGIN_ORIGIN + "/login",
                   "User-Agent": "GLaDOSAccountCenter/2.0.9-login-refresh-validation"}
        if self.authorization:
            headers["Authorization"] = self.authorization
        return headers

    def request_code(self, attempt: LoginAttempt):
        attempt.validate()
        if self._send_started or self._terminal:
            raise RefreshError("code_request_already_started")
        # Set before the network write: timeout cannot be retried in this attempt.
        self._send_started = True
        self._attempt = attempt
        try:
            response = self.transport.request("POST", LOGIN_ORIGIN + "/api/authorization",
                                              headers=self._headers(), payload={
                "address": email_address(attempt.target_email), "site": LOGIN_SITE})
            _check_login_response(response)
        except RefreshError:
            self._terminal = True
            raise

    def submit(self, candidate: CodeCandidate, *, known_account_keys: Iterable[str],
               now: datetime) -> VerifiedLogin:
        if not self._send_started or self._attempt is None or self._terminal:
            raise RefreshError("no_accepted_code_request")
        if self._submit_started:
            raise RefreshError("code_already_submitted")
        target = email_address(self._attempt.target_email)
        if candidate.attempt_id != self._attempt.attempt_id or candidate.target_email != target:
            raise RefreshError("candidate_attempt_mismatch")
        if not isinstance(candidate.code, str) or not re.fullmatch(r"[0-9]{6}", candidate.code):
            raise RefreshError("invalid_code")
        if aware(candidate.issued_at) < aware(self._attempt.requested_at).replace(microsecond=0):
            raise RefreshError("candidate_attempt_mismatch")
        if (aware(now) - aware(candidate.issued_at)).total_seconds() >= self._attempt.ttl_seconds - self._attempt.safety_margin_seconds:
            raise RefreshError("code_expired")
        if aware(candidate.issued_at) > aware(now):
            raise RefreshError("future_code")
        known = frozenset(known_account_keys)
        if not known or any(not isinstance(x, str) or not re.fullmatch(r"[A-F0-9]{16}", x) for x in known):
            raise RefreshError("known_account_registry_required")
        self._submit_started = True
        payload = _check_login_response(self.transport.request(
            "POST", LOGIN_ORIGIN + "/api/login", headers=self._headers(), payload={
                "method": "email", "site": LOGIN_SITE, "email": target, "mailcode": candidate.code}))
        _, login_id = _identity_fields(payload)
        session = _check_login_response(self.transport.request(
            "GET", LOGIN_ORIGIN + "/api/user/session", headers=self._headers()))
        session_email, session_id = _identity_fields(session)
        if not session_email:
            status = _check_login_response(self.transport.request(
                "GET", LOGIN_ORIGIN + "/api/user/status", headers=self._headers()))
            status_email, status_id = _identity_fields(status)
            session_email = status_email
            if session_id and status_id and session_id != status_id:
                raise RefreshError("identity_mismatch")
            session_id = session_id or status_id
        if session_email != target or not session_id or (login_id and login_id != session_id):
            raise RefreshError("identity_mismatch")
        # Matches the installed core.js: hash stable authenticated userId, NOT cookie.
        key = hashlib.sha256(f"glados-user:{session_id}".encode()).hexdigest()[:16].upper()
        if key not in known:
            raise RefreshError("unregistered_authenticated_account")
        points_payload = _check_login_response(self.transport.request(
            "GET", LOGIN_ORIGIN + "/api/user/points", headers=self._headers()))
        point_values = set()
        for obj in _objects(points_payload):
            for field_name in ("points", "point", "pointsTotal", "points_total"):
                raw = obj.get(field_name)
                if raw is None or isinstance(raw, bool):
                    continue
                try:
                    number = float(raw)
                    if not math.isfinite(number) or number < 0 or number != int(number):
                        raise ValueError
                    point_values.add(int(number))
                except (TypeError, ValueError, OverflowError):
                    raise RefreshError("invalid_points") from None
        if not point_values:
            raise RefreshError("missing_points")
        if len(point_values) != 1:
            raise RefreshError("conflicting_points")
        points = point_values.pop()
        cookie_reader = getattr(self.transport, "credential_header", None)
        if not callable(cookie_reader):
            raise RefreshError("cookie_jar_required")
        credential = cookie_reader()
        return VerifiedLogin(target, key, session_id, points, credential, self.authorization)


def trusted_outer_sender(raw: bytes) -> str | None:
    """Interpret the first Gmail Authentication-Results on a Gmail RAW response.

    This function is NOT a general signature verifier. Call only on bytes returned
    directly by the authenticated Gmail API, never a file or forwarded attachment.
    Gmail's trusted topmost results are not interchangeable with results in the body.
    Require DMARC pass AND aligned SPF or DKIM pass; unsupported cases fail closed.
    """
    try:
        mail = BytesParser(policy=policy.default).parsebytes(raw, headersonly=True)
        from login_refresh_core import addresses
        if len(mail.get_all("From", [])) != 1:
            return None
        outer = addresses(str(mail.get("From", "")))
        if len(outer) != 1:
            return None
        sender = next(iter(outer))
        domain = sender.rsplit("@", 1)[1]
        results = mail.get_all("Authentication-Results", [])
        gmail = next((str(v) for v in results if re.match(r"\s*mx\.google\.com\s*;", str(v), re.I)), None)
        if gmail is None:
            return None
        fields = [x.strip() for x in re.sub(r"\r?\n\s+", " ", gmail).split(";")[1:]]
        dmarc = any(re.match(r"dmarc=pass(?:\s|$)", x, re.I) and
                    re.search(r"\bheader\.from=" + re.escape(domain) + r"(?:\s|$)", x, re.I)
                    for x in fields)
        aligned = any(
            (re.match(r"dkim=pass(?:\s|$)", x, re.I) and
             re.search(r"\bheader\.(?:i=[^\s@]*@|d=)" + re.escape(domain) + r"(?:\s|$)", x, re.I)) or
            (re.match(r"spf=pass(?:\s|$)", x, re.I) and
             re.search(r"\bsmtp\.mailfrom=(?:[^\s@]*@)?" + re.escape(domain) + r"(?:\s|$)", x, re.I))
            for x in fields)
        return sender if dmarc and aligned else None
    except (ValueError, TypeError, UnicodeError):
        return None


class GmailReadOnly:
    """Direct official Gmail API access; no send/modify/delete methods are exposed.

    token_provider must supply the APP'S own authorized access token from protected
    storage. It is not a ChatGPT connector credential. No OAuth token is persisted here.
    """
    def __init__(self, transport: Transport, token_provider: Callable[[], str], mailbox: str):
        self.transport = transport
        self.token_provider = token_provider
        self.mailbox = email_address(mailbox)

    def _get(self, path: str, params=None):
        token = self.token_provider()
        if not isinstance(token, str) or not token or any(c in token for c in "\r\n"):
            raise RefreshError("mail_auth_required")
        headers = {"Authorization": "Bearer " + token}
        if path != "profile":
            profile = self.transport.request("GET", GMAIL_ORIGIN + "/gmail/v1/users/me/profile", headers=headers)
            if profile.status != 200 or email_address(profile.data.get("emailAddress", "")) != self.mailbox:
                raise RefreshError("wrong_mailbox")
        url = GMAIL_ORIGIN + "/gmail/v1/users/me/" + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        response = self.transport.request("GET", url, headers=headers)
        if response.status != 200:
            raise HTTPFailure("mail_api_unavailable")
        return response.data

    def verify_mailbox(self):
        data = self._get("profile")
        if email_address(data.get("emailAddress", "")) != self.mailbox:
            raise RefreshError("wrong_mailbox")

    def message_ids(self, *, after: datetime, limit: int = 100) -> tuple[str, ...]:
        if not 1 <= limit <= 300:
            raise RefreshError("invalid_mail_limit")
        # A server-side redirect can preserve the ORIGINAL To header. The profile
        # check pins the actual receiving mailbox; do not filter its address as To.
        query = f'subject:"GLaDOS Authentication Code" after:{int(aware(after).timestamp())}'
        ids: list[str] = []
        page = None
        seen_pages: set[str] = set()
        while True:
            params = {"q": query, "maxResults": min(100, limit - len(ids))}
            if page:
                params["pageToken"] = page
            data = self._get("messages", params)
            rows = data.get("messages", [])
            if not isinstance(rows, list):
                raise RefreshError("invalid_mail_list")
            for item in rows:
                mid = item.get("id", "") if isinstance(item, dict) else ""
                if not re.fullmatch(r"[0-9a-fA-F]{8,64}", mid):
                    raise RefreshError("invalid_gmail_id")
                if mid not in ids:
                    ids.append(mid)
            page = data.get("nextPageToken")
            if len(ids) > limit or (page and len(ids) >= limit):
                raise RefreshError("mail_window_too_large")
            if not page:
                return tuple(ids)
            if not isinstance(page, str) or page in seen_pages:
                raise RefreshError("invalid_mail_pagination")
            seen_pages.add(page)
            if len(seen_pages) > 5:
                raise RefreshError("mail_window_too_large")

    def read_message(self, message_id: str) -> MailEnvelope:
        if not re.fullmatch(r"[0-9a-fA-F]{8,64}", message_id):
            raise RefreshError("invalid_gmail_id")
        data = self._get("messages/" + message_id, {"format": "raw"})
        if data.get("id") != message_id:
            raise RefreshError("mail_id_mismatch")
        encoded = data.get("raw", "")
        if not isinstance(encoded, str) or not encoded or len(encoded) > (MAX_RAW_MAIL * 4 // 3 + 8):
            raise RefreshError("invalid_mail_size")
        try:
            raw = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
            if not raw or len(raw) > MAX_RAW_MAIL:
                raise ValueError
            stamp = int(data["internalDate"])
            if stamp <= 0:
                raise ValueError
            received = datetime.fromtimestamp(stamp / 1000, UTC)
        except (ValueError, KeyError, TypeError, OverflowError, OSError):
            raise RefreshError("invalid_gmail_message") from None
        return MailEnvelope(message_id, self.mailbox, received, trusted_outer_sender(raw), raw)
