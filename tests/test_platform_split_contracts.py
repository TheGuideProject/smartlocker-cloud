import unittest
from types import SimpleNamespace

from app.web.auth_web import _portal_home_for_role
from app.web.dashboard import (
    _client_dashboard_company_scope,
    _client_dashboard_uses_global_support_scope,
)


class PlatformSplitContractTest(unittest.TestCase):
    def test_ppg_roles_land_in_ppg_admin_portal(self):
        self.assertEqual(_portal_home_for_role("ppg_admin"), "/admin/")
        self.assertEqual(_portal_home_for_role("ppg_support"), "/admin/")

    def test_client_roles_land_in_client_portal(self):
        self.assertEqual(_portal_home_for_role("ship_owner"), "/client/")
        self.assertEqual(_portal_home_for_role("crew"), "/client/")

    def test_ship_owner_dashboard_is_scoped_to_own_company(self):
        user = SimpleNamespace(role="ship_owner", company_id="company-client")

        self.assertEqual(
            _client_dashboard_company_scope(user, requested_company_id="company-other"),
            "company-client",
        )

    def test_ppg_dashboard_preview_can_choose_company_scope(self):
        user = SimpleNamespace(role="ppg_admin", company_id=None)

        self.assertEqual(
            _client_dashboard_company_scope(user, requested_company_id="company-client"),
            "company-client",
        )

    def test_ppg_preview_for_empty_company_does_not_show_global_support(self):
        self.assertFalse(
            _client_dashboard_uses_global_support_scope(
                is_ppg_staff=True,
                scoped_company_id="company-client",
                device_ids=[],
            )
        )

    def test_ppg_global_client_preview_can_show_global_support(self):
        self.assertTrue(
            _client_dashboard_uses_global_support_scope(
                is_ppg_staff=True,
                scoped_company_id=None,
                device_ids=[],
            )
        )


if __name__ == "__main__":
    unittest.main()
