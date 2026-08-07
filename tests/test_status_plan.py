import unittest

from status import VIP_PLAN_NAMES, plan_from_status_data


class StatusPlanTests(unittest.TestCase):
    def test_current_vip_mapping(self):
        expected = {
            0: "Free",
            6: "Expired",
            7: "Reset",
            8: "Overlimit",
            9: "Spam",
            10: "Free",
            11: "Edu",
            21: "Basic",
            31: "Pro",
            41: "Team",
            51: "Enterprise",
        }
        self.assertEqual(VIP_PLAN_NAMES, expected)
        for level, name in expected.items():
            self.assertEqual(plan_from_status_data({"vip": level}), (name, level))

    def test_numeric_string_vip_is_supported(self):
        self.assertEqual(plan_from_status_data({"vip": "11"}), ("Edu", 11))

    def test_textual_plan_takes_priority(self):
        self.assertEqual(plan_from_status_data({"planName": "Education", "vip": 11}), ("Education", 11))

    def test_nested_user_vip_is_supported(self):
        self.assertEqual(plan_from_status_data({"user": {"vip": 31}}), ("Pro", 31))

    def test_unknown_vip_stays_visible(self):
        self.assertEqual(plan_from_status_data({"vip": 99}), ("VIP 99", 99))

    def test_missing_plan_data_remains_empty(self):
        self.assertEqual(plan_from_status_data({}), ("", None))


if __name__ == "__main__":
    unittest.main()
