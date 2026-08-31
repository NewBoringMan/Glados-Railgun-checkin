from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import requests

try:
    from pypushdeer import PushDeer
except ImportError:
    PushDeer = None

from logging_config import init_logger


LOGGER = init_logger()
DEFAULT_DOMAINS = ("glados.cloud", "railgun.info")
DEFAULT_CATALOG_PATH = Path(".github/glados/exchange_plans.json")
DEFAULT_ACCOUNT_POLICIES_PATH = Path(".github/glados/account_policies.json")
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
CHALLENGE_HINTS = ("captcha", "challenge", "access denied", "cloudflare", "人机验证", "验证码")


class CheckinError(RuntimeError):
    pass


class AuthenticationError(CheckinError):
    pass


class ChallengeError(CheckinError):
    pass


class NetworkError(CheckinError):
    pass


class ProtocolError(CheckinError):
    pass


@dataclass(frozen=True)
class ExchangePlan:
    plan_id: str
    points: int
    days: int
    verified: bool = True

    @property
    def cost_per_day(self) -> Fraction:
        return Fraction(self.points, self.days)


@dataclass
class AccountResult:
    account_index: int
    account_key: str
    domain: str = ""
    checkin: str = "failed"
    message: str = ""
    points_added: int = 0
    points_total: Optional[int] = None
    days_left: Optional[int] = None
    email: str = ""
    plan_name: str = ""
    auto_exchange: bool = False
    exchange_policy: str = "auto"
    exchange_policy_source: str = "default"
    policy_warning: str = ""
    exchange_plan: str = ""
    exchange_threshold: Optional[int] = None
    exchange_days: Optional[int] = None
    exchange_cost_per_day: Optional[float] = None
    exchange: str = "disabled"
    points_needed: Optional[int] = None
    points_after_exchange: Optional[int] = None
    status_warning: str = ""
    error: str = ""

    @property
    def success(self) -> bool:
        # A completed check-in with a failed points/exchange verification is
        # operationally degraded and must make Actions fail visibly.
        return self.checkin in {"success", "already"} and not self.error


class Config:
    def __init__(self, env: Optional[Dict[str, str]] = None):
        source = os.environ if env is None else env
        raw = source.get("GLADOS_COOKIES", "")
        self.cookies: List[str] = [item.strip() for item in raw.split("&") if item.strip()]
        self.push_key = source.get("PUSHDEER_SENDKEY", "").strip()
        self.verbose = _as_bool(source.get("GLADOS_VERBOSE"), False)
        self.auto_exchange = _as_bool(source.get("GLADOS_AUTO_EXCHANGE"), False)
        self.account_key = source.get("GLADOS_ACCOUNT_KEY", "").strip()
        self.catalog_path = Path(source.get("GLADOS_EXCHANGE_CATALOG", str(DEFAULT_CATALOG_PATH)))
        self.account_policies_path = Path(
            source.get("GLADOS_ACCOUNT_POLICIES_FILE", str(DEFAULT_ACCOUNT_POLICIES_PATH))
        )
        self.domains = tuple(
            item.strip().lower()
            for item in source.get("GLADOS_DOMAINS", ",".join(DEFAULT_DOMAINS)).split(",")
            if item.strip()
        ) or DEFAULT_DOMAINS


class GladosAPI:
    def __init__(self, domain: str, cookie: str, *, verbose: bool = False, session: Optional[requests.Session] = None):
        self.domain = domain
        self.cookie = cookie
        self.verbose = verbose
        self.session = session or requests.Session()
        self.base_url = f"https://{domain}"
        self.headers = {
            "accept": "application/json, text/plain, */*",
            "cookie": cookie,
            "origin": self.base_url,
            "referer": f"{self.base_url}/console/checkin",
            "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/150 Safari/537.36",
        }

    def close(self) -> None:
        self.session.close()

    def _request_json(self, method: str, path: str, *, data: Optional[Dict[str, Any]] = None, allow_retry: bool = True) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        attempts = 3 if allow_retry else 1
        last_error: Optional[BaseException] = None
        for attempt in range(attempts):
            try:
                response = self.session.request(
                    method,
                    url,
                    headers=self.headers,
                    json=data if method.upper() == "POST" else None,
                    timeout=(5, 15),
                )
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_error = exc
                if attempt + 1 >= attempts:
                    raise NetworkError(f"{self.domain} 网络请求失败") from exc
                time.sleep(min(1.5 * (2**attempt), 6.0))
                continue
            except requests.RequestException as exc:
                raise NetworkError(f"{self.domain} 请求异常") from exc

            if response.status_code in {401, 403}:
                body = (response.text or "").lower()
                if any(hint in body for hint in CHALLENGE_HINTS):
                    raise ChallengeError(f"{self.domain} 返回了验证/挑战页面，已停止自动操作")
                raise AuthenticationError(f"{self.domain} 登录信息已失效或无权限（HTTP {response.status_code}）")

            if response.status_code in RETRYABLE_STATUS_CODES:
                if attempt + 1 >= attempts:
                    raise NetworkError(f"{self.domain} 服务暂时不可用（HTTP {response.status_code}）")
                time.sleep(_retry_delay(response, attempt))
                continue

            if response.status_code >= 400:
                raise ProtocolError(f"{self.domain} 接口返回 HTTP {response.status_code}")

            text = response.text or ""
            if any(hint in text.lower() for hint in CHALLENGE_HINTS) and "{" not in text[:10]:
                raise ChallengeError(f"{self.domain} 返回了验证/挑战页面，已停止自动操作")
            try:
                payload = response.json()
            except ValueError as exc:
                raise ProtocolError(f"{self.domain} 接口返回非 JSON 内容") from exc
            if not isinstance(payload, dict):
                raise ProtocolError(f"{self.domain} 接口 JSON 顶层不是对象")
            return payload

        raise NetworkError(f"{self.domain} 请求失败: {last_error}")

    def status(self) -> Dict[str, Any]:
        """Read optional account metadata across legacy and newer response envelopes."""
        payload = self._request_json("GET", "/api/user/status")
        return {
            "days_left": _first_int(payload, ("leftDays", "daysLeft", "left_days")),
            "email": _first_string(payload, ("email", "userEmail")),
            "plan_name": _first_string(payload, ("plan", "planName", "membership", "vipType")),
        }

    def checkin(self) -> Dict[str, Any]:
        payload = self._request_json(
            "POST",
            "/api/user/checkin",
            data={"token": self.domain},
            allow_retry=False,
        )
        code = payload.get("code")
        message = str(payload.get("message", ""))
        if code == 0:
            return {
                "state": "success",
                "message": message,
                "points_added": _extract_points_added(payload, message),
            }
        if code == 1:
            return {"state": "already", "message": message, "points_added": 0}
        raise ProtocolError(f"签到接口业务拒绝，code={code!r}, message={message[:120]}")

    def points(self) -> int:
        payload = self._request_json("GET", "/api/user/points")
        points = _first_int(payload, ("points", "point", "pointsTotal", "points_total"))
        if points is None:
            raise ProtocolError("积分接口缺少 points")
        return points

    def exchange(self, plan_id: str) -> str:
        payload = self._request_json(
            "POST",
            "/api/user/exchange",
            data={"planType": plan_id},
            allow_retry=False,
        )
        if payload.get("code") != 0:
            raise ProtocolError(
                f"兑换接口业务拒绝，code={payload.get('code')!r}, message={str(payload.get('message', ''))[:120]}"
            )
        return str(payload.get("message") or "兑换成功")


def load_exchange_catalog(path: Path) -> List[ExchangePlan]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ProtocolError(f"兑换方案目录不存在：{path}") from exc
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"兑换方案目录 JSON 无法解析：{path}") from exc
    plans_raw = raw.get("plans") if isinstance(raw, dict) else None
    if not isinstance(plans_raw, list):
        raise ProtocolError("兑换方案目录缺少 plans 数组")
    plans: List[ExchangePlan] = []
    seen = set()
    for item in plans_raw:
        if not isinstance(item, dict):
            continue
        plan_id = str(item.get("id", "")).strip()
        if not re.fullmatch(r"plan[A-Za-z0-9_-]+", plan_id):
            continue
        try:
            points = int(item.get("points"))
            days = int(item.get("days"))
        except (TypeError, ValueError):
            continue
        verified = bool(item.get("verified", False))
        if points <= 0 or days <= 0 or not verified or plan_id in seen:
            continue
        seen.add(plan_id)
        plans.append(ExchangePlan(plan_id, points, days, True))
    if not plans:
        raise ProtocolError("兑换方案目录中没有经过验证的有效方案")
    return plans


def select_best_exchange_plan(plans: Iterable[ExchangePlan]) -> ExchangePlan:
    candidates = [plan for plan in plans if plan.verified and plan.points > 0 and plan.days > 0]
    if not candidates:
        raise ValueError("没有可选择的已验证兑换方案")
    return min(candidates, key=lambda plan: (plan.cost_per_day, plan.days, plan.points, plan.plan_id))


def load_account_policies(path: Path) -> str:
    """Load the non-sensitive account policy file without ever gating check-in.

    Missing/unreadable configuration deliberately falls back to smart auto.
    Malformed JSON is handled by parse_account_policies so each result carries
    an explicit policy warning.
    """
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    except OSError as exc:
        LOGGER.warning("账号兑换策略文件读取失败，已回退智能最优: %s", exc)
        return ""


def parse_account_policies(raw: str) -> tuple[str, Dict[str, str], str]:
    """Parse non-sensitive per-account exchange policy configuration.

    Expected shape:
    {"version":1,"default":"auto","accounts":{"ACCOUNT_KEY":"plan200"}}
    """
    if not raw:
        return "auto", {}, ""

    warnings: List[str] = []
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError) as exc:
        return "auto", {}, f"账号兑换策略配置无法解析，已回退智能最优：{exc}"

    if not isinstance(payload, dict):
        return "auto", {}, "账号兑换策略配置不是对象，已回退智能最优"

    default_policy = str(payload.get("default", "auto") or "auto").strip()
    if default_policy != "auto" and not re.fullmatch(r"plan[A-Za-z0-9_-]+", default_policy):
        warnings.append(f"默认兑换策略 {default_policy!r} 无效，已回退智能最优")
        default_policy = "auto"

    accounts_raw = payload.get("accounts", {})
    accounts: Dict[str, str] = {}
    if accounts_raw is None:
        accounts_raw = {}
    if not isinstance(accounts_raw, dict):
        warnings.append("账号兑换策略 accounts 不是对象，已忽略账号级配置")
    else:
        for key, value in accounts_raw.items():
            account_key = str(key or "").strip().upper()
            policy = str(value or "").strip()
            if not account_key:
                continue
            if policy == "auto" or re.fullmatch(r"plan[A-Za-z0-9_-]+", policy):
                accounts[account_key] = policy
            else:
                warnings.append(f"账号 {account_key} 的兑换策略 {policy!r} 无效，已忽略")

    return default_policy, accounts, "；".join(warnings)


def resolve_exchange_plan(
    plans: Iterable[ExchangePlan], account_key: str, raw_policies: str
) -> tuple[ExchangePlan, str, str, str]:
    catalog = list(plans)
    default_policy, accounts, warning = parse_account_policies(raw_policies)
    normalized_key = (account_key or "").strip().upper()
    source = "account" if normalized_key and normalized_key in accounts else "default"
    configured_policy = accounts.get(normalized_key, default_policy)

    if configured_policy == "auto":
        return select_best_exchange_plan(catalog), "auto", source, warning

    selected = next((plan for plan in catalog if plan.plan_id == configured_policy and plan.verified), None)
    if selected is not None:
        return selected, configured_policy, source, warning

    fallback = select_best_exchange_plan(catalog)
    extra = f"兑换策略 {configured_policy} 不存在或未验证，已回退智能最优 {fallback.plan_id}"
    warning = f"{warning}；{extra}" if warning else extra
    return fallback, "auto", "fallback", warning


def _refresh_optional_status(api: GladosAPI, result: AccountResult) -> None:
    """Best-effort metadata refresh that can never downgrade a completed check-in."""
    try:
        status = api.status()
        result.days_left = status.get("days_left")
        result.email = status.get("email", "")
        result.plan_name = status.get("plan_name", "")
    except (AuthenticationError, ChallengeError, NetworkError, ProtocolError) as exc:
        result.status_warning = str(exc)


def run_one_account(
    cookie: str,
    account_index: int,
    *,
    account_key: str,
    auto_exchange: bool,
    catalog: List[ExchangePlan],
    domains: Iterable[str],
    account_policies: str = "",
    api_factory=GladosAPI,
) -> AccountResult:
    result = AccountResult(
        account_index=account_index,
        account_key=account_key or f"legacy-{account_index}",
        auto_exchange=auto_exchange,
    )
    selected_plan, configured_policy, policy_source, policy_warning = resolve_exchange_plan(
        catalog, result.account_key, account_policies
    )
    result.exchange_policy = configured_policy
    result.exchange_policy_source = policy_source
    result.policy_warning = policy_warning
    result.exchange_plan = selected_plan.plan_id
    result.exchange_threshold = selected_plan.points
    result.exchange_days = selected_plan.days
    result.exchange_cost_per_day = float(selected_plan.cost_per_day)

    # The write path is intentionally first. Read-only status/points APIs must
    # never gate the check-in request itself.
    selected_api = None
    last_error = ""
    for domain in domains:
        api = api_factory(domain, cookie)
        try:
            outcome = api.checkin()
            result.checkin = outcome["state"]
            result.message = outcome["message"]
            result.points_added = int(outcome.get("points_added", 0))
            result.domain = domain
            selected_api = api
            break
        except (NetworkError, ProtocolError) as exc:
            last_error = str(exc)
            api.close()
            continue
        except (AuthenticationError, ChallengeError) as exc:
            api.close()
            result.error = str(exc)
            return result

    if selected_api is None:
        result.error = last_error or "所有 GLaDOS 域名均不可用"
        return result

    try:
        try:
            result.points_total = selected_api.points()
        except (AuthenticationError, ChallengeError, NetworkError, ProtocolError) as exc:
            result.error = f"签到已完成，但积分/兑换检查失败：{exc}"
            result.exchange = "check_failed" if auto_exchange else "disabled"
            _refresh_optional_status(selected_api, result)
            return result

        if not auto_exchange:
            result.exchange = "disabled"
            _refresh_optional_status(selected_api, result)
            return result

        if result.points_total < selected_plan.points:
            result.exchange = "waiting"
            result.points_needed = selected_plan.points - result.points_total
            _refresh_optional_status(selected_api, result)
            return result

        try:
            selected_api.exchange(selected_plan.plan_id)
            result.exchange = "success"
        except (AuthenticationError, ChallengeError, NetworkError, ProtocolError) as exc:
            result.exchange = "failed"
            result.error = f"签到已完成，但自动兑换失败：{exc}"
            _refresh_optional_status(selected_api, result)
            return result

        try:
            result.points_after_exchange = selected_api.points()
        except (AuthenticationError, ChallengeError, NetworkError, ProtocolError) as exc:
            result.error = f"兑换已提交，但兑换后积分复核失败：{exc}"

        _refresh_optional_status(selected_api, result)
        return result
    finally:
        selected_api.close()


def format_human_summary(result: AccountResult) -> str:
    lines = [
        f"账号 {result.account_key}",
        f"域名: {result.domain or '-'}",
        f"签到: {result.checkin}",
        f"当前积分: {result.points_total if result.points_total is not None else '-'}",
        f"剩余天数: {result.days_left if result.days_left is not None else '-'}",
        f"自动兑换: {'开启' if result.auto_exchange else '关闭'}",
    ]
    if result.exchange_threshold is not None:
        policy_label = "智能最优" if result.exchange_policy == "auto" else f"固定 {result.exchange_policy}"
        lines.append(
            f"兑换策略: {policy_label} · 本次 {result.exchange_plan} · {result.exchange_threshold} 分 → {result.exchange_days} 天 · {result.exchange_cost_per_day:.3f} 分/天"
        )
    if result.policy_warning:
        lines.append(f"策略警告: {result.policy_warning}")
    if result.exchange == "waiting":
        lines.append(f"兑换: 未触发 · 还差 {result.points_needed} 分")
    elif result.exchange == "success":
        lines.append(f"兑换: 成功 · 兑换后积分 {result.points_after_exchange}")
    elif result.exchange == "disabled":
        lines.append("兑换: 已关闭")
    else:
        lines.append(f"兑换: {result.exchange}")
    if result.status_warning:
        lines.append(f"状态辅助信息: 暂不可用 · {result.status_warning}")
    if result.error:
        lines.append(f"错误: {result.error}")
    return "\n".join(lines)


def send_push(push_key: str, title: str, content: str) -> None:
    if not push_key:
        return
    if PushDeer is None:
        LOGGER.warning("未安装 pypushdeer，跳过推送。")
        return
    try:
        PushDeer(pushkey=push_key).send_text(title, desp=content)
    except Exception as exc:
        LOGGER.warning("推送失败: %s", exc)


def main() -> int:
    config = Config()
    if not config.cookies:
        LOGGER.error("未找到有效的 GLADOS_COOKIES。")
        return 2
    try:
        catalog = load_exchange_catalog(config.catalog_path)
        best = select_best_exchange_plan(catalog)
    except CheckinError as exc:
        LOGGER.error("兑换方案目录不可用: %s", exc)
        return 2

    LOGGER.info(
        "最优已验证兑换方案: %s · %d 分 → %d 天 · %.3f 分/天",
        best.plan_id,
        best.points,
        best.days,
        float(best.cost_per_day),
    )
    LOGGER.info("自动兑换: %s", "开启" if config.auto_exchange else "关闭")
    account_policies = load_account_policies(config.account_policies_path)

    results: List[AccountResult] = []
    for index, cookie in enumerate(config.cookies, 1):
        result = run_one_account(
            cookie,
            index,
            account_key=config.account_key,
            auto_exchange=config.auto_exchange,
            catalog=catalog,
            domains=config.domains,
            account_policies=account_policies,
        )
        results.append(result)
        LOGGER.info("\n%s", format_human_summary(result))
        print("GLADOS_RESULT_JSON=" + json.dumps(asdict(result), ensure_ascii=False, separators=(",", ":")))

    success_count = sum(1 for result in results if result.success)
    failure_count = len(results) - success_count
    title = f"GLaDOS 签到 · 成功 {success_count} · 失败 {failure_count}"
    content = "\n\n".join(format_human_summary(item) for item in results)
    send_push(config.push_key, title, content)
    return 0 if failure_count == 0 else 1


def _as_bool(value: Optional[str], default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off", ""}:
        return False
    return default


def _to_int(value: Any, field: str) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError) as exc:
        raise ProtocolError(f"{field} 不是有效数字") from exc


def _candidate_dicts(data: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    payload_data = data.get("data")
    if isinstance(payload_data, dict):
        yield payload_data
        nested_user = payload_data.get("user")
        if isinstance(nested_user, dict):
            yield nested_user
    user = data.get("user")
    if isinstance(user, dict):
        yield user
    yield data


def _first_string(data: Dict[str, Any], keys: Iterable[str]) -> str:
    for source in _candidate_dicts(data):
        for key in keys:
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _first_int(data: Dict[str, Any], keys: Iterable[str]) -> Optional[int]:
    for source in _candidate_dicts(data):
        for key in keys:
            value = source.get(key)
            if value is None or isinstance(value, bool):
                continue
            try:
                return int(float(value))
            except (TypeError, ValueError):
                continue
    return None


def _extract_points_added(payload: Dict[str, Any], message: str) -> int:
    value = payload.get("points")
    if value is not None:
        try:
            return _to_int(value, "points")
        except ProtocolError:
            pass
    match = re.search(r"got\s+(\d+)\s+points?", message, re.IGNORECASE)
    if match:
        return int(match.group(1))
    match = re.search(r"获得\s*(\d+)\s*点", message)
    return int(match.group(1)) if match else 0


def _retry_delay(response: requests.Response, attempt: int) -> float:
    retry_after = response.headers.get("Retry-After", "").strip()
    if retry_after:
        try:
            return max(0.0, min(float(retry_after), 30.0))
        except ValueError:
            pass
    return min(1.5 * (2**attempt), 8.0)


if __name__ == "__main__":
    raise SystemExit(main())
