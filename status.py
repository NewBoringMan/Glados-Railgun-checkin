from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Optional, Tuple
from zoneinfo import ZoneInfo

from checkin import AuthenticationError, ChallengeError, GladosAPI, NetworkError, ProtocolError


TAIPEI = ZoneInfo("Asia/Taipei")
HISTORY_WINDOW_DAYS = 35
VIP_PLAN_NAMES = {
    0: "Free",
    6: "Expired",
    7: "Reset",
    8: "Overlimit",
    9: "Spam",
    10: "Free",
    11: "Edu",
    21: "Basic",
    31: "Pro",
    41: "Team",
    51: "Enterprise",
}

PLAN_TEXT_KEYS = ("plan", "planName", "membership", "vipType")
VIP_LEVEL_KEYS = ("vip", "vipLevel", "vip_level")
STREAK_KEYS = ("streak", "checkinStreak", "checkin_streak", "signStreak", "sign_streak")
HISTORY_TIME_KEYS = ("time", "timestamp", "createdAt", "created_at", "date")
HISTORY_CHANGE_KEYS = ("change", "delta", "pointsChange", "points_change", "amount")
HISTORY_TEXT_KEYS = ("type", "reason", "description", "source", "message", "remark", "title", "action", "name")
CHECKIN_MARKERS = ("checkin", "check-in", "checked in", "签到", "打卡", "observation", "logging")
NON_CHECKIN_MARKERS = ("exchange", "redeem", "兑换", "invite", "referral", "邀请")


def _text_field(data: Dict[str, Any], keys: Iterable[str]) -> str:
    for source in (data, data.get("user")):
        if not isinstance(source, dict):
            continue
        for key in keys:
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _vip_level(data: Dict[str, Any]) -> Optional[int]:
    for source in (data, data.get("user")):
        if not isinstance(source, dict):
            continue
        for key in VIP_LEVEL_KEYS:
            value = source.get(key)
            if value is None or isinstance(value, bool):
                continue
            try:
                return int(float(value))
            except (TypeError, ValueError):
                continue
    return None


def plan_from_status_data(data: Dict[str, Any]) -> Tuple[str, Optional[int]]:
    """Return a stable display name for the current membership plan."""
    text = _text_field(data, PLAN_TEXT_KEYS)
    level = _vip_level(data)
    if text:
        return text, level
    if level is None:
        return "", None
    return VIP_PLAN_NAMES.get(level, f"VIP {level}"), level


def _status_streak(data: Dict[str, Any]) -> Optional[int]:
    for source in (data, data.get("user")):
        if not isinstance(source, dict):
            continue
        for key in STREAK_KEYS:
            value = source.get(key)
            if value is None or isinstance(value, bool):
                continue
            try:
                result = int(float(value))
            except (TypeError, ValueError):
                continue
            if result >= 0:
                return result
    return None


def _read_status_payload(api: GladosAPI) -> Dict[str, Any]:
    payload = api._request_json("GET", "/api/user/status")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ProtocolError("状态接口缺少 data")
    if data.get("leftDays") is None:
        raise ProtocolError("状态接口缺少 leftDays")
    return data


def _read_points_payload(api: GladosAPI) -> Dict[str, Any]:
    payload = api._request_json("GET", "/api/user/points")
    if payload.get("points") is None:
        raise ProtocolError("积分接口缺少 points")
    return payload


def _to_int(value: Any, field: str) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError) as exc:
        raise ProtocolError(f"{field} 不是有效数字") from exc


def _history_datetime(item: Dict[str, Any]) -> Optional[datetime]:
    for key in HISTORY_TIME_KEYS:
        value = item.get(key)
        if value is None or isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            raw = float(value)
            if raw > 10_000_000_000:
                while raw > 10_000_000_000:
                    raw /= 1000.0
            try:
                return datetime.fromtimestamp(raw, tz=timezone.utc).astimezone(TAIPEI)
            except (OSError, OverflowError, ValueError):
                continue
        if isinstance(value, str) and value.strip():
            text = value.strip()
            try:
                if text.isdigit():
                    raw = float(text)
                    while raw > 10_000_000_000:
                        raw /= 1000.0
                    return datetime.fromtimestamp(raw, tz=timezone.utc).astimezone(TAIPEI)
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=TAIPEI)
                return parsed.astimezone(TAIPEI)
            except (OSError, OverflowError, ValueError):
                continue
    return None


def _history_change(item: Dict[str, Any]) -> Optional[int]:
    for key in HISTORY_CHANGE_KEYS:
        value = item.get(key)
        if value is None or isinstance(value, bool):
            continue
        try:
            return int(float(value))
        except (TypeError, ValueError):
            continue
    return None


def _history_text(item: Dict[str, Any]) -> str:
    values = []
    for key in HISTORY_TEXT_KEYS:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            values.append(value.strip().lower())
    return " ".join(values)


def _looks_like_checkin(item: Dict[str, Any]) -> bool:
    text = _history_text(item)
    if any(marker in text for marker in NON_CHECKIN_MARKERS):
        return False
    if any(marker in text for marker in CHECKIN_MARKERS):
        return True
    # Current points-history rows can expose only time + change. Positive point
    # mutations are a conservative fallback; redemptions are negative.
    change = _history_change(item)
    return change is not None and change > 0


def build_checkin_history(
    points_payload: Dict[str, Any],
    *,
    today: Optional[date] = None,
    window_days: int = HISTORY_WINDOW_DAYS,
) -> Tuple[list[Dict[str, Any]], int, str]:
    """Build a daily punch calendar from GLaDOS points history.

    States are checked/missed/pending/unknown. Dates older than the earliest
    available history row remain unknown instead of being misreported as missed.
    """
    if today is None:
        today = datetime.now(TAIPEI).date()
    window_days = max(1, min(int(window_days), 90))
    history_raw = points_payload.get("history")
    history = history_raw if isinstance(history_raw, list) else []

    all_dates: list[date] = []
    checked: Dict[date, int] = {}
    for raw in history:
        if not isinstance(raw, dict):
            continue
        when = _history_datetime(raw)
        if when is None:
            continue
        day = when.date()
        all_dates.append(day)
        if _looks_like_checkin(raw):
            delta = _history_change(raw) or 0
            checked[day] = max(checked.get(day, 0), delta)

    coverage_start = min(all_dates) if all_dates else None
    start = today - timedelta(days=window_days - 1)
    rows: list[Dict[str, Any]] = []
    for offset in range(window_days):
        day = start + timedelta(days=offset)
        if day in checked:
            state = "checked"
            delta: Optional[int] = checked[day]
        elif day == today:
            state = "pending" if coverage_start is not None else "unknown"
            delta = None
        elif coverage_start is None or day < coverage_start:
            state = "unknown"
            delta = None
        else:
            state = "missed"
            delta = None
        rows.append({"date": day.isoformat(), "state": state, "points_delta": delta})

    cursor = today if today in checked else today - timedelta(days=1)
    streak = 0
    while cursor in checked:
        streak += 1
        cursor -= timedelta(days=1)

    source = "points_history" if all_dates else "unavailable"
    return rows, streak, source


def read_status(cookie: str, account_key: str, domains: Iterable[str]):
    last_error = ""
    for domain in domains:
        api = GladosAPI(domain, cookie)
        try:
            data = _read_status_payload(api)
            points_payload = _read_points_payload(api)
            points = _to_int(points_payload.get("points"), "points")
            plan_name, vip_level = plan_from_status_data(data)
            email = _text_field(data, ("email", "userEmail"))
            checkin_history, inferred_streak, history_source = build_checkin_history(points_payload)
            streak = _status_streak(data)
            if streak is None:
                streak = inferred_streak
            return {
                "account_key": account_key,
                "domain": domain,
                "ok": True,
                "days_left": _to_int(data.get("leftDays"), "leftDays"),
                "points_total": points,
                "email": email,
                "plan_name": plan_name,
                "vip_level": vip_level,
                "streak": streak,
                "checkin_history": checkin_history,
                "history_source": history_source,
                "error": "",
            }
        except (NetworkError, ProtocolError) as exc:
            last_error = str(exc)
            continue
        except (AuthenticationError, ChallengeError) as exc:
            return _error_result(account_key, domain, str(exc))
        finally:
            api.close()
    return _error_result(account_key, "", last_error or "所有 GLaDOS 域名均不可用")


def _error_result(account_key: str, domain: str, error: str) -> Dict[str, Any]:
    return {
        "account_key": account_key,
        "domain": domain,
        "ok": False,
        "days_left": None,
        "points_total": None,
        "email": "",
        "plan_name": "",
        "vip_level": None,
        "streak": None,
        "checkin_history": [],
        "history_source": "unavailable",
        "error": error,
    }


def main() -> int:
    cookie = os.environ.get("GLADOS_COOKIES", "").strip()
    account_key = os.environ.get("GLADOS_ACCOUNT_KEY", "").strip()
    domains = tuple(
        value.strip().lower()
        for value in os.environ.get("GLADOS_DOMAINS", "glados.cloud,railgun.info").split(",")
        if value.strip()
    )
    result = _error_result(account_key, "", "GLADOS_COOKIES 为空") if not cookie else read_status(cookie, account_key, domains)
    print("GLADOS_STATUS_JSON=" + json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
