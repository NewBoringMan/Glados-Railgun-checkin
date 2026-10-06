"""Read manually captured login context without renewing or persisting sessions.

The App stores this JSON in the existing per-account GitHub Secret. Legacy raw
Cookie headers remain readable, but cannot recover a browser UA or original host.
No value in this module is a server-side session lifetime or an expiry guess.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable


SCHEMA = "glados.manual-session"
SUPPORTED_HOSTS = frozenset({
    "glados.cloud", "glados.network", "glados.rocks", "glados.one",
    "glados.space", "glados.vip", "glados-facility.com", "railgun.info",
})
LEGACY_WARNING = "旧登录信息未记录原浏览器和登录域名；可在 App 中手动重新读取并保存。"
_KEY = re.compile(r"[A-F0-9]{16}\Z")
_EMAIL = re.compile(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+\Z")
_COOKIE_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z:-]+\Z")


class SessionContextError(ValueError):
    """Invalid local capture data. Messages must never contain credential values."""


@dataclass(frozen=True)
class SessionContext:
    cookie_header: str = field(repr=False)
    user_agent: str = ""
    host: str = ""
    account_key: str = ""
    email: str = field(default="", repr=False)
    captured_at: str = ""
    browser: str = ""
    structured: bool = False

    def domains(self, legacy_domains: Iterable[str]) -> tuple[str, ...]:
        # A browser's cookies belong to the origin actually used to capture them.
        return (self.host,) if self.structured else tuple(legacy_domains)

    @property
    def warning(self) -> str:
        return "" if self.structured else LEGACY_WARNING


def _text(value: Any, name: str, limit: int, *, required: bool = True) -> str:
    if not isinstance(value, str) or len(value) > limit:
        raise SessionContextError(f"手动登录信息的 {name} 格式不正确")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise SessionContextError(f"手动登录信息的 {name} 包含非法控制字符")
    value = value.strip()
    if required and not value:
        raise SessionContextError(f"手动登录信息缺少 {name}")
    return value


def validate_cookie_header(value: Any) -> str:
    header = _text(value, "cookieHeader", 32768)
    names: dict[str, str] = {}
    for part in header.split(";"):
        part = part.strip()
        if not part:
            continue
        name, separator, cookie_value = part.partition("=")
        if not separator or not _COOKIE_NAME.fullmatch(name) or not cookie_value:
            raise SessionContextError("手动登录信息的 Cookie 格式不完整")
        if name in names:
            raise SessionContextError("手动登录信息包含重复 Cookie 名称，请重新手动读取")
        names[name] = cookie_value
    # gld values are opaque: do not decode them or use a legacy koa _expire as TTL.
    for base in ("gld:sess", "koa:sess"):
        if (base in names) != (f"{base}.sig" in names):
            raise SessionContextError(f"手动登录信息缺少完整的 {base} 签名对")
    if not any(base in names for base in ("gld:sess", "koa:sess")):
        raise SessionContextError("手动登录信息未包含 GLaDOS 会话 Cookie")
    return header


def parse_session(raw: str | SessionContext, expected_account_key: str = "") -> SessionContext:
    if isinstance(raw, SessionContext):
        context = raw
    else:
        if not isinstance(raw, str) or len(raw) > 65536:
            raise SessionContextError("登录信息为空或超过允许大小")
        raw = raw.strip()
        if not raw:
            raise SessionContextError("GLADOS_COOKIES 为空")
        if raw.startswith(("{", "[")):
            try:
                payload = json.loads(raw)
            except (ValueError, RecursionError) as exc:
                raise SessionContextError("手动登录信息 JSON 无法解析，请重新手动保存") from exc
            if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
                raise SessionContextError("不支持的登录信息格式")
            if type(payload.get("version")) is not int or payload["version"] != 1:
                raise SessionContextError("不支持的手动登录信息版本")
            key = _text(payload.get("accountKey"), "accountKey", 16)
            if not _KEY.fullmatch(key):
                raise SessionContextError("手动登录信息的账号 ID 格式不正确")
            email = _text(payload.get("email"), "email", 320)
            if not _EMAIL.fullmatch(email):
                raise SessionContextError("手动登录信息缺少有效邮箱")
            host = _text(payload.get("host"), "host", 253).lower()
            if host not in SUPPORTED_HOSTS:
                raise SessionContextError("手动登录信息的原登录域名不受支持")
            captured_at = _text(payload.get("capturedAt"), "capturedAt", 64)
            try:
                instant = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
                if instant.tzinfo is None:
                    raise ValueError("missing timezone")
            except ValueError as exc:
                raise SessionContextError("手动登录信息的保存时间不正确") from exc
            context = SessionContext(
                cookie_header=validate_cookie_header(payload.get("cookieHeader")),
                user_agent=_text(payload.get("userAgent"), "userAgent", 2048),
                host=host, account_key=key, email=email,
                captured_at=captured_at,
                browser=_text(payload.get("browser"), "browser", 100),
                structured=True,
            )
        else:
            # Keep legacy headers intact, except for rejecting header injection.
            context = SessionContext(cookie_header=_text(raw, "Cookie", 32768))
    if context.structured and expected_account_key and context.account_key != expected_account_key.strip().upper():
        raise SessionContextError("手动登录信息与当前账号不匹配，已停止请求")
    return context


def split_sessions(raw: str) -> list[str]:
    """Parse JSON as one object before considering the old '&' account separator."""
    raw = raw.strip()
    if not raw:
        return []
    if raw.startswith(("{", "[")):
        parse_session(raw)
        return [raw]
    return [item.strip() for item in raw.split("&") if item.strip()]


def account_key_from_user_id(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise SessionContextError("服务端账号 ID 格式不正确")
    user_id = str(value).strip()
    if not user_id or len(user_id) > 128:
        raise SessionContextError("服务端账号 ID 格式不正确")
    return hashlib.sha256(f"glados-user:{user_id}".encode()).hexdigest()[:16].upper()
