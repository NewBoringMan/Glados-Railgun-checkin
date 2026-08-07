#!/usr/bin/env python3
"""Privacy-minimized GLaDOS V3 orchestration.

The runtime intentionally emits only coarse operational state. It never writes
email addresses, Cookie values, exact balances, account keys, or points history
to public GitHub Actions logs.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from checkin import (
    AuthenticationError,
    ChallengeError,
    Config,
    ExchangePlan,
    GladosAPI,
    NetworkError,
    ProtocolError,
    load_exchange_catalog,
    run_one_account,
)
from status import build_checkin_history

TAIPEI = ZoneInfo("Asia/Taipei")
CHECKIN_MARKERS = ("checkin", "check-in", "checked in", "签到", "打卡", "observation", "logging")
NON_CHECKIN_MARKERS = ("exchange", "redeem", "兑换", "invite", "referral", "邀请")


def _to_positive_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def extract_live_plans(payload: dict[str, Any]) -> dict[str, tuple[int, int]] | None:
    """Return live plan map when the API exposes a ``plans`` field.

    ``None`` means the endpoint did not expose live plan metadata, so the trusted
    fallback catalog may still be used. An empty dict means metadata was present
    but could not be safely parsed, which is a fail-closed signal for exchange.
    """
    if "plans" not in payload:
        return None
    raw = payload.get("plans")
    result: dict[str, tuple[int, int]] = {}

    if isinstance(raw, dict):
        iterable: Iterable[tuple[str | None, Any]] = raw.items()
    elif isinstance(raw, list):
        iterable = ((None, item) for item in raw)
    else:
        return result

    for fallback_id, item in iterable:
        if not isinstance(item, dict):
            continue
        plan_id = str(
            item.get("id")
            or item.get("planType")
            or item.get("plan_type")
            or fallback_id
            or ""
        ).strip()
        points = _to_positive_int(item.get("points"))
        days = _to_positive_int(item.get("days"))
        if not plan_id or points is None or days is None:
            continue
        result[plan_id] = (points, days)
    return result


def trusted_live_intersection(
    trusted: Iterable[ExchangePlan],
    live: dict[str, tuple[int, int]] | None,
) -> list[ExchangePlan]:
    trusted_list = [plan for plan in trusted if plan.verified]
    if live is None:
        return trusted_list
    return [
        plan
        for plan in trusted_list
        if live.get(plan.plan_id) == (plan.points, plan.days)
    ]


def _read_live_plans(cookie: str, domains: tuple[str, ...]) -> dict[str, tuple[int, int]] | None:
    last_error: Exception | None = None
    for domain in domains:
        api = GladosAPI(domain, cookie)
        try:
            api.status()
            payload = api._request_json("GET", "/api/user/points")
            return extract_live_plans(payload)
        except (NetworkError, ProtocolError) as exc:
            last_error = exc
            continue
        except (AuthenticationError, ChallengeError):
            raise
        finally:
            api.close()
    if last_error:
        raise last_error
    return None


def _explicit_today_checkin(cookie: str, domains: tuple[str, ...]) -> bool:
    """Conservative manual-recovery fallback.

    Scheduled recovery primarily relies on the morning GitHub job conclusion.
    This helper is only a second line of defense for manual recovery runs and
    skips the POST only when GLaDOS history contains explicit check-in wording.
    """
    today = datetime.now(TAIPEI).date().isoformat()
    for domain in domains:
        api = GladosAPI(domain, cookie)
        try:
            api.status()
            payload = api._request_json("GET", "/api/user/points")
            raw_history = payload.get("history")
            if not isinstance(raw_history, list):
                return False
            history, _, _ = build_checkin_history(payload, today=datetime.now(TAIPEI).date(), window_days=2)
            today_known = any(row.get("date") == today and row.get("state") == "checked" for row in history)
            if not today_known:
                return False
            for row in raw_history:
                if not isinstance(row, dict):
                    continue
                text = " ".join(
                    str(row.get(key, "")).strip().lower()
                    for key in ("type", "reason", "description", "source", "message", "remark", "title", "action", "name")
                    if row.get(key) is not None
                )
                if any(marker in text for marker in NON_CHECKIN_MARKERS):
                    continue
                if any(marker in text for marker in CHECKIN_MARKERS):
                    return True
            return False
        except (NetworkError, ProtocolError):
            continue
        finally:
            api.close()
    return False


def execute() -> dict[str, Any]:
    config = Config()
    if len(config.cookies) != 1:
        raise ProtocolError("V3 requires exactly one account secret per matrix job")
    cookie = config.cookies[0]
    mode = os.environ.get("GLADOS_RUN_MODE", "primary").strip().lower()
    if mode not in {"primary", "recovery", "read_only"}:
        mode = "primary"

    if mode == "read_only":
        last_error = None
        for domain in config.domains:
            api = GladosAPI(domain, cookie)
            try:
                api.status()
                api._request_json("GET", "/api/user/points")
                return {"mode": mode, "checkin": "not-run", "exchange": "not-run", "exchange_policy": "not-run", "ok": True}
            except (NetworkError, ProtocolError) as exc:
                last_error = exc
                continue
            finally:
                api.close()
        if last_error:
            raise last_error
        raise NetworkError("No GLaDOS domain was reachable")

    if mode == "recovery" and _explicit_today_checkin(cookie, config.domains):
        return {"mode": mode, "checkin": "already-confirmed", "exchange": "not-needed", "ok": True}

    catalog = load_exchange_catalog(config.catalog_path)
    effective_catalog = catalog
    auto_exchange = config.auto_exchange
    exchange_policy = "disabled" if not auto_exchange else "trusted-fallback"

    if auto_exchange:
        live = _read_live_plans(cookie, config.domains)
        effective_catalog = trusted_live_intersection(catalog, live)
        if live is None:
            exchange_policy = "trusted-fallback"
        elif effective_catalog:
            exchange_policy = "trusted-live-intersection"
        else:
            # Live metadata exists but does not exactly match anything in the
            # verified catalog. Check-in remains allowed; spending is blocked.
            auto_exchange = False
            effective_catalog = catalog
            exchange_policy = "blocked-unverified-live-plans"

    result = run_one_account(
        cookie,
        1,
        account_key=config.account_key,
        auto_exchange=auto_exchange,
        catalog=effective_catalog,
        domains=config.domains,
    )
    exchange_state = result.exchange
    if config.auto_exchange and not auto_exchange:
        exchange_state = "blocked-unverified-live-plans"
    return {
        "mode": mode,
        "checkin": result.checkin,
        "exchange": exchange_state,
        "exchange_policy": exchange_policy,
        "ok": result.success,
        "error_type": "" if not result.error else "account_error",
    }


def main() -> int:
    try:
        result = execute()
        print("GLaDOS V3: " + json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 0 if result.get("ok") else 1
    except (AuthenticationError, ChallengeError, NetworkError, ProtocolError) as exc:
        print("GLaDOS V3: " + json.dumps({"ok": False, "error_type": type(exc).__name__}, separators=(",", ":")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
