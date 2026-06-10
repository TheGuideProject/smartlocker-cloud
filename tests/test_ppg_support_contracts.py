from pathlib import Path
from types import SimpleNamespace
import unittest

from app.web import admin


CLOUD_ROOT = Path(__file__).resolve().parents[1]


class PpgSupportContractTest(unittest.TestCase):
    def test_support_request_context_links_ticket_to_client_and_vessel(self):
        context_for_request = getattr(admin, "_support_request_client_context", None)
        company = SimpleNamespace(id="company-123", name="Med Toscana")
        fleet = SimpleNamespace(company=company)
        vessel = SimpleNamespace(name="Vessel One", fleet=fleet)
        device = SimpleNamespace(name="Locker Bridge", vessel=vessel)
        support_request = SimpleNamespace(device=device, device_id="locker-1")

        self.assertIsNotNone(context_for_request)
        self.assertEqual(
            context_for_request(support_request),
            {
                "company_name": "Med Toscana",
                "vessel_name": "Vessel One",
                "device_label": "Locker Bridge",
                "client_href": "/client/?company_id=company-123",
            },
        )

    def test_support_request_context_handles_missing_relationships(self):
        context_for_request = getattr(admin, "_support_request_client_context", None)
        support_request = SimpleNamespace(device=None, device_id="locker-1")

        self.assertIsNotNone(context_for_request)
        self.assertEqual(
            context_for_request(support_request),
            {
                "company_name": "Unknown client",
                "vessel_name": "Unknown vessel",
                "device_label": "locker-1",
                "client_href": None,
            },
        )

    def test_ppg_support_template_shows_client_context(self):
        template = (CLOUD_ROOT / "app" / "web" / "templates" / "admin" / "support_requests.html").read_text(encoding="utf-8")

        self.assertIn("Client / Vessel", template)
        self.assertIn("client_context.company_name", template)
        self.assertIn("client_context.vessel_name", template)
        self.assertIn("Open client preview", template)

    def test_support_route_eager_loads_client_context(self):
        source = (CLOUD_ROOT / "app" / "web" / "admin.py").read_text(encoding="utf-8")

        self.assertIn("selectinload(LockerDevice.vessel)", source)
        self.assertIn("selectinload(Vessel.fleet)", source)
        self.assertIn("selectinload(Fleet.company)", source)
        self.assertIn("_support_request_client_context", source)


if __name__ == "__main__":
    unittest.main()
