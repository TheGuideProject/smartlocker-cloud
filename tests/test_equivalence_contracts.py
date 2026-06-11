import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

from app.services import equivalence_client as eq


class EquivalenceHelperContractTest(unittest.TestCase):
    def test_normalize_key_lowercases_and_collapses_whitespace(self):
        self.assertEqual(eq.normalize_key("  SIGMACOVER   280 "), "sigmacover 280")
        self.assertEqual(eq.normalize_key("Sigmacover 280"), "sigmacover 280")

    def test_cache_freshness_window(self):
        now = datetime(2026, 6, 11, 12, 0, 0)
        fresh = now - timedelta(hours=1)
        stale = now - timedelta(hours=200)
        self.assertTrue(eq.is_cache_fresh(fresh, ttl_hours=168, now=now))
        self.assertFalse(eq.is_cache_fresh(stale, ttl_hours=168, now=now))
        self.assertFalse(eq.is_cache_fresh(None, ttl_hours=168, now=now))

    def test_apply_remote_to_cache_maps_fields(self):
        row = SimpleNamespace(
            matched_name=None, match_type=None, coverage_m2_per_l=None,
            coverage_source=None, confidence=None, needs_validation=True,
            specs_json=None, candidates_json=None, fetched_at=None,
        )
        data = {
            "ok": True,
            "matched": {"name": "SIGMACOVER 280", "kind": "ppg"},
            "matchType": "exact",
            "needsValidation": False,
            "confidence": "high",
            "coverage": {"m2PerL": 7.5, "source": "computed"},
            "specs": {"volumeSolidsPercent": 75},
            "candidates": [{"name": "SIGMACOVER 280", "kind": "ppg"}],
        }
        eq._apply_remote_to_cache(row, data)
        self.assertEqual(row.matched_name, "SIGMACOVER 280")
        self.assertEqual(row.match_type, "exact")
        self.assertEqual(row.coverage_m2_per_l, 7.5)
        self.assertEqual(row.coverage_source, "computed")
        self.assertEqual(row.confidence, "high")
        self.assertFalse(row.needs_validation)
        self.assertEqual(row.specs_json, {"volumeSolidsPercent": 75})
        self.assertEqual(len(row.candidates_json), 1)
        self.assertIsNotNone(row.fetched_at)

    def test_cache_to_response_shape(self):
        row = SimpleNamespace(
            query_name="SIGMACOVER 280",
            matched_name="SIGMACOVER 280",
            match_type="exact",
            coverage_m2_per_l=7.5,
            coverage_source="computed",
            confidence="high",
            needs_validation=False,
            specs_json={"volumeSolidsPercent": 75},
            candidates_json=[],
            fetched_at=datetime(2026, 6, 11, 12, 0, 0),
        )
        resp = eq.cache_to_response(row, stale=True)
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["coverage_m2_per_l"], 7.5)
        self.assertTrue(resp["stale"])
        self.assertTrue(resp["cached"])
        self.assertEqual(resp["fetched_at"], "2026-06-11T12:00:00")

    def test_unavailable_response_flags_validation(self):
        resp = eq.unavailable_response("Unknown Paint", "down")
        self.assertFalse(resp["ok"])
        self.assertTrue(resp["needs_validation"])
        self.assertEqual(resp["coverage_source"], "none")
        self.assertIsNone(resp["coverage_m2_per_l"])
        self.assertEqual(resp["error"], "down")

    def test_configuration_gate(self):
        # Depends on settings; default deployment has no key configured.
        from app.config import settings
        original = settings.SMARTLOCKER_SERVICE_KEY
        try:
            settings.SMARTLOCKER_SERVICE_KEY = ""
            self.assertFalse(eq.is_integration_configured())
            settings.SMARTLOCKER_SERVICE_KEY = "a-real-key-value-1234567890"
            self.assertTrue(eq.is_integration_configured())
        finally:
            settings.SMARTLOCKER_SERVICE_KEY = original


if __name__ == "__main__":
    unittest.main()
