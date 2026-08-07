from __future__ import annotations

import json
import os
from typing import Iterable

from checkin import AuthenticationError, ChallengeError, GladosAPI, NetworkError, ProtocolError


def read_status(cookie: str, account_key: str, domains: Iterable[str]):
    last_error = ""
    for domain in domains:
        api = GladosAPI(domain, cookie)
        try:
            status = api.status()
            points = api.points()
            return {
                "account_key": account_key,
                "domain": domain,
                "ok": True,
                "days_left": status.get("days_left"),
                "points_total": points,
                "email": status.get("email", ""),
                "plan_name": status.get("plan_name", ""),
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
            "error": "GLADOS_COOKIES 为空",
        }
    else:
        result = read_status(cookie, account_key, domains)
    print("GLADOS_STATUS_JSON=" + json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
