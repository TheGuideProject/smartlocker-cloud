import unittest
from types import SimpleNamespace

from app.web.auth_web import (
    _login_context_for_path,
    _login_path_for_request_path,
    _portal_home_for_role,
)
from app.web import dashboard
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

    def test_client_company_selector_has_all_companies_and_selected_company(self):
        selector_options = getattr(dashboard, "_client_company_selector_options", None)
        companies = [
            SimpleNamespace(id="company-a", name="Alpha Marine"),
            SimpleNamespace(id="company-b", name="Beta Shipping"),
        ]

        self.assertIsNotNone(selector_options)
        options = selector_options(companies, scoped_company_id="company-b")

        self.assertEqual(options[0], {"id": "", "name": "All companies", "selected": False})
        self.assertEqual(options[1], {"id": "company-a", "name": "Alpha Marine", "selected": False})
        self.assertEqual(options[2], {"id": "company-b", "name": "Beta Shipping", "selected": True})

    def test_client_scope_summary_describes_client_and_ppg_preview(self):
        scope_summary = getattr(dashboard, "_client_scope_summary", None)
        options = [
            {"id": "", "name": "All companies", "selected": False},
            {"id": "company-a", "name": "Alpha Marine", "selected": True},
        ]

        self.assertIsNotNone(scope_summary)
        self.assertEqual(
            scope_summary(is_ppg_staff=False, scoped_company_id="company-a", selector_options=[]),
            {
                "title": "Client view",
                "detail": "Showing only vessels linked to your company.",
                "badge": "client",
            },
        )
        self.assertEqual(
            scope_summary(is_ppg_staff=True, scoped_company_id=None, selector_options=options),
            {
                "title": "PPG preview",
                "detail": "Showing all client companies.",
                "badge": "preview",
            },
        )
        self.assertEqual(
            scope_summary(is_ppg_staff=True, scoped_company_id="company-a", selector_options=options),
            {
                "title": "PPG preview",
                "detail": "Showing Alpha Marine only.",
                "badge": "preview",
            },
        )

    def test_client_dashboard_quick_actions_preserve_company_scope(self):
        quick_actions = getattr(dashboard, "_client_dashboard_quick_actions", None)
        vessels = [SimpleNamespace(id="vessel-1")]
        support_requests = [SimpleNamespace(status="open")]

        self.assertIsNotNone(quick_actions)
        actions = quick_actions(
            vessels=vessels,
            support_requests=support_requests,
            scoped_company_id="company-client",
        )

        self.assertEqual(actions[0]["label"], "Open first vessel")
        self.assertEqual(actions[0]["href"], "/client/vessels/vessel-1?company_id=company-client")
        self.assertEqual(actions[1]["label"], "Support")
        self.assertEqual(actions[1]["href"], "/client/support?company_id=company-client")
        self.assertEqual(actions[1]["badge"], "1 open")
        self.assertEqual(actions[2]["label"], "Activity")
        self.assertEqual(actions[2]["href"], "/client/activity?company_id=company-client")

    def test_client_dashboard_quick_actions_cover_empty_fleet(self):
        quick_actions = getattr(dashboard, "_client_dashboard_quick_actions", None)

        self.assertIsNotNone(quick_actions)
        actions = quick_actions(vessels=[], support_requests=[], scoped_company_id=None)

        self.assertEqual(actions[0]["label"], "No vessels yet")
        self.assertEqual(actions[0]["href"], "")
        self.assertEqual(actions[0]["badge"], "setup")
        self.assertEqual(actions[1]["badge"], "ready")

    def test_client_vessel_inventory_status_explains_empty_and_ready_states(self):
        inventory_status = getattr(dashboard, "_client_vessel_inventory_status", None)

        self.assertIsNotNone(inventory_status)
        self.assertEqual(
            inventory_status(devices=[], products=[]),
            {
                "title": "SmartLocker not installed",
                "detail": "PPG must assign a SmartLocker before live stock can appear for this vessel.",
                "badge": "setup",
                "tone": "warning",
            },
        )
        self.assertEqual(
            inventory_status(
                devices=[SimpleNamespace(is_online=True)],
                products=[{"name": "SIGMACOVER 280"}],
            ),
            {
                "title": "Inventory visible",
                "detail": "Stock combines SmartLocker reports with PPG inventory adjustments.",
                "badge": "1 product",
                "tone": "ready",
            },
        )
        self.assertEqual(
            inventory_status(
                devices=[SimpleNamespace(is_online=False)],
                products=[],
            ),
            {
                "title": "Waiting for stock",
                "detail": "A SmartLocker is installed, but no stock is visible yet. PPG can add stock or wait for the next device sync.",
                "badge": "empty",
                "tone": "warning",
            },
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

    def test_client_activity_global_scope_is_ppg_only(self):
        uses_global_scope = getattr(dashboard, "_client_activity_uses_global_scope", None)

        self.assertIsNotNone(uses_global_scope)
        self.assertTrue(uses_global_scope(is_ppg_staff=True, scoped_company_id=None))
        self.assertFalse(uses_global_scope(is_ppg_staff=True, scoped_company_id="company-client"))
        self.assertFalse(uses_global_scope(is_ppg_staff=False, scoped_company_id="company-client"))

    def test_client_activity_stats_count_events_and_devices(self):
        activity_event_stats = getattr(dashboard, "_client_activity_event_stats", None)
        events = [
            SimpleNamespace(device_id="device-a", event_type="scan"),
            SimpleNamespace(device_id="device-a", event_type="mixing_started"),
            SimpleNamespace(device_id="device-b", event_type="scan"),
        ]

        self.assertIsNotNone(activity_event_stats)
        self.assertEqual(
            activity_event_stats(events),
            {"total": 3, "devices": 2, "types": 2},
        )

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

    def test_client_support_request_validation_requires_known_device_and_title(self):
        request_error = getattr(dashboard, "_client_support_request_error", None)
        allowed_devices = {"locker-1", "locker-2"}

        self.assertIsNotNone(request_error)
        self.assertEqual(
            request_error("", "Scale problem", allowed_devices),
            "Select a SmartLocker device",
        )
        self.assertEqual(
            request_error("locker-x", "Scale problem", allowed_devices),
            "Device is not available for this client",
        )
        self.assertEqual(
            request_error("locker-1", "", allowed_devices),
            "Describe the support request",
        )
        self.assertIsNone(
            request_error("locker-1", "Scale problem", allowed_devices),
        )

    def test_client_support_request_severity_defaults_to_warning(self):
        request_severity = getattr(dashboard, "_client_support_request_severity", None)

        self.assertIsNotNone(request_severity)
        self.assertEqual(request_severity("critical"), "critical")
        self.assertEqual(request_severity("info"), "info")
        self.assertEqual(request_severity("unknown"), "warning")
        self.assertEqual(request_severity(""), "warning")

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
