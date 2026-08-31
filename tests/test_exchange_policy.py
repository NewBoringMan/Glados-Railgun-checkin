import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from checkin import (
    ExchangePlan,
    load_account_policies,
    load_exchange_catalog,
    parse_account_policies,
    resolve_exchange_plan,
    run_one_account,
    select_best_exchange_plan,
)


class FakeAPI:
    def __init__(self, domain, cookie, *, points=0, fail_status=False, fail_checkin=False, fail_points=False):
        self.domain = domain
        self.cookie = cookie
        self._points = points
        self.exchange_calls = []
        self.closed = False
        self.fail_status = fail_status
        self.fail_checkin = fail_checkin
        self.fail_points = fail_points
        self.calls = []

    def status(self):
        self.calls.append("status")
        if self.fail_status:
            from checkin import NetworkError
            raise NetworkError("status down")
        return {"days_left": 100, "email": "", "plan_name": "Edu"}

    def checkin(self):
        self.calls.append("checkin")
        if self.fail_checkin:
            from checkin import NetworkError
            raise NetworkError("checkin down")
        return {"state": "already", "message": "already", "points_added": 0}

    def points(self):
        self.calls.append("points")
        if self.fail_points:
            from checkin import NetworkError
            raise NetworkError("points down")
        return self._points

    def exchange(self, plan_id):
        self.exchange_calls.append(plan_id)
        costs = {"plan100": 100, "plan200": 200, "plan500": 500, "plan800": 800, "plan900": 900}
        self._points -= costs.get(plan_id, 0)
        return "ok"

    def close(self):
        self.closed = True


class ExchangePolicyTests(unittest.TestCase):
    def test_current_catalog_selects_500(self):
        plans = [
            ExchangePlan("plan100", 100, 10),
            ExchangePlan("plan200", 200, 30),
            ExchangePlan("plan500", 500, 100),
        ]
        self.assertEqual(select_best_exchange_plan(plans).plan_id, "plan500")

    def test_800_for_200_days_beats_500(self):
        plans = [ExchangePlan("plan500", 500, 100), ExchangePlan("plan800", 800, 200)]
        self.assertEqual(select_best_exchange_plan(plans).plan_id, "plan800")

    def test_equal_ratio_prefers_shorter_days(self):
        plans = [ExchangePlan("plan500", 500, 100), ExchangePlan("plan900", 900, 180)]
        self.assertEqual(select_best_exchange_plan(plans).plan_id, "plan500")

    def test_unverified_plan_is_ignored(self):
        plans = [ExchangePlan("plan500", 500, 100), ExchangePlan("plan1", 1, 1000, False)]
        self.assertEqual(select_best_exchange_plan(plans).plan_id, "plan500")

    def test_missing_policy_file_loads_as_smart_auto(self):
        with TemporaryDirectory() as tmp:
            raw = load_account_policies(Path(tmp) / "missing.json")
        self.assertEqual(raw, "")
        default, accounts, warning = parse_account_policies(raw)
        self.assertEqual((default, accounts, warning), ("auto", {}, ""))

    def test_policy_file_loads_exact_content(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "account_policies.json"
            path.write_text('{"version":1,"default":"auto","accounts":{"A":"plan200"}}', encoding="utf-8")
            raw = load_account_policies(path)
        self.assertIn('"A":"plan200"', raw)

    def test_account_policy_parser_defaults_to_auto(self):
        default, accounts, warning = parse_account_policies("")
        self.assertEqual(default, "auto")
        self.assertEqual(accounts, {})
        self.assertEqual(warning, "")

    def test_fixed_plan200_overrides_best_plan_for_one_account(self):
        plans = [
            ExchangePlan("plan100", 100, 10),
            ExchangePlan("plan200", 200, 30),
            ExchangePlan("plan500", 500, 100),
        ]
        selected, configured, source, warning = resolve_exchange_plan(
            plans,
            "CLIENT-A",
            '{"version":1,"default":"auto","accounts":{"CLIENT-A":"plan200"}}',
        )
        self.assertEqual(selected.plan_id, "plan200")
        self.assertEqual(configured, "plan200")
        self.assertEqual(source, "account")
        self.assertEqual(warning, "")

    def test_unknown_account_keeps_smart_best_plan(self):
        plans = [
            ExchangePlan("plan100", 100, 10),
            ExchangePlan("plan200", 200, 30),
            ExchangePlan("plan500", 500, 100),
        ]
        selected, configured, source, warning = resolve_exchange_plan(
            plans,
            "OTHER",
            '{"version":1,"default":"auto","accounts":{"CLIENT-A":"plan200"}}',
        )
        self.assertEqual(selected.plan_id, "plan500")
        self.assertEqual(configured, "auto")
        self.assertEqual(source, "default")
        self.assertEqual(warning, "")

    def test_invalid_policy_json_falls_back_to_smart_best_with_warning(self):
        plans = [ExchangePlan("plan200", 200, 30), ExchangePlan("plan500", 500, 100)]
        selected, configured, source, warning = resolve_exchange_plan(plans, "A", "{broken")
        self.assertEqual(selected.plan_id, "plan500")
        self.assertEqual(configured, "auto")
        self.assertEqual(source, "default")
        self.assertIn("回退智能最优", warning)

    def test_unknown_fixed_plan_falls_back_to_smart_best_with_warning(self):
        plans = [ExchangePlan("plan200", 200, 30), ExchangePlan("plan500", 500, 100)]
        selected, configured, source, warning = resolve_exchange_plan(
            plans,
            "A",
            '{"version":1,"default":"auto","accounts":{"A":"plan999"}}',
        )
        self.assertEqual(selected.plan_id, "plan500")
        self.assertEqual(configured, "auto")
        self.assertEqual(source, "fallback")
        self.assertIn("不存在或未验证", warning)

    def test_plan200_waits_at_199_points(self):
        holder = []

        def factory(domain, cookie):
            api = FakeAPI(domain, cookie, points=199)
            holder.append(api)
            return api

        result = run_one_account(
            "cookie",
            1,
            account_key="CLIENT-A",
            auto_exchange=True,
            catalog=[ExchangePlan("plan200", 200, 30), ExchangePlan("plan500", 500, 100)],
            domains=["glados.cloud"],
            account_policies='{"version":1,"default":"auto","accounts":{"CLIENT-A":"plan200"}}',
            api_factory=factory,
        )
        self.assertEqual(result.exchange_policy, "plan200")
        self.assertEqual(result.exchange_plan, "plan200")
        self.assertEqual(result.exchange, "waiting")
        self.assertEqual(result.points_needed, 1)
        self.assertEqual(holder[0].exchange_calls, [])

    def test_plan200_exchanges_once_at_200_points(self):
        holder = []

        def factory(domain, cookie):
            api = FakeAPI(domain, cookie, points=200)
            holder.append(api)
            return api

        result = run_one_account(
            "cookie",
            1,
            account_key="CLIENT-A",
            auto_exchange=True,
            catalog=[ExchangePlan("plan200", 200, 30), ExchangePlan("plan500", 500, 100)],
            domains=["glados.cloud"],
            account_policies='{"version":1,"default":"auto","accounts":{"CLIENT-A":"plan200"}}',
            api_factory=factory,
        )
        self.assertEqual(result.exchange_policy, "plan200")
        self.assertEqual(result.exchange, "success")
        self.assertEqual(holder[0].exchange_calls, ["plan200"])
        self.assertEqual(result.points_after_exchange, 0)

    def test_exchange_disabled_never_calls_exchange(self):
        api_holder = []

        def factory(domain, cookie):
            api = FakeAPI(domain, cookie, points=9999)
            api_holder.append(api)
            return api

        result = run_one_account(
            "cookie",
            1,
            account_key="A",
            auto_exchange=False,
            catalog=[ExchangePlan("plan500", 500, 100)],
            domains=["glados.cloud"],
            api_factory=factory,
        )
        self.assertEqual(result.exchange, "disabled")
        self.assertEqual(api_holder[0].exchange_calls, [])

    def test_below_threshold_never_calls_exchange(self):
        api_holder = []

        def factory(domain, cookie):
            api = FakeAPI(domain, cookie, points=499)
            api_holder.append(api)
            return api

        result = run_one_account(
            "cookie",
            1,
            account_key="A",
            auto_exchange=True,
            catalog=[ExchangePlan("plan500", 500, 100)],
            domains=["glados.cloud"],
            api_factory=factory,
        )
        self.assertEqual(result.exchange, "waiting")
        self.assertEqual(result.points_needed, 1)
        self.assertEqual(api_holder[0].exchange_calls, [])

    def test_threshold_calls_exchange_once(self):
        api_holder = []

        def factory(domain, cookie):
            api = FakeAPI(domain, cookie, points=500)
            api_holder.append(api)
            return api

        result = run_one_account(
            "cookie",
            1,
            account_key="A",
            auto_exchange=True,
            catalog=[ExchangePlan("plan500", 500, 100)],
            domains=["glados.cloud"],
            api_factory=factory,
        )
        self.assertEqual(result.exchange, "success")
        self.assertEqual(api_holder[0].exchange_calls, ["plan500"])
        self.assertEqual(result.points_after_exchange, 0)

    def test_fallback_domain_used_only_after_primary_failure(self):
        apis = []

        def factory(domain, cookie):
            api = FakeAPI(domain, cookie, points=10, fail_checkin=(domain == "glados.cloud"))
            apis.append(api)
            return api

        result = run_one_account(
            "cookie",
            1,
            account_key="A",
            auto_exchange=False,
            catalog=[ExchangePlan("plan500", 500, 100)],
            domains=["glados.cloud", "railgun.info"],
            api_factory=factory,
        )
        self.assertEqual(result.domain, "railgun.info")
        self.assertEqual(len(apis), 2)
        self.assertTrue(apis[0].closed)

    def test_status_failure_never_blocks_checkin(self):
        holder = []

        def factory(domain, cookie):
            api = FakeAPI(domain, cookie, points=20, fail_status=True)
            holder.append(api)
            return api

        result = run_one_account(
            "cookie",
            1,
            account_key="A",
            auto_exchange=True,
            catalog=[ExchangePlan("plan500", 500, 100)],
            domains=["glados.cloud"],
            api_factory=factory,
        )
        self.assertEqual(result.checkin, "already")
        self.assertEqual(result.exchange, "waiting")
        self.assertTrue(result.success)
        self.assertEqual(holder[0].calls[:2], ["checkin", "points"])
        self.assertEqual(holder[0].calls[-1], "status")
        self.assertIn("status down", result.status_warning)

    def test_points_failure_is_visible_but_checkin_stays_completed(self):
        holder = []

        def factory(domain, cookie):
            api = FakeAPI(domain, cookie, points=500, fail_points=True)
            holder.append(api)
            return api

        result = run_one_account(
            "cookie",
            1,
            account_key="A",
            auto_exchange=True,
            catalog=[ExchangePlan("plan500", 500, 100)],
            domains=["glados.cloud"],
            api_factory=factory,
        )
        self.assertEqual(result.checkin, "already")
        self.assertEqual(result.exchange, "check_failed")
        self.assertFalse(result.success)
        self.assertIn("积分/兑换检查失败", result.error)
        self.assertEqual(holder[0].exchange_calls, [])
        self.assertEqual(holder[0].calls[:2], ["checkin", "points"])

    def test_catalog_validation(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "catalog.json"
            path.write_text(
                '{"plans":[{"id":"plan500","points":500,"days":100,"verified":true},{"id":"bad","points":1,"days":999,"verified":false}]}',
                encoding="utf-8",
            )
            plans = load_exchange_catalog(path)
            self.assertEqual([p.plan_id for p in plans], ["plan500"])


if __name__ == "__main__":
    unittest.main()
