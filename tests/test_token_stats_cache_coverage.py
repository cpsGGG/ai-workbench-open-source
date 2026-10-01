import unittest

import server


class CacheReadCoverageTests(unittest.TestCase):
    def request(self, usage, request_id="request"):
        return {
            "id": request_id,
            "provider": "fixture",
            "model": "fixture-model",
            "source": "deepseek",
            "sessionId": "fixture-session",
            "costUsd": 0,
            "priceKnown": False,
            **usage,
        }

    def test_all_json_parsers_distinguish_missing_zero_and_positive_cache_read(self):
        parsers = [
            (server.token_stats_deepseek_request_usage,
             {"inputTokens": 10, "outputTokens": 2}, "cacheReadTokens"),
            (server.token_stats_cline_request_usage,
             {"tokensIn": 10, "tokensOut": 2}, "cacheReads"),
            (server.token_stats_codex_request_usage,
             {"input_tokens": 10, "output_tokens": 2}, "cached_input_tokens"),
            (server.token_stats_claude_request_usage,
             {"input_tokens": 10, "output_tokens": 2}, "cache_read_input_tokens"),
        ]
        for parser, base, field in parsers:
            with self.subTest(parser=parser.__name__):
                missing = parser(base)
                self.assertFalse(missing["cacheReadKnown"])
                self.assertEqual(missing["cachedInputTokens"], 0)
                for value in (0, 3):
                    usage = parser({**base, field: value})
                    self.assertTrue(usage["cacheReadKnown"])
                    self.assertEqual(usage["cachedInputTokens"], value)
                for invalid in (None, "0", True):
                    self.assertFalse(parser({**base, field: invalid})["cacheReadKnown"])

    def test_workbuddy_nested_cache_details_are_known_but_missing_is_unknown(self):
        base = {"prompt_tokens": 10, "completion_tokens": 2}
        for cache_value in (None, 0, 3):
            raw = dict(base)
            if cache_value is not None:
                raw["prompt_tokens_details"] = {"cached_tokens": cache_value}
            usage = server.token_stats_workbuddy_request_usage(
                {"providerData": {"rawUsage": raw}}
            )
            self.assertEqual(usage["cacheReadKnown"], cache_value is not None)
            self.assertEqual(usage["cachedInputTokens"], cache_value or 0)

    def test_codex_delta_does_not_invent_missing_cache_read_field(self):
        delta = server.token_stats_usage_delta(
            {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
            {"input_tokens": 3, "cached_input_tokens": 0, "total_tokens": 3},
        )
        self.assertNotIn("cached_input_tokens", delta)
        self.assertFalse(server.token_stats_codex_request_usage(delta)["cacheReadKnown"])

    def test_unknown_read_coverage_never_yields_a_zero_hit_rate(self):
        missing = self.request(server.token_stats_deepseek_request_usage(
            {"inputTokens": 10, "outputTokens": 2}
        ))
        zero = self.request(server.token_stats_deepseek_request_usage(
            {"inputTokens": 20, "outputTokens": 3, "cacheReadTokens": 0}
        ), "zero")
        for rows, expected in (([missing], "none"), ([missing, zero], "partial")):
            aggregates = [server.token_stats_request_metrics(rows)]
            aggregates += server.token_stats_group_requests(rows, "provider")
            aggregates += server.token_stats_group_models(rows)
            for aggregate in aggregates:
                self.assertEqual(aggregate["cacheReadCoverage"], expected)
                self.assertIsNone(aggregate["cacheHitRatio"])
                self.assertIsNone(aggregate["cacheHitRate"])
        known = server.token_stats_request_metrics([zero])
        self.assertEqual(known["cacheReadCoverage"], "full")
        self.assertEqual(known["cacheHitRate"], 0)
        self.assertEqual(known["cacheReadKnownRequestCount"], 1)
        self.assertEqual(known["cacheReadUnknownRequestCount"], 0)

    def test_old_explicit_values_are_compatible_but_false_and_null_stay_unknown(self):
        usage = server.token_stats_deepseek_request_usage(
            {"inputTokens": 10, "outputTokens": 2, "cacheReadTokens": 0}
        )
        del usage["cacheReadKnown"]
        self.assertEqual(server.token_stats_request_metrics([self.request(usage)])
                         ["cacheReadCoverage"], "full")
        usage["cacheReadKnown"] = False
        self.assertEqual(server.token_stats_request_metrics([self.request(usage)])
                         ["cacheReadCoverage"], "none")
        self.assertFalse(server.token_stats_cache_read_known({"cachedInputTokens": None}))

    def test_legacy_activity_placeholders_are_unknown(self):
        rows = server.token_stats_claude_legacy_metric_rows({
            "activities": [{"sessionId": "old", "date": "2026-09-01"}],
        })
        self.assertTrue(rows)
        self.assertFalse(rows[0]["cacheReadKnown"])
        self.assertIsNone(server.token_stats_request_metrics(rows)["cacheHitRate"])


if __name__ == "__main__":
    unittest.main()
