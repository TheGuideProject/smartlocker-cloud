import unittest

from pydantic import ValidationError

from app.api.events import EventIn
from app.api.pairing import _color_names_from_payload


class AndroidEventContractTest(unittest.TestCase):
    def test_event_data_accepts_json_string_payload(self):
        event = EventIn(
            event_id="EVT-1",
            event_type="product_scan",
            timestamp=1_717_000_000.0,
            data='{"source":"CAMERA","matched":false}',
        )

        self.assertEqual(event.data, {"source": "CAMERA", "matched": False})

    def test_event_data_wraps_malformed_string_payload(self):
        event = EventIn(
            event_id="EVT-2",
            event_type="product_scan",
            timestamp=1_717_000_000.0,
            data="not-json",
        )

        self.assertEqual(event.data, {"_raw": "not-json"})

    def test_event_data_still_rejects_unexpected_required_types(self):
        with self.assertRaises(ValidationError):
            EventIn(
                event_id="EVT-3",
                event_type="product_scan",
                timestamp="not-a-timestamp",
                data={},
            )


class AndroidConfigContractTest(unittest.TestCase):
    def test_extracts_mobile_color_names_from_cloud_color_objects(self):
        self.assertEqual(
            _color_names_from_payload(
                [
                    {"name": "GREY 5284", "hex": "#8D9199"},
                    {"color": "WHITE"},
                    "CLEAR",
                ],
            ),
            ["GREY 5284", "WHITE", "CLEAR"],
        )


if __name__ == "__main__":
    unittest.main()
