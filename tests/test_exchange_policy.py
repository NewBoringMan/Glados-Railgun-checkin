import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from checkin import ExchangePlan, load_exchange_catalog, run_one_account, select_best_exchange_plan


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
        self._points -= 500
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
