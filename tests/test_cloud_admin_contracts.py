import unittest
from pathlib import Path
from types import SimpleNamespace

from app.web import admin
from app.web.admin import (
    _apply_inventory_adjustment_summary,
    _barcode_payload_value,
)

CLOUD_ROOT = Path(__file__).resolve().parents[1]


class BarcodeAdminContractTest(unittest.TestCase):
    def test_generated_barcode_omits_batch_number(self):
        self.assertEqual(
            _barcode_payload_value(ppg_code="SC-280", color="", real_barcode=""),
            "SL_SC-280",
        )

    def test_generated_barcode_can_include_color_without_batch(self):
        self.assertEqual(
            _barcode_payload_value(
                ppg_code="SC-280",
                color="White Grey",
                real_barcode="",
            ),
            "SL_SC-280_WHITEGREY",
        )

    def test_real_barcode_is_preserved(self):
        self.assertEqual(
            _barcode_payload_value(
                ppg_code="SC-280",
                color="",
                real_barcode="8712345678901",
            ),
            "8712345678901",
        )

    def test_saved_barcodes_page_has_image_fallback_and_png_link(self):
        template = (CLOUD_ROOT / "app" / "web" / "templates" / "admin" / "barcodes_list.html").read_text(encoding="utf-8")

        self.assertIn('src="/admin/barcodes/{{ bc.id }}/image.png"', template)
        self.assertIn("onerror=", template)
        self.assertIn("Barcode image preview unavailable", template)
        self.assertIn("Open PNG", template)

    def test_admin_guide_describes_real_barcode_import_without_batch_number(self):
        guide = (CLOUD_ROOT / "app" / "web" / "templates" / "admin" / "guide.html").read_text(encoding="utf-8")

        self.assertNotIn("Batch number, color, and can size input fields", guide)
        self.assertIn("real manufacturer barcode import", guide)

    def test_admin_guide_uses_client_portal_language(self):
        guide = (CLOUD_ROOT / "app" / "web" / "templates" / "admin" / "guide.html").read_text(encoding="utf-8")

        self.assertNotIn("Owner dashboard", guide)
        self.assertNotIn("Owner Dashboard", guide)
        self.assertIn("Client portal with fleet data", guide)
        self.assertIn("Client portal with real fleet statistics", guide)


class InventoryAdminContractTest(unittest.TestCase):
    def test_manual_inventory_adjustments_need_a_device_target(self):
        adjustment_device_id = getattr(admin, "_inventory_adjustment_device_id", None)

        self.assertIsNotNone(adjustment_device_id)
        self.assertIsNone(adjustment_device_id([]))
        self.assertEqual(
            adjustment_device_id([SimpleNamespace(id="device-1")]),
            "device-1",
        )

    def test_vessel_inventory_blocks_orphan_manual_adjustments(self):
        source = (CLOUD_ROOT / "app" / "web" / "admin.py").read_text(encoding="utf-8")
        start = source.index("async def admin_adjust_vessel_inventory")
        end = source.index('@router.post("/inventory/{vessel_id}/clear-all"', start)
        adjust_route = source[start:end]

        self.assertIn("if not device_id:", adjust_route)
        self.assertIn("Add+a+SmartLocker+device+before+adding+stock", adjust_route)
        self.assertIn('"has_devices": bool(devices)', source)

    def test_vessel_pdf_import_blocks_orphan_adjustments(self):
        source = (CLOUD_ROOT / "app" / "web" / "admin.py").read_text(encoding="utf-8")
        start = source.index("async def admin_import_vessel_pdf")
        end = source.index('@router.post("/inventory/adjust"', start)
        import_route = source[start:end]

        self.assertIn("if not device_id:", import_route)
        self.assertIn("Add+a+SmartLocker+device+before+importing+stock", import_route)

    def test_vessel_inventory_template_warns_when_stock_cannot_be_assigned(self):
        template = (CLOUD_ROOT / "app" / "web" / "templates" / "admin" / "inventory_vessel.html").read_text(encoding="utf-8")

        self.assertIn("{% if has_devices %}", template)
        self.assertIn("Add a SmartLocker device before adding stock", template)

    def test_manual_adjustments_apply_on_top_of_edge_stock(self):
        product = SimpleNamespace(
            id="prod-1",
            name="SIGMACOVER 280",
            product_type="base_paint",
        )
        summary = {
            "SIGMACOVER 280": {
                "name": "SIGMACOVER 280",
                "product_id": "prod-1",
                "product_type": "base_paint",
                "product_type_label": "Base Paint",
                "liters": 5.0,
                "full_liters": 0.0,
                "low_stock": False,
                "colors": [],
                "hardener_name": None,
                "is_hardener_pair": False,
            }
        }

        _apply_inventory_adjustment_summary(
            product_summary=summary,
            product=product,
            adjustment_type="manual_add",
            quantity_liters=10.0,
            vessel_product_colors={},
        )

        self.assertEqual(summary["SIGMACOVER 280"]["liters"], 15.0)

    def test_manual_remove_never_makes_negative_stock(self):
        product = SimpleNamespace(
            id="prod-1",
            name="SIGMACOVER 280",
            product_type="base_paint",
        )
        summary = {
            "SIGMACOVER 280": {
                "name": "SIGMACOVER 280",
                "product_id": "prod-1",
                "product_type": "base_paint",
                "product_type_label": "Base Paint",
                "liters": 5.0,
                "full_liters": 0.0,
                "low_stock": False,
                "colors": [],
                "hardener_name": None,
                "is_hardener_pair": False,
            }
        }

        _apply_inventory_adjustment_summary(
            product_summary=summary,
            product=product,
            adjustment_type="manual_remove",
            quantity_liters=20.0,
            vessel_product_colors={},
        )

        self.assertEqual(summary["SIGMACOVER 280"]["liters"], 0.0)


if __name__ == "__main__":
    unittest.main()
