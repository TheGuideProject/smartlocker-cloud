import unittest
from types import SimpleNamespace

from app.web.auth_web import (
    _login_context_for_path,
    _login_path_for_request_path,
    _portal_home_for_role,
)
from app.web.dashboard import (
    _client_can_access_company,
    _client_dashboard_company_scope,
    _client_dashboard_uses_global_support_scope,
    _client_support_uses_global_scope,
    _support_request_stats,
)


class PlatformSplitContractTest(unittest.TestCase):
    def test_ppg_roles_land_in_ppg_admin_portal(self):
        self.assertEqual(_portal_home_for_role("ppg_admin"), "/admin/")
        self.assertEqual(_portal_home_for_role("ppg_support"), "/admin/")

    def test_client_roles_land_in_client_portal(self):
        self.assertEqual(_portal_home_for_role("ship_owner"), "/client/")
        self.assertEqual(_portal_home_for_role("crew"), "/client/")

    def test_client_routes_redirect_to_client_login_when_unauthenticated(self):
        self.assertEqual(_login_path_for_request_path("/client/"), "/client/login")
        self.assertEqual(_login_path_for_request_path("/client/vessels/abc"), "/client/login")

    def test_admin_routes_redirect_to_admin_login_when_unauthenticated(self):
        self.assertEqual(_login_path_for_request_path("/admin/"), "/admin/login")
        self.assertEqual(_login_path_for_request_path("/admin/devices"), "/admin/login")

    def test_client_login_context_has_client_branding_and_action(self):
        context = _login_context_for_path("/client/login")

        self.assertEqual(context["badge"], "Client")
        self.assertEqual(context["form_action"], "/client/login")
        self.assertEqual(context["email_placeholder"], "user@client.com")
        self.assertEqual(context["switch_href"], "/admin/login")

    def test_admin_login_context_has_ppg_branding_and_action(self):
        context = _login_context_for_path("/admin/login")

        self.assertEqual(context["badge"], "PPG")
        self.assertEqual(context["form_action"], "/admin/login")
        self.assertEqual(context["email_placeholder"], "admin@ppg.com")
        self.assertEqual(context["switch_href"], "/client/login")

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

    def test_client_support_global_scope_is_ppg_only(self):
        self.assertTrue(_client_support_uses_global_scope(is_ppg_staff=True, scoped_company_id=None))
        self.assertFalse(_client_support_uses_global_scope(is_ppg_staff=True, scoped_company_id="company-client"))
        self.assertFalse(_client_support_uses_global_scope(is_ppg_staff=False, scoped_company_id="company-client"))

    def test_support_request_stats_count_open_and_resolved(self):
        requests = [
            SimpleNamespace(status="open"),
            SimpleNamespace(status="in_progress"),
            SimpleNamespace(status="resolved"),
            SimpleNamespace(status="closed"),
        ]

        self.assertEqual(
            _support_request_stats(requests),
            {"total": 4, "open": 2, "resolved": 2},
        )

    def test_client_can_access_only_own_company(self):
        user = SimpleNamespace(role="ship_owner", company_id="company-client")

        self.assertTrue(_client_can_access_company(user, "company-client"))
        self.assertFalse(_client_can_access_company(user, "company-other"))
        self.assertFalse(_client_can_access_company(user, None))

    def test_ppg_staff_can_access_any_client_company(self):
        user = SimpleNamespace(role="ppg_support", company_id=None)

        self.assertTrue(_client_can_access_company(user, "company-client"))
        self.assertTrue(_client_can_access_company(user, None))


if __name__ == "__main__":
    unittest.main()
