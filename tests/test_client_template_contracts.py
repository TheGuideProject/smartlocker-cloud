from pathlib import Path
import unittest


TEMPLATE_ROOT = Path("app/web/templates")


class ClientTemplateContractTest(unittest.TestCase):
    def test_client_pages_use_shared_navigation_partial(self):
        for template_name in ["dashboard.html", "vessel_detail.html", "support.html", "activity.html"]:
            template = (TEMPLATE_ROOT / "client" / template_name).read_text(encoding="utf-8")

            self.assertIn('{% include "client/_client_nav.html" %}', template)

    def test_client_pages_have_no_company_selector(self):
        for template_name in ["dashboard.html", "support.html", "activity.html"]:
            with self.subTest(template=template_name):
                template = (TEMPLATE_ROOT / "client" / template_name).read_text(encoding="utf-8")
                self.assertNotIn("_company_selector", template)

    def test_client_list_pages_use_scope_summary_partial(self):
        for template_name in ["dashboard.html", "support.html", "activity.html"]:
            with self.subTest(template=template_name):
                template = (TEMPLATE_ROOT / "client" / template_name).read_text(encoding="utf-8")
                self.assertIn('{% include "client/_scope_summary.html" %}', template)

    def test_scope_summary_partial_uses_client_scope_context(self):
        partial = (TEMPLATE_ROOT / "client" / "_scope_summary.html").read_text(encoding="utf-8")

        self.assertIn("client_scope.title", partial)
        self.assertIn("client_scope.detail", partial)
        self.assertIn("client_scope.badge", partial)

    def test_company_selector_lives_in_the_admin_preview(self):
        self.assertFalse((TEMPLATE_ROOT / "client" / "_company_selector.html").exists())

        preview = (TEMPLATE_ROOT / "admin" / "client_preview.html").read_text(encoding="utf-8")
        self.assertIn('method="get"', preview)
        self.assertIn('name="company_id"', preview)
        self.assertIn('value=""', preview)
        self.assertIn("company_selector_options", preview)

    def test_client_support_page_has_the_request_form(self):
        template = (TEMPLATE_ROOT / "client" / "support.html").read_text(encoding="utf-8")

        self.assertNotIn("is_ppg_staff", template)
        self.assertIn('action="/client/support/create"', template)
        self.assertIn('name="device_id"', template)
        self.assertIn('name="error_title"', template)
        self.assertIn('name="severity"', template)
        self.assertIn('name="details"', template)

    def test_client_support_create_route_exists(self):
        source = Path("app/web/dashboard.py").read_text(encoding="utf-8")

        self.assertIn('@router.post("/support/create"', source)
        self.assertIn('SupportRequest(', source)
        self.assertIn('error_code="CLIENT"', source)

    def test_client_support_page_surfaces_redirect_messages(self):
        source = Path("app/web/dashboard.py").read_text(encoding="utf-8")
        template = (TEMPLATE_ROOT / "client" / "support.html").read_text(encoding="utf-8")

        self.assertIn('"success": request.query_params.get("success")', source)
        self.assertIn('"error": request.query_params.get("error")', source)
        self.assertIn("{% if success %}", template)
        self.assertIn("{% if error %}", template)
        self.assertIn("{{ success }}", template)
        self.assertIn("{{ error }}", template)

    def test_shared_client_navigation_links_core_client_pages(self):
        nav = (TEMPLATE_ROOT / "client" / "_client_nav.html").read_text(encoding="utf-8")

        self.assertIn('href="/client/', nav)
        self.assertIn('href="/client/activity', nav)
        self.assertIn('href="/client/support', nav)
        self.assertIn('href="/client/logout"', nav)

    def test_client_navigation_uses_plain_company_free_links(self):
        nav = (TEMPLATE_ROOT / "client" / "_client_nav.html").read_text(encoding="utf-8")

        self.assertIn('href="/client/"', nav)
        self.assertIn('href="/client/activity"', nav)
        self.assertIn('href="/client/support"', nav)
        self.assertNotIn("company_id", nav)

    def test_client_dashboard_uses_plain_company_free_links(self):
        dashboard = (TEMPLATE_ROOT / "client" / "dashboard.html").read_text(encoding="utf-8")

        self.assertIn('href="/client/activity"', dashboard)
        self.assertIn('href="/client/support"', dashboard)
        self.assertNotIn("company_id", dashboard)

    def test_client_dashboard_surfaces_redirect_messages(self):
        source = Path("app/web/dashboard.py").read_text(encoding="utf-8")
        start = source.index('TemplateResponse("client/dashboard.html"')
        end = source.index('"active": "client_dashboard"', start)
        dashboard_context = source[start:end]
        template = (TEMPLATE_ROOT / "client" / "dashboard.html").read_text(encoding="utf-8")

        self.assertIn('"success": request.query_params.get("success")', dashboard_context)
        self.assertIn('"error": request.query_params.get("error")', dashboard_context)
        self.assertIn("{% if success %}", template)
        self.assertIn("{% if error %}", template)
        self.assertIn("{{ success }}", template)
        self.assertIn("{{ error }}", template)

    def test_client_dashboard_renders_backend_quick_actions(self):
        source = Path("app/web/dashboard.py").read_text(encoding="utf-8")
        start = source.index('TemplateResponse("client/dashboard.html"')
        end = source.index('"active": "client_dashboard"', start)
        dashboard_context = source[start:end]
        dashboard = (TEMPLATE_ROOT / "client" / "dashboard.html").read_text(encoding="utf-8")

        self.assertIn('"quick_actions": _client_dashboard_quick_actions(', dashboard_context)
        self.assertIn("Next actions", dashboard)
        self.assertIn("{% for action in quick_actions %}", dashboard)
        self.assertIn("{{ action.label }}", dashboard)
        self.assertIn("{{ action.badge }}", dashboard)
        self.assertIn("{% if action.href %}", dashboard)

    def test_client_activity_links_back_to_the_fleet_overview(self):
        activity = (TEMPLATE_ROOT / "client" / "activity.html").read_text(encoding="utf-8")

        self.assertIn('href="/client/"', activity)
        self.assertNotIn("company_id", activity)

    def test_client_detail_breadcrumbs_link_back_without_company_scope(self):
        for template_name in ["support.html", "vessel_detail.html"]:
            with self.subTest(template=template_name):
                template = (TEMPLATE_ROOT / "client" / template_name).read_text(encoding="utf-8")
                self.assertIn('href="/client/"', template)
                self.assertNotIn("company_id", template)

    def test_client_activity_route_renders_activity_template(self):
        source = Path("app/web/dashboard.py").read_text(encoding="utf-8")

        self.assertIn('@router.get("/activity"', source)
        self.assertIn('"client/activity.html"', source)

    def test_client_vessel_detail_checks_company_access(self):
        source = Path("app/web/dashboard.py").read_text(encoding="utf-8")

        self.assertIn("_client_can_access_company(current_user, vessel.fleet.company_id)", source)

    def test_client_vessel_detail_renders_inventory_status(self):
        source = Path("app/web/dashboard.py").read_text(encoding="utf-8")
        start = source.index('TemplateResponse("client/vessel_detail.html"')
        end = source.index('"products": inventory["products"]', start)
        detail_context = source[start:end]
        template = (TEMPLATE_ROOT / "client" / "vessel_detail.html").read_text(encoding="utf-8")

        self.assertIn('"inventory_status": _client_vessel_inventory_status(', detail_context)
        self.assertIn("inventory_status.title", template)
        self.assertIn("inventory_status.detail", template)
        self.assertIn("inventory_status.badge", template)

    def test_client_portal_does_not_render_from_owner_template_namespace(self):
        source = Path("app/web/dashboard.py").read_text(encoding="utf-8")

        self.assertNotIn('TemplateResponse("owner/', source)
        self.assertFalse((TEMPLATE_ROOT / "owner").exists())


if __name__ == "__main__":
    unittest.main()
