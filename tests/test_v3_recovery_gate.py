import unittest
from datetime import datetime
from scripts.v3_recovery_gate import TAIPEI, find_morning_run, slot_succeeded

class RecoveryGateTests(unittest.TestCase):
    def test_finds_completed_morning_run_only(self):
        now=datetime(2026,8,8,17,7,tzinfo=TAIPEI)
        rows=[
            {'databaseId':12,'createdAt':'2026-08-08T09:07:00Z','status':'completed'},
            {'databaseId':11,'createdAt':'2026-08-07T21:07:00Z','status':'completed'},
            {'databaseId':10,'createdAt':'2026-08-07T20:00:00Z','status':'in_progress'},
        ]
        self.assertEqual(find_morning_run(rows,12,now),11)
    def test_slot_success_requires_primary_mode_and_exact_slot(self):
        jobs=[
            {'name':'GLaDOS primary slot 1 · aabbccddeeff0011','conclusion':'success'},
            {'name':'GLaDOS primary slot 2 · 1122334455667788','conclusion':'failure'},
            {'name':'GLaDOS read_only slot 3 · 9988776655443322','conclusion':'success'},
        ]
        self.assertTrue(slot_succeeded(jobs,1)); self.assertFalse(slot_succeeded(jobs,2)); self.assertFalse(slot_succeeded(jobs,3))
    def test_no_same_day_morning_returns_none(self):
        now=datetime(2026,8,8,17,7,tzinfo=TAIPEI)
        rows=[{'databaseId':1,'createdAt':'2026-08-06T21:07:00Z','status':'completed'}]
        self.assertIsNone(find_morning_run(rows,99,now))

if __name__=='__main__': unittest.main()
