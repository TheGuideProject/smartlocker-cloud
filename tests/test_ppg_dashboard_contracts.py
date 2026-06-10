import unittest

from app.web.admin import _ppg_dashboard_quick_actions


class PpgDashboardContractTest(unittest.TestCase):
    def test_support_and_offline_devices_are_prioritized(self):
        actions = _ppg_dashboard_quick_actions(
            open_support_count=3,
            offline_device_count=2,
        )

        self.assertEqual(actions[0]["label"], "Review support")
        self.assertEqual(actions[0]["href"], "/admin/support")
        self.assertEqual(actions[0]["detail"], "Resolve client/device support tickets.")
        self.assertEqual(actions[0]["badge"], "3 open")
        self.assertEqual(actions[1]["label"], "Check devices")
        self.assertEqual(actions[1]["href"], "/admin/devices")
        self.assertEqual(actions[1]["badge"], "2 offline")

    def test_client_preview_is_always_available_inside_the_ppg_portal(self):
        actions = _ppg_dashboard_quick_actions(
            open_support_count=0,
            offline_device_count=0,
        )

        self.assertTrue(any(action["href"] == "/admin/client-preview" for action in actions))
        client_preview = next(action for action in actions if action["href"] == "/admin/client-preview")
        self.assertEqual(client_preview["detail"], "Open the client-facing read-only platform.")
        self.assertFalse(any(action["href"].startswith("/client") for action in actions))

    def test_quick_actions_stay_small_enough_to_scan(self):
        actions = _ppg_dashboard_quick_actions(
            open_support_count=9,
            offline_device_count=4,
        )

        self.assertLessEqual(len(actions), 6)


if __name__ == "__main__":
    unittest.main()
