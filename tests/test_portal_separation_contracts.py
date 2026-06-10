"""Contracts for the hard PPG/Client portal separation.

Design:
- /admin/*  -> PPG staff only (ppg_admin, ppg_support).
- /client/* -> client roles only (ship_owner, crew); PPG staff are redirected
  back to /admin/ instead of browsing the client portal directly.
- PPG previews client data from inside the internal portal at
  /admin/client-preview (optionally scoped with ?company_id=...).
- The client portal always scopes data to the user's own company and never
  honours a company_id passed via query string.
"""

from pathlib import Path
from types import SimpleNamespace
import unittest

from app.web import auth_web
from app.web.auth_web import _portal_home_for_role
from app.web.dashboard import _client_dashboard_company_scope


CLOUD_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_ROOT = CLOUD_ROOT / "app" / "web" / "templates"


class ClientSessionRoleContractTest(unittest.TestCase):
    """require_client_session must not accept PPG roles."""

    def test_client_roles_may_use_the_client_portal(self):
        redirect_for_role = getattr(auth_web, "_client_portal_redirect_for_role", None)

        self.assertIsNotNone(redirect_for_role)
        self.assertIsNone(redirect_for_role("ship_owner"))
        self.assertIsNone(redirect_for_role("crew"))

    def test_ppg_roles_are_redirected_to_the_ppg_portal(self):
        redirect_for_role = getattr(auth_web, "_client_portal_redirect_for_role", None)

        self.assertIsNotNone(redirect_for_role)
        self.assertEqual(redirect_for_role("ppg_admin"), "/admin/")
        self.assertEqual(redirect_for_role("ppg_support"), "/admin/")

    def test_unknown_roles_are_sent_to_a_login_page(self):
        redirect_for_role = getattr(auth_web, "_client_portal_redirect_for_role", None)

        self.assertIsNotNone(redirect_for_role)
        self.assertEqual(redirect_for_role("legacy_role"), "/admin/login")
        self.assertEqual(redirect_for_role(None), "/admin/login")

    def test_require_client_session_uses_the_role_redirect_contract(self):
        source = (CLOUD_ROOT / "app" / "web" / "auth_web.py").read_text(encoding="utf-8")
        start = source.index("async def require_client_session")
        end = source.index("# ======", start)
        body = source[start:end]

        self.assertIn("_client_portal_redirect_for_role", body)
        self.assertNotIn("PPG_WEB_ROLES | CLIENT_WEB_ROLES", body)

    def test_ppg_home_is_still_the_admin_portal(self):
        self.assertEqual(_portal_home_for_role("ppg_admin"), "/admin/")
        self.assertEqual(_portal_home_for_role("ship_owner"), "/client/")


class ClientCompanyScopeContractTest(unittest.TestCase):
    """The client portal always filters on the user's own company."""

    def test_client_roles_never_get_a_requested_company(self):
        for role in ("ship_owner", "crew"):
            user = SimpleNamespace(role=role, company_id="company-client")
            self.assertEqual(
                _client_dashboard_company_scope(user, requested_company_id="company-other"),
                "company-client",
            )

    def test_requested_company_id_is_always_ignored(self):
        ppg_user = SimpleNamespace(role="ppg_admin", company_id=None)

        self.assertIsNone(
            _client_dashboard_company_scope(ppg_user, requested_company_id="company-client")
        )

    def test_client_portal_routes_have_no_ppg_branches(self):
        source = (CLOUD_ROOT / "app" / "web" / "dashboard.py").read_text(encoding="utf-8")

        self.assertNotIn("PPG_WEB_ROLES", source)
        self.assertNotIn("is_ppg_staff", source)
        self.assertNotIn("All companies", source)


class ClientNavigationContractTest(unittest.TestCase):
    """The client portal navigation never links into the PPG portal."""

    def test_client_nav_has_no_ppg_or_api_links(self):
        nav = (TEMPLATE_ROOT / "client" / "_client_nav.html").read_text(encoding="utf-8")

        self.assertNotIn('href="/admin', nav)
        self.assertNotIn('href="/docs"', nav)
        self.assertNotIn("PPG Portal", nav)
        self.assertNotIn("API Docs", nav)
        self.assertNotIn("is_ppg_staff", nav)

    def test_client_nav_keeps_core_client_links(self):
        nav = (TEMPLATE_ROOT / "client" / "_client_nav.html").read_text(encoding="utf-8")

        self.assertIn('href="/client/"', nav)
        self.assertIn('href="/client/activity"', nav)
        self.assertIn('href="/client/support"', nav)
        self.assertIn('href="/client/logout"', nav)

    def test_client_templates_do_not_propagate_company_id(self):
        for template_name in [
            "_client_nav.html",
            "dashboard.html",
            "support.html",
            "activity.html",
            "vessel_detail.html",
        ]:
            with self.subTest(template=template_name):
                template = (TEMPLATE_ROOT / "client" / template_name).read_text(encoding="utf-8")
                self.assertNotIn("company_id", template)

    def test_client_templates_have_no_company_selector(self):
        self.assertFalse((TEMPLATE_ROOT / "client" / "_company_selector.html").exists())

        for template_name in ["dashboard.html", "support.html", "activity.html"]:
            with self.subTest(template=template_name):
                template = (TEMPLATE_ROOT / "client" / template_name).read_text(encoding="utf-8")
                self.assertNotIn("_company_selector", template)


class AdminClientPreviewContractTest(unittest.TestCase):
    """PPG previews client data from inside the internal portal."""

    def test_preview_module_is_admin_protected(self):
        source = (CLOUD_ROOT / "app" / "web" / "client_preview.py").read_text(encoding="utf-8")

        self.assertIn('prefix="/admin/client-preview"', source)
        self.assertIn("require_admin_session", source)
        self.assertIn('"admin/client_preview.html"', source)

    def test_preview_router_is_mounted_in_the_app(self):
        source = (CLOUD_ROOT / "app" / "main.py").read_text(encoding="utf-8")

        self.assertIn("client_preview", source)

    def test_preview_scope_summary_describes_global_and_company_scope(self):
        from app.web import client_preview

        scope_summary = getattr(client_preview, "_preview_scope_summary", None)
        options = [
            {"id": "", "name": "All companies", "selected": False},
            {"id": "company-a", "name": "Alpha Marine", "selected": True},
        ]

        self.assertIsNotNone(scope_summary)
        self.assertEqual(
            scope_summary(scoped_company_id=None, selector_options=options),
            {
                "title": "PPG preview",
                "detail": "Showing all client companies.",
                "badge": "preview",
            },
        )
        self.assertEqual(
            scope_summary(scoped_company_id="company-a", selector_options=options),
            {
                "title": "PPG preview",
                "detail": "Showing Alpha Marine only.",
                "badge": "preview",
            },
        )

    def test_preview_company_selector_lists_all_companies(self):
        from app.web import client_preview

        selector_options = getattr(client_preview, "_preview_company_selector_options", None)
        companies = [
            SimpleNamespace(id="company-a", name="Alpha Marine"),
            SimpleNamespace(id="company-b", name="Beta Shipping"),
        ]

        self.assertIsNotNone(selector_options)
        options = selector_options(companies, scoped_company_id="company-b")

        self.assertEqual(options[0], {"id": "", "name": "All companies", "selected": False})
        self.assertEqual(options[1], {"id": "company-a", "name": "Alpha Marine", "selected": False})
        self.assertEqual(options[2], {"id": "company-b", "name": "Beta Shipping", "selected": True})

    def test_preview_global_scope_is_only_for_unscoped_previews(self):
        from app.web import client_preview

        support_scope = getattr(client_preview, "_preview_uses_global_support_scope", None)
        list_scope = getattr(client_preview, "_preview_uses_global_scope", None)

        self.assertIsNotNone(support_scope)
        self.assertIsNotNone(list_scope)
        self.assertTrue(support_scope(scoped_company_id=None, device_ids=[]))
        self.assertFalse(support_scope(scoped_company_id="company-a", device_ids=[]))
        self.assertTrue(list_scope(scoped_company_id=None))
        self.assertFalse(list_scope(scoped_company_id="company-a"))

    def test_preview_template_lives_in_the_admin_namespace(self):
        template_path = TEMPLATE_ROOT / "admin" / "client_preview.html"

        self.assertTrue(template_path.exists())
        template = template_path.read_text(encoding="utf-8")
        self.assertIn('action="/admin/client-preview"', template)
        self.assertIn('name="company_id"', template)
        self.assertNotIn('href="/client/', template)

    def test_ppg_dashboard_quick_action_points_to_admin_preview(self):
        from app.web.admin import _ppg_dashboard_quick_actions

        actions = _ppg_dashboard_quick_actions(open_support_count=0, offline_device_count=0)
        hrefs = [action["href"] for action in actions]

        self.assertIn("/admin/client-preview", hrefs)
        self.assertNotIn("/client/", hrefs)

    def test_support_ticket_context_links_to_admin_preview(self):
        from app.web.admin import _support_request_client_context

        company = SimpleNamespace(id="company-123", name="Med Toscana")
        fleet = SimpleNamespace(company=company)
        vessel = SimpleNamespace(name="Vessel One", fleet=fleet)
        device = SimpleNamespace(name="Locker Bridge", vessel=vessel)
        support_request = SimpleNamespace(device=device, device_id="locker-1")

        context = _support_request_client_context(support_request)

        self.assertEqual(context["client_href"], "/admin/client-preview?company_id=company-123")


class ExternalClientPortalContractTest(unittest.TestCase):
    """When the standalone client portal is deployed, /client/* hops over."""

    def test_no_redirect_without_a_configured_portal_url(self):
        import app.main as main

        redirect_for_path = getattr(main, "_external_client_portal_redirect", None)

        self.assertIsNotNone(redirect_for_path)
        self.assertIsNone(redirect_for_path("/client/", "", ""))
        self.assertIsNone(redirect_for_path("/admin/", "", "https://portal.example.com"))

    def test_client_paths_redirect_to_the_external_portal(self):
        import app.main as main

        redirect_for_path = getattr(main, "_external_client_portal_redirect", None)

        self.assertIsNotNone(redirect_for_path)
        self.assertEqual(
            redirect_for_path("/client/", "", "https://portal.example.com/"),
            "https://portal.example.com/client/",
        )
        self.assertEqual(
            redirect_for_path("/client/support", "a=1", "https://portal.example.com"),
            "https://portal.example.com/client/support?a=1",
        )
        self.assertEqual(
            redirect_for_path("/dashboard/", "", "https://portal.example.com"),
            "https://portal.example.com/dashboard/",
        )


if __name__ == "__main__":
    unittest.main()
