import unittest
from types import SimpleNamespace

from app.web.admin import (
    _apply_inventory_adjustment_summary,
    _barcode_payload_value,
)


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


class InventoryAdminContractTest(unittest.TestCase):
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
