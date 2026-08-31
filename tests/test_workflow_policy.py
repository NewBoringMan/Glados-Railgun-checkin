import json
import unittest
from pathlib import Path


class RepositoryPolicyTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]

    def test_policy_is_not_coupled_to_generated_account_workflow(self):
        workflow = (self.root / ".github" / "workflows" / "gladosAccounts.yml").read_text(encoding="utf-8")
        self.assertNotIn("GLADOS_ACCOUNT_POLICIES", workflow)

    def test_policy_file_only_references_verified_plans(self):
        policies = json.loads((self.root / ".github" / "glados" / "account_policies.json").read_text(encoding="utf-8"))
        catalog = json.loads((self.root / ".github" / "glados" / "exchange_plans.json").read_text(encoding="utf-8"))

        self.assertEqual(policies.get("version"), 1)
        self.assertEqual(policies.get("default"), "auto")
        self.assertIsInstance(policies.get("accounts"), dict)

        verified_plans = {item["id"] for item in catalog["plans"] if item.get("verified")}
        for key, policy in policies["accounts"].items():
            self.assertTrue(key.strip())
            self.assertIn(policy, verified_plans | {"auto"})


if __name__ == "__main__":
    unittest.main()
