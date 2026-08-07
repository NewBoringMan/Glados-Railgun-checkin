#!/usr/bin/env python3
"""Privacy-minimized GLaDOS V3 runtime.

One invocation handles exactly one account secret. It supports:
- read_only: GET-only account/capability canary
- primary: one check-in POST, optional verified optimal exchange
- recovery: same as primary, but first avoids a duplicate POST when reliable history confirms today

Actions logs deliberately contain only coarse operational state. They never print Cookie,
email, exact points balance, points history, raw API payloads, or persistent account keys.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterable, Optional
from zoneinfo import ZoneInfo

import requests

TAIPEI = ZoneInfo("Asia/Taipei")
DEFAULT_DOMAINS = ("glados.cloud", "railgun.info")
RETRYABLE = {429, 500, 502, 503, 504}
CHALLENGE_HINTS = ("captcha", "challenge", "access denied", "cloudflare", "人机验证", "验证码")
CHECKIN_MARKERS = ("checkin", "check-in", "checked in", "签到", "打卡", "observation", "logging")
NON_CHECKIN_MARKERS = ("exchange", "redeem", "兑换", "invite", "referral", "邀请")


class V3Error(RuntimeError): pass
class AuthError(V3Error): pass
class ChallengeError(V3Error): pass
class NetworkError(V3Error): pass
class ProtocolError(V3Error): pass


@dataclass(frozen=True)
class Plan:
    plan_id: str
    points: int
    days: int
    verified: bool = True

    @property
    def cost(self) -> Fraction:
        return Fraction(self.points, self.days)


class Client:
    def __init__(self, domain: str, cookie: str, session: Optional[requests.Session] = None):
        self.domain = domain
        self.cookie = cookie
        self.session = session or requests.Session()
        self.base = f"https://{domain}"
        self.headers = {
            "accept": "application/json, text/plain, */*",
            "content-type": "application/json;charset=UTF-8",
            "cookie": cookie,
            "origin": self.base,
            "referer": f"{self.base}/console/checkin",
            "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/150 Safari/537.36",
        }

    def close(self): self.session.close()

    def request(self, method: str, path: str, body: Optional[dict] = None, *, retry_get: bool = True) -> dict:
        method = method.upper()
        attempts = 3 if method == "GET" and retry_get else 1
        for attempt in range(attempts):
            try:
                response = self.session.request(method, self.base + path, headers=self.headers,
                                                json=body if method == "POST" else None, timeout=(5, 15))
            except (requests.Timeout, requests.ConnectionError) as exc:
                if attempt + 1 >= attempts: raise NetworkError("network") from exc
                time.sleep(min(1.0 * (2 ** attempt), 4.0)); continue
            except requests.RequestException as exc:
                raise NetworkError("request") from exc
            text = response.text or ""; lower = text.lower()
            if response.status_code in {401, 403}:
                if any(x in lower for x in CHALLENGE_HINTS): raise ChallengeError("challenge")
                raise AuthError("auth")
            if response.status_code in RETRYABLE:
                if attempt + 1 >= attempts: raise NetworkError(f"http_{response.status_code}")
                delay = 1.0 * (2 ** attempt)
                if response.status_code == 429:
                    try: delay = min(float(response.headers.get("Retry-After", "1")), 30.0)
                    except ValueError: pass
                time.sleep(max(0.0, delay)); continue
            if response.status_code >= 400: raise ProtocolError(f"http_{response.status_code}")
            if any(x in lower for x in CHALLENGE_HINTS) and not text.lstrip().startswith("{"):
                raise ChallengeError("challenge")
            try: data = response.json()
            except ValueError as exc: raise ProtocolError("non_json") from exc
            if not isinstance(data, dict): raise ProtocolError("non_object")
            return data
        raise NetworkError("exhausted")

    def status(self) -> dict:
        payload = self.request("GET", "/api/user/status"); data = payload.get("data")
        if not isinstance(data, dict) or data.get("leftDays") is None: raise ProtocolError("status_shape")
        return data

    def points(self) -> dict:
        payload = self.request("GET", "/api/user/points")
        if payload.get("points") is None: raise ProtocolError("points_shape")
        return payload

    def checkin(self) -> str:
        payload = self.request("POST", "/api/user/checkin", {"token": self.domain}, retry_get=False)
        code = payload.get("code")
        if code == 0: return "success"
        if code == 1: return "already"
        raise ProtocolError("checkin_rejected")

    def exchange(self, plan_id: str) -> None:
        payload = self.request("POST", "/api/user/exchange", {"planType": plan_id}, retry_get=False)
        if payload.get("code") != 0: raise ProtocolError("exchange_rejected")


def _int(value: Any) -> Optional[int]:
    if isinstance(value, bool) or value is None: return None
    try: return int(float(value))
    except (TypeError, ValueError): return None


def load_catalog(path: Path) -> list[Plan]:
    raw = json.loads(path.read_text(encoding="utf-8")); rows = raw.get("plans") if isinstance(raw, dict) else None
    if not isinstance(rows, list): raise ProtocolError("catalog_shape")
    plans: list[Plan] = []
    for row in rows:
        if not isinstance(row, dict): continue
        pid = str(row.get("id", "")).strip(); pts, days = _int(row.get("points")), _int(row.get("days"))
        if re.fullmatch(r"plan[A-Za-z0-9_-]+", pid) and pts and days and bool(row.get("verified", False)):
            plans.append(Plan(pid, pts, days, True))
    if not plans: raise ProtocolError("catalog_empty")
    return plans


def parse_live_plans(payload: dict) -> Optional[list[Plan]]:
    raw = payload.get("plans")
    if raw is None: return None
    if isinstance(raw, dict):
        items: Iterable[Any] = [dict(v, __key=k) if isinstance(v, dict) else v for k, v in raw.items()]
    elif isinstance(raw, list): items = raw
    else: return None
    out: list[Plan] = []
    for item in items:
        if not isinstance(item, dict): continue
        pid = str(item.get("id") or item.get("planType") or item.get("plan_type") or item.get("__key") or "").strip()
        pts, days = _int(item.get("points")), _int(item.get("days"))
        if re.fullmatch(r"plan[A-Za-z0-9_-]+", pid) and pts and days and pts > 0 and days > 0:
            out.append(Plan(pid, pts, days, False))
    return out or None


def trusted_live_intersection(trusted: list[Plan], live: Optional[list[Plan]]) -> list[Plan]:
    if not live: return []
    live_keys = {(p.plan_id, p.points, p.days) for p in live}
    return [p for p in trusted if (p.plan_id, p.points, p.days) in live_keys]


def best_plan(plans: Iterable[Plan]) -> Plan:
    candidates = list(plans)
    if not candidates: raise ProtocolError("no_verified_live_plan")
    return min(candidates, key=lambda p: (p.cost, p.days, p.points, p.plan_id))


def _history_datetime(item: dict) -> Optional[datetime]:
    for key in ("time", "timestamp", "createdAt", "created_at", "date"):
        value = item.get(key)
        if value is None: continue
        if isinstance(value, (int, float)):
            raw = float(value)
            while raw > 10_000_000_000: raw /= 1000.0
            try: return datetime.fromtimestamp(raw, timezone.utc).astimezone(TAIPEI)
            except (ValueError, OSError, OverflowError): continue
        if isinstance(value, str) and value.strip():
            text = value.strip()
            try:
                if text.isdigit():
                    raw = float(text)
                    while raw > 10_000_000_000: raw /= 1000.0
                    return datetime.fromtimestamp(raw, timezone.utc).astimezone(TAIPEI)
                dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
                if dt.tzinfo is None: dt = dt.replace(tzinfo=TAIPEI)
                return dt.astimezone(TAIPEI)
            except (ValueError, OSError, OverflowError): continue
    return None


def reliable_today_checkin(points_payload: dict) -> bool:
    today = datetime.now(TAIPEI).date(); history = points_payload.get("history")
    if not isinstance(history, list): return False
    for item in history:
        if not isinstance(item, dict): continue
        when = _history_datetime(item)
        if when is None or when.date() != today: continue
        text = " ".join(str(item.get(k, "")).strip().lower() for k in
                        ("type", "reason", "description", "source", "message", "remark", "title", "action", "name"))
        if any(x in text for x in NON_CHECKIN_MARKERS): continue
        if any(x in text for x in CHECKIN_MARKERS): return True
    return False


def _client_for(cookie: str, domains: Iterable[str]) -> tuple[Client, dict, dict]:
    last: Optional[V3Error] = None
    for domain in domains:
        client = Client(domain, cookie)
        try: return client, client.status(), client.points()
        except (NetworkError, ProtocolError) as exc: last = exc; client.close(); continue
        except (AuthError, ChallengeError): client.close(); raise
    raise last or NetworkError("all_domains")


def _push(sendkey: str, title: str, body: str) -> None:
    if not sendkey: return
    try:
        requests.post("https://api2.pushdeer.com/message/push",
                      data={"pushkey": sendkey, "text": title, "desp": body, "type": "text"}, timeout=10)
    except requests.RequestException: pass


def execute(env: Optional[dict[str, str]] = None) -> dict:
    env = os.environ if env is None else env
    cookie = env.get("GLADOS_COOKIES", "").strip(); mode = env.get("GLADOS_RUN_MODE", "primary").strip().lower()
    auto_exchange = env.get("GLADOS_AUTO_EXCHANGE", "false").strip().lower() in {"1", "true", "yes", "on"}
    domains = tuple(x.strip().lower() for x in env.get("GLADOS_DOMAINS", ",".join(DEFAULT_DOMAINS)).split(",") if x.strip())
    catalog_path = Path(env.get("GLADOS_EXCHANGE_CATALOG", ".github/glados/exchange_plans.json"))
    if mode not in {"primary", "recovery", "read_only"}: mode = "primary"
    if not cookie: raise ProtocolError("configuration")

    client, before_status, before_points_payload = _client_for(cookie, domains)
    try:
        live = parse_live_plans(before_points_payload); trusted = load_catalog(catalog_path)
        compatible = trusted_live_intersection(trusted, live)
        capability = "verified" if compatible else ("unavailable" if live is None else "changed")
        if mode == "read_only":
            return {"ok": True, "mode": mode, "status": "readable", "exchange_capability": capability}
        if mode == "recovery" and reliable_today_checkin(before_points_payload):
            return {"ok": True, "mode": mode, "checkin": "already-confirmed", "exchange": "not-needed", "exchange_capability": capability}
        checkin = client.checkin(); points_payload = client.points(); exchange = "disabled"
        if auto_exchange:
            live_after = parse_live_plans(points_payload); compatible_after = trusted_live_intersection(trusted, live_after)
            if not compatible_after: exchange = "held-unverified"
            else:
                plan = best_plan(compatible_after); points_before = _int(points_payload.get("points")); days_before = _int(before_status.get("leftDays"))
                if points_before is None: exchange = "held-unknown-points"
                elif points_before < plan.points: exchange = "waiting"
                else:
                    client.exchange(plan.plan_id); after_points = client.points(); after_status = client.status()
                    points_after = _int(after_points.get("points")); days_after = _int(after_status.get("leftDays"))
                    points_ok = points_after is not None and points_after <= points_before - plan.points
                    days_ok = days_before is not None and days_after is not None and days_after >= days_before + plan.days - 1
                    exchange = "success-verified" if points_ok and days_ok else "verification-failed"
        ok = checkin in {"success", "already"} and exchange != "verification-failed"
        return {"ok": ok, "mode": mode, "checkin": checkin, "exchange": exchange, "exchange_capability": capability}
    finally: client.close()


def main() -> int:
    sendkey = os.environ.get("PUSHDEER_SENDKEY", "").strip(); slot = os.environ.get("GLADOS_SLOT", "?").strip() or "?"
    try:
        result = execute(); print("GLaDOS V3: " + json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        if result.get("exchange") == "success-verified": _push(sendkey, "GLaDOS 自动兑换成功", f"账号槽位 {slot} 已完成兑换并通过积分/天数双重校验。")
        elif not result.get("ok"): _push(sendkey, "GLaDOS 自动任务异常", f"账号槽位 {slot} 需要检查。")
        return 0 if result.get("ok") else 1
    except (AuthError, ChallengeError, NetworkError, ProtocolError) as exc:
        error_type = type(exc).__name__; print("GLaDOS V3: " + json.dumps({"ok": False, "error_type": error_type}, separators=(",", ":")))
        _push(sendkey, "GLaDOS 自动任务需要处理", f"账号槽位 {slot} · {error_type}")
        return 1

if __name__ == "__main__": raise SystemExit(main())
