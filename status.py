from __future__ import annotations

import json
import os
from typing import Any, Dict, Iterable, Optional, Tuple

from checkin import AuthenticationError, ChallengeError, GladosAPI, NetworkError, ProtocolError


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
    """Return a stable display name for the current membership plan.

    Newer GLaDOS status payloads expose the membership as numeric ``vip``.
    Textual plan fields are still accepted for compatibility. Unknown numeric
    levels stay visible as ``VIP <level>`` instead of disappearing from the UI.
    """
    text = _text_field(data, PLAN_TEXT_KEYS)
    level = _vip_level(data)
    if text:
        return text, level
    if level is None:
        return "", None
    return VIP_PLAN_NAMES.get(level, f"VIP {level}"), level


def _read_status_payload(api: GladosAPI) -> Dict[str, Any]:
    payload = api._request_json("GET", "/api/user/status")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ProtocolError("状态接口缺少 data")
    if data.get("leftDays") is None:
        raise ProtocolError("状态接口缺少 leftDays")
    return data


def _to_int(value: Any, field: str) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError) as exc:
        raise ProtocolError(f"{field} 不是有效数字") from exc


def read_status(cookie: str, account_key: str, domains: Iterable[str]):
    last_error = ""
    for domain in domains:
        api = GladosAPI(domain, cookie)
        try:
            data = _read_status_payload(api)
            points = api.points()
            plan_name, vip_level = plan_from_status_data(data)
            email = _text_field(data, ("email", "userEmail"))
            return {
                "account_key": account_key,
                "domain": domain,
                "ok": True,
                "days_left": _to_int(data.get("leftDays"), "leftDays"),
                "points_total": points,
                "email": email,
                "plan_name": plan_name,
                "vip_level": vip_level,
                "error": "",
            }
        except (NetworkError, ProtocolError) as exc:
            last_error = str(exc)
            continue
        except (AuthenticationError, ChallengeError) as exc:
            return {
                "account_key": account_key,
                "domain": domain,
                "ok": False,
                "days_left": None,
                "points_total": None,
                "email": "",
                "plan_name": "",
                "vip_level": None,
                "error": str(exc),
            }
        finally:
            api.close()
    return {
        "account_key": account_key,
        "domain": "",
        "ok": False,
        "days_left": None,
        "points_total": None,
        "email": "",
        "plan_name": "",
        "vip_level": None,
        "error": last_error or "所有 GLaDOS 域名均不可用",
    }


def main() -> int:
    cookie = os.environ.get("GLADOS_COOKIES", "").strip()
    account_key = os.environ.get("GLADOS_ACCOUNT_KEY", "").strip()
    domains = tuple(
        value.strip().lower()
        for value in os.environ.get("GLADOS_DOMAINS", "glados.cloud,railgun.info").split(",")
        if value.strip()
    )
    if not cookie:
        result = {
            "account_key": account_key,
            "domain": "",
            "ok": False,
            "days_left": None,
            "points_total": None,
            "email": "",
            "plan_name": "",
            "vip_level": None,
            "error": "GLADOS_COOKIES 为空",
        }
    else:
        result = read_status(cookie, account_key, domains)
    print("GLADOS_STATUS_JSON=" + json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
