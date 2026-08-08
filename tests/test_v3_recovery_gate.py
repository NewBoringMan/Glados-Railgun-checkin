import unittest
from datetime import datetime

from scripts.v3_recovery_gate import TAIPEI, candidate_run_ids, primary_job_succeeded


class RecoveryGateTests(unittest.TestCase):
    def test_scheduled_candidates_require_same_day_morning(self):
        now = datetime(2026, 8, 8, 17, 7, tzinfo=TAIPEI)
        rows = [
            {'databaseId': 12, 'createdAt': '2026-08-08T09:07:00Z', 'status': 'completed'},  # 17:07 Taipei
            {'databaseId': 11, 'createdAt': '2026-08-07T21:07:00Z', 'status': 'completed'},  # 05:07 Taipei
            {'databaseId': 10, 'createdAt': '2026-08-07T20:00:00Z', 'status': 'in_progress'},
        ]
        self.assertEqual(candidate_run_ids(rows, 12, now, morning_only=True), [11])

    def test_manual_candidates_allow_same_day_prior_dispatch(self):
        now = datetime(2026, 8, 8, 17, 7, tzinfo=TAIPEI)
        rows = [
            {'databaseId': 22, 'createdAt': '2026-08-08T08:30:00Z', 'status': 'completed'},
            {'databaseId': 21, 'createdAt': '2026-08-08T02:00:00Z', 'status': 'completed'},
            {'databaseId': 20, 'createdAt': '2026-08-07T02:00:00Z', 'status': 'completed'},
        ]
        self.assertEqual(candidate_run_ids(rows, 99, now, morning_only=False), [22, 21])

    def test_primary_success_requires_exact_slot_and_lock(self):
        jobs = [
            {'name': 'GLaDOS primary slot 1 · aabbccddeeff0011', 'conclusion': 'success'},
            {'name': 'GLaDOS primary slot 2 · 1122334455667788', 'conclusion': 'failure'},
            {'name': 'GLaDOS read_only slot 3 · 9988776655443322', 'conclusion': 'success'},
        ]
        self.assertTrue(primary_job_succeeded(jobs, 1, 'aabbccddeeff0011'))
        self.assertFalse(primary_job_succeeded(jobs, 1, 'ffffffffffffffff'))
        self.assertFalse(primary_job_succeeded(jobs, 2, '1122334455667788'))
        self.assertFalse(primary_job_succeeded(jobs, 3, '9988776655443322'))

    def test_no_same_day_candidate_returns_empty(self):
        now = datetime(2026, 8, 8, 17, 7, tzinfo=TAIPEI)
        rows = [{'databaseId': 1, 'createdAt': '2026-08-06T21:07:00Z', 'status': 'completed'}]
        self.assertEqual(candidate_run_ids(rows, 99, now, morning_only=False), [])


if __name__ == '__main__':
    unittest.main()
