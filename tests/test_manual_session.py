import json
import unittest
from unittest.mock import patch

import status
from checkin import (
    AuthenticationRejected, ChallengeError, Config, DeviceMismatchError,
    ExchangePlan, GladosAPI, IdentityMismatchError, NetworkError, run_one_account,
)
from session_context import (
    SessionContextError, account_key_from_user_id, parse_session, split_sessions,
)


ACCOUNT_KEY = account_key_from_user_id("synthetic-manual-account")


def packet(**overrides):
    value = {
        "schema": "glados.manual-session", "version": 1,
        "accountKey": ACCOUNT_KEY, "email": "synthetic@example.com",
        "cookieHeader": "gld:sess=opaque&test=1; gld:sess.sig=synthetic-signature==; cf_clearance=synthetic-clearance",
        "host": "glados.cloud", "userAgent": "Synthetic browser / 1.0",
        "browser": "edge", "capturedAt": "2026-10-06T01:00:00Z",
    }
    value.update(overrides)
    return json.dumps(value)


class Response:
    def __init__(self, payload=None, *, status_code=200, text=None):
        self.payload = payload
        self.status_code = status_code
        self.headers = {}
        self.text = text if text is not None else json.dumps(payload)

    def json(self):
        if self.payload is None:
            raise ValueError("synthetic non-JSON response")
        return self.payload


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.closed = False

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    def close(self):
        self.closed = True


class ManualContextTests(unittest.TestCase):
    def test_json_is_parsed_before_legacy_ampersand_separator(self):
        raw = packet()
        self.assertEqual(Config({"GLADOS_COOKIES": raw}).cookies, [raw])
        self.assertIn("opaque&test=1", parse_session(raw).cookie_header)
        self.assertEqual(split_sessions("koa:sess=a&koa:sess=b"), ["koa:sess=a", "koa:sess=b"])

    def test_invalid_json_never_falls_through_as_a_cookie(self):
        with self.assertRaises(SessionContextError):
            Config({"GLADOS_COOKIES": '{"schema":"glados.manual-session", & invalid'})

    def test_opaque_gld_and_old_koa_data_do_not_invent_an_expiry(self):
        raw = packet(
            capturedAt="2020-01-01T00:00:00Z",
            cookieHeader="gld:sess=opaque-not-base64; gld:sess.sig=sig; koa:sess=eyJfZXhwaXJlIjoxfQ==; koa:sess.sig=legacy",
        )
        context = parse_session(raw, ACCOUNT_KEY)
        self.assertTrue(context.structured)
        self.assertEqual(context.captured_at, "2020-01-01T00:00:00Z")

    def test_original_host_overrides_legacy_fallback_order(self):
        context = parse_session(packet(host="glados.network"))
        self.assertEqual(context.domains(["glados.cloud", "glados-facility.com"]), ("glados.network",))

    def test_context_validation_rejects_incomplete_or_unsafe_data(self):
        invalid_cases = [
            {"cookieHeader": "gld:sess=only-half"},
            {"cookieHeader": "gld:sess=a; gld:sess.sig=b; koa:sess=only-half"},
            {"cookieHeader": "gld:sess=a; gld:sess.sig=b; gld:sess=c"},
            {"cookieHeader": "gld:sess=a\r\nX-Leak: value; gld:sess.sig=b"},
            {"userAgent": ""}, {"userAgent": "browser\nX-Forged: 1"},
            {"host": "https://glados.cloud"}, {"host": "glados.cloud.invalid"},
            {"version": True}, {"schema": "another.format"},
            {"email": "not-an-email"}, {"accountKey": "WRONG"},
            {"capturedAt": "2026-10-06T00:00:00"},
        ]
        for override in invalid_cases:
            with self.subTest(field=next(iter(override))), self.assertRaises(SessionContextError):
                parse_session(packet(**override))

    def test_wrong_account_is_rejected_before_any_request(self):
        calls = []
        result = run_one_account(
            packet(), 1, account_key="FFFFFFFFFFFFFFFF", auto_exchange=False,
            catalog=[ExchangePlan("plan500", 500, 100)], domains=["glados.cloud"],
            api_factory=lambda *args: calls.append(args),
        )
        self.assertEqual(calls, [])
        self.assertEqual(result.error_kind, "invalid_session_context")

    def test_legacy_credentials_still_work_but_report_missing_context(self):
        context = parse_session("koa:sess=synthetic; koa:sess.sig=signature")
        self.assertFalse(context.structured)
        self.assertIn("原浏览器", context.warning)

    def test_secrets_are_not_exposed_by_object_repr_or_validation_errors(self):
        context = parse_session(packet())
        self.assertNotIn("opaque&test", repr(context))
        self.assertNotIn("synthetic@example.com", repr(context))
        with self.assertRaises(SessionContextError) as caught:
            parse_session(packet(cookieHeader="PRIVATE-INVALID-COOKIE"))
        self.assertNotIn("PRIVATE-INVALID-COOKIE", str(caught.exception))


class ManualAuthenticationTests(unittest.TestCase):
    def test_actual_browser_ua_cookie_origin_and_no_redirects(self):
        session = Session([Response({"code": 1, "message": "already"})])
        api = GladosAPI("glados.cloud", packet(), session=session)
        api.checkin()
        request = session.calls[0][2]
        self.assertEqual(request["headers"]["user-agent"], "Synthetic browser / 1.0")
        self.assertIn("opaque&test=1", request["headers"]["cookie"])
        self.assertEqual(request["headers"]["origin"], "https://glados.cloud")
        self.assertEqual(request["json"], {"token": "glados.cloud"})
        self.assertFalse(request["allow_redirects"])
        self.assertFalse(any(name.startswith("sec-ch-") for name in request["headers"]))

    def test_api_cannot_send_saved_context_to_a_different_host(self):
        with self.assertRaises(SessionContextError):
            GladosAPI("glados.network", packet(), session=Session([]))

    def test_code_minus_two_is_authorization_refusal_not_missing_points(self):
        api = GladosAPI("glados.cloud", packet(), session=Session([Response({"code": -2, "message": "No permission"})]))
        with self.assertRaises(AuthenticationRejected) as caught:
            api.points()
        self.assertIn("code=-2", str(caught.exception))
        self.assertNotIn("缺少 points", str(caught.exception))

    def test_device_mismatch_reason_survives_even_with_http_403(self):
        session = Session([Response({"code": -2, "message": "No permission", "reason": "device-mismatch"}, status_code=403)])
        api = GladosAPI("glados.cloud", packet(), session=session)
        with self.assertRaises(DeviceMismatchError):
            api.checkin()
        self.assertEqual(len(session.calls), 1)

    def test_anti_automation_and_browser_challenge_stop_without_retry(self):
        for response in [
            Response({"code": -2, "message": "Automated check-in detected"}),
            Response(status_code=403, text="<html>Cloudflare challenge</html>"),
        ]:
            session = Session([response])
            with self.subTest(response=response.status_code), self.assertRaises(ChallengeError):
                GladosAPI("glados.cloud", packet(), session=session).checkin()
            self.assertEqual(len(session.calls), 1)

    def test_redirect_is_not_followed(self):
        session = Session([Response(status_code=302, text="")])
        with self.assertRaises(AuthenticationRejected):
            GladosAPI("glados.cloud", packet(), session=session).points()
        self.assertEqual(len(session.calls), 1)

    def test_server_identity_conflict_is_terminal(self):
        api = GladosAPI("glados.cloud", packet(), session=Session([
            Response({"points": 9, "userId": "another-synthetic-account"})
        ]))
        with self.assertRaises(IdentityMismatchError):
            api.points()

    def test_explicit_auth_refusal_stops_legacy_cross_domain_loop(self):
        created = []

        def factory(domain, cookie):
            session = Session([Response({"code": -2, "message": "No permission"})])
            created.append(session)
            return GladosAPI(domain, cookie, session=session)

        result = run_one_account(
            "legacy-cookie", 1, account_key="LEGACY", auto_exchange=False,
            catalog=[ExchangePlan("plan500", 500, 100)],
            domains=["glados.cloud", "glados.network"], api_factory=factory,
        )
        self.assertEqual(len(created), 1)
        self.assertTrue(created[0].closed)
        self.assertEqual(result.domain, "glados.cloud")
        self.assertEqual(result.error_kind, "authentication_rejected")

    def test_failed_status_retains_bound_email_and_first_error(self):
        sessions = []

        def factory(domain, cookie):
            session = Session([Response({"code": -2, "reason": "device-mismatch"})])
            sessions.append(session)
            return GladosAPI(domain, cookie, session=session)

        with patch.object(status, "GladosAPI", factory):
            result = status.read_status(packet(), ACCOUNT_KEY, ["glados.network", "glados.cloud"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["email"], "synthetic@example.com")
        self.assertEqual(result["error_kind"], "device_mismatch")
        self.assertEqual(result["domain"], "glados.cloud")
        self.assertEqual(len(sessions), 1)

    def test_points_success_is_not_reported_as_checkin_verification(self):
        session = Session([
            Response({"points": 9, "streak": 120}),
            Response({"code": -2, "message": "No permission"}),
        ])
        with patch.object(status, "GladosAPI", lambda domain, cookie: GladosAPI(domain, cookie, session=session)):
            result = status.read_status(packet(), ACCOUNT_KEY, ["glados.cloud"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["authentication_state"], "accepted_for_status")
        self.assertFalse(result["checkin_verified"])
        self.assertEqual(result["streak"], 120)
        self.assertIn("code=-2", result["status_warning"])
        self.assertEqual(result["email"], "synthetic@example.com")

    def test_successful_checkin_auth_error_stops_further_exchange_requests(self):
        session = Session([
            Response({"code": 0, "message": "ok", "points": 1}),
            Response({"code": -2, "reason": "device-mismatch"}),
        ])
        result = run_one_account(
            packet(), 1, account_key=ACCOUNT_KEY, auto_exchange=True,
            catalog=[ExchangePlan("plan500", 500, 100)], domains=["glados.cloud"],
            api_factory=lambda domain, cookie: GladosAPI(domain, cookie, session=session),
        )
        self.assertEqual(result.checkin, "success")
        self.assertEqual(result.error_kind, "device_mismatch")
        self.assertEqual(result.exchange, "check_failed")
        self.assertEqual(len(session.calls), 2)
        self.assertFalse(result.success)

    def test_optional_status_identity_conflict_cannot_be_downgraded_to_warning(self):
        session = Session([
            Response({"points": 9}),
            Response({"data": {"userId": "different-synthetic-account", "email": "other@example.com"}}),
        ])
        with patch.object(status, "GladosAPI", lambda domain, cookie: GladosAPI(domain, cookie, session=session)):
            result = status.read_status(packet(), ACCOUNT_KEY, ["glados.cloud"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_kind"], "identity_mismatch")
        self.assertEqual(result["email"], "synthetic@example.com")

    def test_checkin_optional_status_identity_conflict_is_visible(self):
        session = Session([
            Response({"code": 1, "message": "already"}),
            Response({"points": 9}),
            Response({"data": {"userId": "different-synthetic-account"}}),
        ])
        result = run_one_account(
            packet(), 1, account_key=ACCOUNT_KEY, auto_exchange=False,
            catalog=[ExchangePlan("plan500", 500, 100)], domains=["glados.cloud"],
            api_factory=lambda domain, cookie: GladosAPI(domain, cookie, session=session),
        )
        self.assertFalse(result.success)
        self.assertEqual(result.checkin, "already")
        self.assertEqual(result.error_kind, "identity_mismatch")

    def test_explicit_email_conflict_is_terminal_even_without_user_id(self):
        session = Session([
            Response({"points": 9}),
            Response({"data": {"email": "other@example.com", "leftDays": 1}}),
        ])
        with patch.object(status, "GladosAPI", lambda domain, cookie: GladosAPI(domain, cookie, session=session)):
            result = status.read_status(packet(), ACCOUNT_KEY, ["glados.cloud"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_kind"], "identity_mismatch")
        self.assertEqual(result["email"], "synthetic@example.com")

    def test_points_shape_diagnostic_redacts_short_cookie_value(self):
        session = Session([Response({"code": 88, "message": "invalid opaque&test=1"})])
        with patch.object(status, "GladosAPI", lambda domain, cookie: GladosAPI(domain, cookie, session=session)):
            result = status.read_status(packet(), ACCOUNT_KEY, ["glados.cloud"])
        self.assertFalse(result["ok"])
        self.assertNotIn("opaque&test=1", result["error"])
        self.assertIn("[redacted]", result["error"])

    def test_api_diagnostics_redact_cookie_values_and_email(self):
        session = Session([Response({"code": 88, "message": "invalid opaque&test=1 for synthetic@example.com"})])
        api = GladosAPI("glados.cloud", packet(), session=session)
        with self.assertRaises(Exception) as caught:
            api.checkin()
        self.assertNotIn("opaque&test=1", str(caught.exception))
        self.assertNotIn("synthetic@example.com", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
