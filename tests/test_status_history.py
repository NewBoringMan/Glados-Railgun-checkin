import unittest
from datetime import date, datetime, timezone

from status import build_checkin_history


def ms(y, m, d, h=4):
    return int(datetime(y, m, d, h, tzinfo=timezone.utc).timestamp() * 1000)


class StatusHistoryTests(unittest.TestCase):
    def test_checked_missed_pending_and_unknown(self):
        payload = {
            "points": 50,
            "history": [
                {"time": ms(2026, 8, 4), "change": 2, "reason": "checkin"},
                {"time": ms(2026, 8, 6), "change": 5, "message": "Today's observation logged"},
            ],
        }
        rows, streak, source = build_checkin_history(payload, today=date(2026, 8, 7), window_days=5)
        self.assertEqual([r["state"] for r in rows], ["unknown", "checked", "missed", "checked", "pending"])
        self.assertEqual(streak, 1)
        self.assertEqual(source, "points_history")

    def test_positive_change_without_text_is_fallback_checkin(self):
        payload = {"points": 10, "history": [{"time": ms(2026, 8, 7), "change": "8"}]}
        rows, streak, _ = build_checkin_history(payload, today=date(2026, 8, 7), window_days=1)
        self.assertEqual(rows[0]["state"], "checked")
        self.assertEqual(rows[0]["points_delta"], 8)
        self.assertEqual(streak, 1)

    def test_exchange_and_referral_are_not_checkins(self):
        payload = {
            "points": 10,
            "history": [
                {"time": ms(2026, 8, 5), "change": -500, "reason": "exchange plan500"},
                {"time": ms(2026, 8, 6), "change": 100, "reason": "referral reward"},
            ],
        }
        rows, streak, _ = build_checkin_history(payload, today=date(2026, 8, 7), window_days=3)
        self.assertEqual([r["state"] for r in rows], ["missed", "missed", "pending"])
        self.assertEqual(streak, 0)

    def test_no_history_is_unknown_not_missed(self):
        rows, streak, source = build_checkin_history({"points": 0}, today=date(2026, 8, 7), window_days=3)
        self.assertTrue(all(r["state"] == "unknown" for r in rows))
        self.assertEqual(streak, 0)
        self.assertEqual(source, "unavailable")

    def test_consecutive_streak_includes_today(self):
        payload = {"points": 10, "history": [
            {"time": ms(2026, 8, 5), "change": 1},
            {"time": ms(2026, 8, 6), "change": 1},
            {"time": ms(2026, 8, 7), "change": 1},
        ]}
        _, streak, _ = build_checkin_history(payload, today=date(2026, 8, 7), window_days=3)
        self.assertEqual(streak, 3)


if __name__ == "__main__":
    unittest.main()
