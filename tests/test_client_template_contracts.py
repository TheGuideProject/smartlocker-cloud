from pathlib import Path
import unittest


TEMPLATE_ROOT = Path("app/web/templates")


class ClientTemplateContractTest(unittest.TestCase):
    def test_client_pages_use_shared_navigation_partial(self):
        for template_name in ["dashboard.html", "vessel_detail.html", "support.html", "activity.html"]:
            template = (TEMPLATE_ROOT / "owner" / template_name).read_text(encoding="utf-8")

            self.assertIn('{% include "owner/_client_nav.html" %}', template)

    def test_shared_client_navigation_links_core_client_pages(self):
        nav = (TEMPLATE_ROOT / "owner" / "_client_nav.html").read_text(encoding="utf-8")

        self.assertIn('href="/client/"', nav)
        self.assertIn('href="/client/activity', nav)
        self.assertIn('href="/client/support"', nav)
        self.assertIn('href="/client/logout"', nav)

    def test_client_activity_route_renders_activity_template(self):
        source = Path("app/web/dashboard.py").read_text(encoding="utf-8")

        self.assertIn('@router.get("/activity"', source)
        self.assertIn('"owner/activity.html"', source)


if __name__ == "__main__":
    unittest.main()
