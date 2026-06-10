from pathlib import Path
import unittest

import app.main as main


CLOUD_ROOT = Path(__file__).resolve().parents[1]


class PortalEntryContractTest(unittest.TestCase):
    def test_unauthenticated_root_renders_portal_selector(self):
        destination_for_role = getattr(main, "_root_portal_destination", None)

        self.assertIsNotNone(destination_for_role)
        self.assertIsNone(destination_for_role(None))
        self.assertIsNone(destination_for_role(""))

    def test_authenticated_root_still_redirects_by_role(self):
        destination_for_role = getattr(main, "_root_portal_destination", None)

        self.assertIsNotNone(destination_for_role)
        self.assertEqual(destination_for_role("ppg_admin"), "/admin/")
        self.assertEqual(destination_for_role("ppg_support"), "/admin/")
        self.assertEqual(destination_for_role("ship_owner"), "/client/")
        self.assertEqual(destination_for_role("crew"), "/client/")

    def test_portal_options_link_ppg_and_client_logins(self):
        portal_options = getattr(main, "_portal_entry_options", None)

        self.assertIsNotNone(portal_options)
        self.assertEqual(
            portal_options(),
            [
                {
                    "label": "PPG Portal",
                    "href": "/admin/login",
                    "badge": "PPG staff",
                    "detail": "Manage companies, vessels, devices, catalog, barcodes, inventory, and support.",
                },
                {
                    "label": "Client Portal",
                    "href": "/client/login",
                    "badge": "Client access",
                    "detail": "Review vessel stock, SmartLocker status, activity, and support requests.",
                },
            ],
        )

    def test_root_route_uses_portal_selector_template(self):
        source = (CLOUD_ROOT / "app" / "main.py").read_text(encoding="utf-8")

        self.assertIn("_root_portal_destination", source)
        self.assertIn('TemplateResponse("portal_select.html"', source)
        self.assertIn("_portal_entry_options()", source)

    def test_portal_selector_template_has_two_distinct_entries(self):
        template = (CLOUD_ROOT / "app" / "web" / "templates" / "portal_select.html").read_text(encoding="utf-8")

        self.assertIn("{% for portal in portal_options %}", template)
        self.assertIn('href="{{ portal.href }}"', template)
        self.assertIn("{{ portal.badge }}", template)
        self.assertIn("{{ portal.label }}", template)
        self.assertIn("{{ portal.detail }}", template)
        self.assertIn('Open {{ portal.label }}', template)
        self.assertNotIn("portal_options[0]", template)
        self.assertNotIn("portal_options[1]", template)
        self.assertIn("SmartLocker", template)

    def test_navigation_uses_ppg_portal_label_for_staff_workspace(self):
        base_template = (CLOUD_ROOT / "app" / "web" / "templates" / "base.html").read_text(encoding="utf-8")
        client_nav = (CLOUD_ROOT / "app" / "web" / "templates" / "client" / "_client_nav.html").read_text(encoding="utf-8")

        self.assertIn('<span class="nav-icon">&#9671;</span> PPG Portal</a>', base_template)
        self.assertIn('<span class="nav-icon">&#9671;</span> PPG Portal</a>', client_nav)
        self.assertNotIn('<span class="nav-icon">&#9671;</span> Admin Portal</a>', client_nav)

    def test_top_bar_shows_workspace_badge(self):
        base_template = (CLOUD_ROOT / "app" / "web" / "templates" / "base.html").read_text(encoding="utf-8")
        stylesheet = (CLOUD_ROOT / "app" / "static" / "style.css").read_text(encoding="utf-8")

        self.assertIn("workspace_is_client", base_template)
        self.assertIn("workspace_label", base_template)
        self.assertIn("Client Portal", base_template)
        self.assertIn("PPG Portal", base_template)
        self.assertIn('class="workspace-chip', base_template)
        self.assertIn("{{ workspace_label }}", base_template)
        self.assertIn(".workspace-chip", stylesheet)
        self.assertIn(".workspace-chip.client", stylesheet)

    def test_sidebar_header_and_footer_are_workspace_aware(self):
        base_template = (CLOUD_ROOT / "app" / "web" / "templates" / "base.html").read_text(encoding="utf-8")

        self.assertIn("workspace_badge", base_template)
        self.assertIn("workspace_footer", base_template)
        self.assertIn("workspace_subfooter", base_template)
        self.assertIn("{{ workspace_badge }}", base_template)
        self.assertIn("{{ workspace_footer }}", base_template)
        self.assertIn("{{ workspace_subfooter }}", base_template)
        self.assertIn("Client workspace", base_template)
        self.assertIn("Marine Coatings", base_template)


if __name__ == "__main__":
    unittest.main()
