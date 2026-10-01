import copy
import sqlite3
import unittest
from unittest.mock import patch

import server


class AstraPricingTests(unittest.TestCase):
    def usage(self, raw=100_000, cached=80_000, output=1_000):
        return {"inputTokens": raw, "cachedInputTokens": cached, "outputTokens": output}

    def test_standard_and_aliases(self):
        for tier, expected in (("standard", .33), ("default", .33),
                               ("fast", .66), ("priority", .66),
                               ("batch", .165), ("flex", .165)):
            with self.subTest(tier=tier):
                result = server.estimate_model_cost("gpt-6-astra", tier, self.usage())
                self.assertTrue(result["priceKnown"])
                self.assertAlmostEqual(result["costUsd"], expected)

    def test_long_context_standard_and_fast(self):
        for tier, expected in (("standard", 2.25), ("fast", 4.5), ("flex", 1.125)):
            result = server.estimate_model_cost("gpt-6-astra", tier, self.usage(300_000, 250_000, 10_000))
            self.assertAlmostEqual(result["costUsd"], expected)

    def test_threshold_includes_cache_and_is_strict(self):
        at = server.estimate_model_cost("gpt-6-astra", "standard", self.usage(272_000, 272_000, 0))
        above = server.estimate_model_cost("GPT-6-ASTRA", "standard", self.usage(272_001, 272_001, 0))
        self.assertEqual(at["price"]["cachedInput"], 1)
        self.assertEqual(above["price"]["cachedInput"], 2)
        self.assertEqual(above["price"]["cacheWrite"], 25)

    def test_shared_prices_do_not_mutate_or_compound(self):
        before = copy.deepcopy(server.MODEL_PRICES_PER_1M)
        for _ in range(3):
            server.estimate_model_cost("gpt-6-astra", "fast", self.usage(300_000))
        self.assertEqual(server.MODEL_PRICES_PER_1M, before)
        self.assertEqual(server.estimate_model_cost("gpt-6-astra", "standard", self.usage())["costUsd"], .33)

    def test_unknown_model_or_tier_remains_unpriced(self):
        for model, tier in (("gpt-6-astra", "unknown"), ("gpt-6-astra-pro", "standard")):
            self.assertFalse(server.estimate_model_cost(model, tier, self.usage())["priceKnown"])

    def test_other_models_unchanged(self):
        result = server.estimate_model_cost("gpt-5.6-sol", "standard", self.usage(300_000, 250_000, 10_000))
        self.assertEqual(result["costUsd"], .675)

    def test_session_totals_do_not_trigger_request_context_pricing(self):
        result = server.estimate_model_cost("gpt-6-astra", "standard", self.usage(1_000_000), usage_is_request=False)
        self.assertFalse(result["priceKnown"])

    def test_old_unpriced_cache_not_reused(self):
        with sqlite3.connect(":memory:") as conn:
            conn.execute("CREATE TABLE file_cache (version TEXT, source TEXT, path_key TEXT, size INTEGER, mtime_ns INTEGER, payload BLOB, PRIMARY KEY(version, source, path_key))")
            descriptor = {"pathKey": "fixture", "size": 10, "mtimeNs": 20}
            with patch.object(server, "TOKEN_STATS_DETAILS_FILE_CACHE_VERSION", "2026-08-11-v3"):
                server.token_stats_details_cache_save(conn, "codex", descriptor, {"priceKnown": False})
            self.assertIsNone(server.token_stats_details_cache_load(conn, "codex", "fixture"))
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM file_cache").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
