import unittest
from unittest.mock import patch

import status
from checkin import AuthenticationError, NetworkError, ProtocolError


class FakeStatusAPI:
    scenarios = {}

    def __init__(self, domain, cookie):
        self.domain = domain
        self.cookie = cookie
        self.closed = False
        self.calls = []

    def _request_json(self, method, path):
        self.calls.append(path)
        scenario = self.scenarios[self.domain]
        value = scenario[path]
        if isinstance(value, BaseException):
            raise value
        return value

    def close(self):
        self.closed = True


class StatusApiCompatibilityTests(unittest.TestCase):
    def run_status(self, scenarios, domains=("glados.cloud",)):
        FakeStatusAPI.scenarios = scenarios
        with patch.object(status, "GladosAPI", FakeStatusAPI):
            return status.read_status("cookie", "A", domains)

    def test_legacy_data_envelope_is_still_supported(self):
        result = self.run_status({
            "glados.cloud": {
                "/api/user/points": {"points": 321, "streak": 47, "history": []},
                "/api/user/status": {"code": 0, "data": {"leftDays": 88.9, "email": "a@example.com", "vip": 11}},
            }
        })
        self.assertTrue(result["ok"])
        self.assertEqual(result["points_total"], 321)
        self.assertEqual(result["days_left"], 88)
        self.assertEqual(result["email"], "a@example.com")
        self.assertEqual(result["plan_name"], "Edu")
        self.assertEqual(result["streak"], 47)

    def test_status_without_data_does_not_break_points_state(self):
        result = self.run_status({
            "glados.cloud": {
                "/api/user/points": {"points": "42", "streak": 23, "history": []},
                "/api/user/status": {"code": 0, "message": "ok"},
            }
        })
        self.assertTrue(result["ok"])
        self.assertEqual(result["points_total"], 42)
        self.assertEqual(result["streak"], 23)
        self.assertIsNone(result["days_left"])
        self.assertEqual(result["plan_name"], "")
        self.assertEqual(result["error"], "")

    def test_top_level_status_shape_is_supported(self):
        result = self.run_status({
            "glados.cloud": {
                "/api/user/points": {"points": 10, "history": []},
                "/api/user/status": {"leftDays": 17, "userEmail": "top@example.com", "planName": "Education"},
            }
        })
        self.assertTrue(result["ok"])
        self.assertEqual(result["days_left"], 17)
        self.assertEqual(result["email"], "top@example.com")
        self.assertEqual(result["plan_name"], "Education")

    def test_status_network_failure_is_only_a_warning(self):
        result = self.run_status({
            "glados.cloud": {
                "/api/user/points": {"points": 10, "history": []},
                "/api/user/status": NetworkError("status down"),
            }
        })
        self.assertTrue(result["ok"])
        self.assertEqual(result["points_total"], 10)
        self.assertIn("status down", result["status_warning"])
        self.assertEqual(result["error"], "")

    def test_points_failure_falls_back_to_second_domain(self):
        result = self.run_status({
            "glados.cloud": {
                "/api/user/points": ProtocolError("points broken"),
                "/api/user/status": {},
            },
            "railgun.info": {
                "/api/user/points": {"points": 9, "history": []},
                "/api/user/status": {"data": {"leftDays": 5}},
            },
        }, domains=("glados.cloud", "railgun.info"))
        self.assertTrue(result["ok"])
        self.assertEqual(result["domain"], "railgun.info")
        self.assertEqual(result["points_total"], 9)
        self.assertEqual(result["days_left"], 5)

    def test_points_authentication_failure_is_fatal(self):
        result = self.run_status({
            "glados.cloud": {
                "/api/user/points": AuthenticationError("expired"),
                "/api/user/status": {},
            }
        })
        self.assertFalse(result["ok"])
        self.assertIn("expired", result["error"])

    def test_nested_points_shape_is_supported(self):
        result = self.run_status({
            "glados.cloud": {
                "/api/user/points": {"data": {"points": 77, "streak": 31}, "history": []},
                "/api/user/status": {},
            }
        })
        self.assertTrue(result["ok"])
        self.assertEqual(result["points_total"], 77)
        self.assertEqual(result["streak"], 31)


if __name__ == "__main__":
    unittest.main()
