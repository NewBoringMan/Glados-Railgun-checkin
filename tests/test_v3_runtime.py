import pathlib
import unittest


class RuntimeSafetyTests(unittest.TestCase):
    def test_runtime_does_not_print_sensitive_fields(self):
        text = pathlib.Path('v3_runtime.py').read_text(encoding='utf-8')
        self.assertNotIn('result.email', text)
        self.assertNotIn('points_total', text)
        self.assertNotIn('cookie=', text.lower())
        self.assertNotIn('"account_key": result.account_key', text)
        self.assertNotIn('"account_key": account_key', text)
        self.assertIn('already-confirmed', text)

    def test_workflow_uses_dynamic_secret_name_but_not_in_logs(self):
        workflow = pathlib.Path('.github/workflows/v3Checkin.yml').read_text(encoding='utf-8')
        self.assertIn('secrets[matrix.secret_name]', workflow)
        self.assertIn('max-parallel: 1', workflow)
        self.assertNotIn("format('GLaDOS_ACCOUNT_{0}'", workflow)


if __name__ == '__main__':
    unittest.main()
