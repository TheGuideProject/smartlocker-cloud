from pathlib import Path
import unittest


TEMPLATE_ROOT = Path("app/web/templates")


class ClientTemplateContractTest(unittest.TestCase):
    def test_client_pages_use_shared_navigation_partial(self):
        for template_name in ["dashboard.html", "vessel_detail.html", "support.html", "activity.html"]:
            template = (TEMPLATE_ROOT / "owner" / template_name).read_text(encoding="utf-8")

            self.assertIn('{% include "owner/_client_nav.html" %}', template)

    def test_client_list_pages_use_company_selector_partial(self):
        for template_name in ["dashboard.html", "support.html", "activity.html"]:
            with self.subTest(template=template_name):
                template = (TEMPLATE_ROOT / "owner" / template_name).read_text(encoding="utf-8")
                self.assertIn('{% include "owner/_company_selector.html" %}', template)

    def test_client_list_pages_use_scope_summary_partial(self):
        for template_name in ["dashboard.html", "support.html", "activity.html"]:
            with self.subTest(template=template_name):
                template = (TEMPLATE_ROOT / "owner" / template_name).read_text(encoding="utf-8")
                self.assertIn('{% include "owner/_scope_summary.html" %}', template)

    def test_scope_summary_partial_uses_client_scope_context(self):
        partial = (TEMPLATE_ROOT / "owner" / "_scope_summary.html").read_text(encoding="utf-8")

        self.assertIn("client_scope.title", partial)
        self.assertIn("client_scope.detail", partial)
        self.assertIn("client_scope.badge", partial)

    def test_company_selector_is_ppg_only_and_uses_company_id(self):
        partial = (TEMPLATE_ROOT / "owner" / "_company_selector.html").read_text(encoding="utf-8")

        self.assertIn("{% if is_ppg_staff %}", partial)
        self.assertIn('method="get"', partial)
        self.assertIn('name="company_id"', partial)
        self.assertIn('value=""', partial)
        self.assertIn("company_selector_options", partial)

    def test_client_support_page_has_client_only_request_form(self):
        template = (TEMPLATE_ROOT / "owner" / "support.html").read_text(encoding="utf-8")

        self.assertIn("{% if not is_ppg_staff %}", template)
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
        template = (TEMPLATE_ROOT / "owner" / "support.html").read_text(encoding="utf-8")

        self.assertIn('"success": request.query_params.get("success")', source)
        self.assertIn('"error": request.query_params.get("error")', source)
        self.assertIn("{% if success %}", template)
        self.assertIn("{% if error %}", template)
        self.assertIn("{{ success }}", template)
        self.assertIn("{{ error }}", template)

    def test_shared_client_navigation_links_core_client_pages(self):
        nav = (TEMPLATE_ROOT / "owner" / "_client_nav.html").read_text(encoding="utf-8")

        self.assertIn('href="/client/', nav)
        self.assertIn('href="/client/activity', nav)
        self.assertIn('href="/client/support', nav)
        self.assertIn('href="/client/logout"', nav)

    def test_client_navigation_preserves_company_scope(self):
        nav = (TEMPLATE_ROOT / "owner" / "_client_nav.html").read_text(encoding="utf-8")
        company_scope = "{% if company_id %}?company_id={{ company_id }}{% endif %}"

        self.assertIn(f'href="/client/{company_scope}"', nav)
        self.assertIn(f'href="/client/activity{company_scope}"', nav)
        self.assertIn(f'href="/client/support{company_scope}"', nav)

    def test_client_dashboard_links_preserve_company_scope(self):
        dashboard = (TEMPLATE_ROOT / "owner" / "dashboard.html").read_text(encoding="utf-8")
        company_scope = "{% if company_id %}?company_id={{ company_id }}{% endif %}"

        self.assertIn(f'href="/client/activity{company_scope}"', dashboard)
        self.assertIn(f'href="/client/support{company_scope}"', dashboard)

    def test_client_activity_links_preserve_company_scope(self):
        activity = (TEMPLATE_ROOT / "owner" / "activity.html").read_text(encoding="utf-8")
        company_scope = "{% if company_id %}?company_id={{ company_id }}{% endif %}"

        self.assertIn(f'href="/client/{company_scope}"', activity)

    def test_client_detail_breadcrumbs_preserve_company_scope(self):
        company_scope = "{% if company_id %}?company_id={{ company_id }}{% endif %}"

        for template_name in ["support.html", "vessel_detail.html"]:
            with self.subTest(template=template_name):
                template = (TEMPLATE_ROOT / "owner" / template_name).read_text(encoding="utf-8")
                self.assertIn(f'href="/client/{company_scope}"', template)

    def test_client_activity_route_renders_activity_template(self):
        source = Path("app/web/dashboard.py").read_text(encoding="utf-8")

        self.assertIn('@router.get("/activity"', source)
        self.assertIn('"owner/activity.html"', source)

    def test_client_vessel_detail_passes_company_scope_to_template(self):
        source = Path("app/web/dashboard.py").read_text(encoding="utf-8")

        self.assertIn('"company_id": company_id', source)


if __name__ == "__main__":
    unittest.main()
