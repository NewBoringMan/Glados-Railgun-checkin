#!/usr/bin/env python3
"""V3 orchestration layer.

Uses the tested V2 check-in engine but deliberately emits only operational state.
No email, Cookie, points balance or points history is written to Actions logs.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from checkin import (
    AuthenticationError,
    ChallengeError,
    Config,
    GladosAPI,
    NetworkError,
    ProtocolError,
    load_exchange_catalog,
    run_one_account,
)
from status import build_checkin_history

TAIPEI = ZoneInfo("Asia/Taipei")


def _today_is_checked(cookie: str, domains: tuple[str, ...]) -> bool:
    for domain in domains:
        api = GladosAPI(domain, cookie)
        try:
            api.status()
            payload = api._request_json("GET", "/api/user/points")
            history, _, _ = build_checkin_history(payload, today=datetime.now(TAIPEI).date(), window_days=2)
            return any(row.get("date") == datetime.now(TAIPEI).date().isoformat() and row.get("state") == "checked" for row in history)
        except (NetworkError, ProtocolError):
            continue
        finally:
            api.close()
    return False


def execute() -> dict:
    config = Config()
    if len(config.cookies) != 1:
        raise ProtocolError("V3 requires exactly one account secret per matrix job")
    cookie = config.cookies[0]
    mode = os.environ.get("GLADOS_RUN_MODE", "primary").strip().lower()
    if mode not in {"primary", "recovery"}:
        mode = "primary"

    if mode == "recovery" and _today_is_checked(cookie, config.domains):
        return {"mode": mode, "checkin": "already-confirmed", "exchange": "not-needed", "ok": True}

    catalog = load_exchange_catalog(config.catalog_path)
    result = run_one_account(
        cookie,
        1,
        account_key=config.account_key,
        auto_exchange=config.auto_exchange,
        catalog=catalog,
        domains=config.domains,
    )
    return {
        "mode": mode,
        "checkin": result.checkin,
        "exchange": result.exchange,
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
