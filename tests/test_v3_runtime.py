import pathlib
import unittest

from checkin import ExchangePlan
from v3_runtime import extract_live_plans, trusted_live_intersection


class RuntimeSafetyTests(unittest.TestCase):
    def test_runtime_does_not_print_sensitive_fields(self):
        text = pathlib.Path('v3_runtime.py').read_text(encoding='utf-8')
        self.assertNotIn('result.email', text)
        self.assertNotIn('points_total', text)
        self.assertNotIn('cookie=', text.lower())
        self.assertNotIn('"account_key": result.account_key', text)
        self.assertNotIn('"account_key": account_key', text)
        self.assertIn('blocked-unverified-live-plans', text)
        self.assertIn('read_only', text)

    def test_workflow_uses_dynamic_secret_name_but_not_in_logs(self):
        workflow = pathlib.Path('.github/workflows/v3Checkin.yml').read_text(encoding='utf-8')
        self.assertIn('secrets[matrix.secret_name]', workflow)
        self.assertIn('max-parallel: 1', workflow)
        self.assertIn('Skip when primary slot already succeeded', workflow)
        self.assertIn('options: [primary, recovery, read_only]', workflow)
        self.assertNotIn("format('GLaDOS_ACCOUNT_{0}'", workflow)

    def test_extracts_live_plan_dict_and_array(self):
        self.assertEqual(
            extract_live_plans({'plans': {'plan500': {'points': 500, 'days': 100}}}),
            {'plan500': (500, 100)},
        )
        self.assertEqual(
            extract_live_plans({'plans': [{'id': 'plan800', 'points': '800', 'days': 200}]}),
            {'plan800': (800, 200)},
        )
        self.assertIsNone(extract_live_plans({'points': 10}))
        self.assertEqual(extract_live_plans({'plans': 'unexpected'}), {})

    def test_live_intersection_is_exact_and_fail_closed(self):
        trusted = [
            ExchangePlan('plan500', 500, 100),
            ExchangePlan('plan800', 800, 200),
        ]
        self.assertEqual(
            [p.plan_id for p in trusted_live_intersection(trusted, {'plan500': (500, 100), 'plan800': (900, 200)})],
            ['plan500'],
        )
        self.assertEqual(trusted_live_intersection(trusted, {}), [])
        self.assertEqual([p.plan_id for p in trusted_live_intersection(trusted, None)], ['plan500', 'plan800'])


if __name__ == '__main__':
    unittest.main()
