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
                    "badge": "Customer access",
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

        self.assertIn("PPG Portal", template)
        self.assertIn("Client Portal", template)
        self.assertIn('href="/admin/login"', template)
        self.assertIn('href="/client/login"', template)
        self.assertIn("SmartLocker", template)


if __name__ == "__main__":
    unittest.main()
