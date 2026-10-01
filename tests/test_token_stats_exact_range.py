import unittest
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import server


class TokenStatsExactRangeTests(unittest.TestCase):
    def row(self, row_id: str, timestamp: str, total: int, local_date: str) -> dict:
        return {
            "id": row_id,
            "timestamp": timestamp,
            "timestampUtc": None,
            "date": local_date,
            "provider": "Codex",
            "model": "test-model",
            "source": "codex",
            "sessionId": f"session-{row_id}",
            "inputTokens": total,
            "rawInputTokens": total,
            "outputTokens": 0,
            "cachedInputTokens": 0,
            "cacheCreationInputTokens": 0,
            "reasoningTokens": 0,
            "totalTokens": total,
            "costUsd": 0,
            "priceKnown": False,
        }

    def index(self) -> dict:
        rows = [
            self.row("before", "2026-09-14T16:29:59Z", 1, "2026-09-15"),
            self.row("at-start", "2026-09-14T16:30:00Z", 10, "2026-09-15"),
            self.row("utc-cross-day", "2026-09-14T16:45:00Z", 20, "2026-09-15"),
            self.row("last-hour", "2026-09-15T16:29:59Z", 30, "2026-09-16"),
            self.row("at-end", "2026-09-15T16:30:00Z", 40, "2026-09-16"),
        ]
        return {
            "generatedAt": "2026-09-16T00:30:00+08:00",
            "requests": rows,
            "legacyAggregates": {
                "claude": {
                    "activities": [
                        {"sessionId": "legacy-session", "date": "2026-09-15"}
                    ],
                    "tokenBuckets": [
                        {"date": "2026-09-15", "model": "legacy-model", "tokens": 99}
                    ],
                }
            },
            "coverage": {},
        }

    def test_exact_range_filters_before_metrics_pagination_and_trend(self):
        start_time = datetime(2026, 9, 15, 0, 30, tzinfo=server.TOKEN_STATS_TZ)
        end_time = datetime(2026, 9, 16, 0, 30, tzinfo=server.TOKEN_STATS_TZ)
        with patch.object(server, "token_stats_details_index", return_value=self.index()):
            details = server.build_token_stats_details(
                date(2026, 9, 15),
                date(2026, 9, 16),
                limit=2,
                start_time=start_time,
                end_time=end_time,
            )

        self.assertEqual(details["requestTotal"], 3)
        self.assertEqual(details["requestReturned"], 2)
        self.assertEqual(details["metrics"]["totalTokens"], 60)
        self.assertEqual(
            {row["id"] for row in details["requests"]},
            {"at-start", "utc-cross-day"},
        )
        self.assertEqual(details["period"]["startTime"], "2026-09-15T00:30:00+08:00")
        self.assertEqual(details["period"]["endTime"], "2026-09-16T00:30:00+08:00")
        self.assertTrue(details["period"]["exact"])
        self.assertFalse(details["period"]["inclusive"])
        self.assertEqual(details["period"]["trendGrain"], "hour")
        self.assertEqual(len(details["trend"]), 24)
        self.assertEqual(details["trend"][0]["timestamp"], details["period"]["startTime"])
        self.assertEqual(sum(bucket["totalTokens"] for bucket in details["trend"]), 60)
        self.assertEqual(
            details["exactRangeCoverage"]["excludedUntimestampedRows"], 2
        )
        self.assertEqual(
            details["exactRangeCoverage"]["precision"],
            "timestamped-requests-only",
        )

    def test_natural_date_range_keeps_inclusive_calendar_day_and_legacy_behavior(self):
        with patch.object(server, "token_stats_details_index", return_value=self.index()):
            details = server.build_token_stats_details(
                date(2026, 9, 15), date(2026, 9, 15)
            )

        self.assertEqual(details["requestTotal"], 3)
        self.assertEqual(details["metrics"]["totalTokens"], 130)
        self.assertEqual(details["legacyFallback"]["tokenBuckets"], 1)
        self.assertNotIn("exactRangeCoverage", details)
        self.assertNotIn("exact", details["period"])

    def test_iso_parser_uses_utc_plus_eight_for_naive_values_and_converts_z(self):
        naive = server.token_stats_parse_period_time(
            "2026-09-15T00:30:00", "start_time"
        )
        utc = server.token_stats_parse_period_time(
            "2026-09-14T16:30:00Z", "start_time"
        )

        self.assertEqual(naive.utcoffset(), timedelta(hours=8))
        self.assertEqual(naive, utc)

    def test_exact_range_rejects_unpaired_reversed_and_invalid_parameters(self):
        start_time = datetime(2026, 9, 15, tzinfo=server.TOKEN_STATS_TZ)
        end_time = start_time + timedelta(days=1)
        with self.assertRaisesRegex(ValueError, "provided together"):
            server.build_token_stats_details(
                date(2026, 9, 15), date(2026, 9, 15), start_time=start_time
            )
        with self.assertRaisesRegex(ValueError, "must be before"):
            server.build_token_stats_details(
                date(2026, 9, 15),
                date(2026, 9, 15),
                start_time=end_time,
                end_time=start_time,
            )
        with self.assertRaisesRegex(ValueError, "valid ISO 8601"):
            server.token_stats_parse_period_time("not-a-time", "start_time")
        with self.assertRaisesRegex(ValueError, "valid ISO 8601"):
            server.token_stats_parse_period_time("2026-09-15", "start_time")

    def test_details_endpoint_requires_time_parameters_as_a_pair(self):
        class ResponseRecorder:
            def __init__(self):
                self.payload = None
                self.status = None

            def send_json(self, payload, status=200):
                self.payload = payload
                self.status = status

        recorder = ResponseRecorder()
        server.WorkbenchHandler.serve_token_stats_details(
            recorder, "start_time=2026-09-15T00%3A30%3A00%2B08%3A00"
        )

        self.assertEqual(recorder.status, 400)
        self.assertIn("provided together", recorder.payload["error"])

    def test_details_endpoint_passes_normalized_exact_bounds(self):
        class ResponseRecorder:
            def __init__(self):
                self.payload = None
                self.status = None

            def send_json(self, payload, status=200):
                self.payload = payload
                self.status = status

        query = urllib.parse.urlencode(
            {
                "start_time": "2026-09-14T16:30:00Z",
                "end_time": "2026-09-15T16:30:00Z",
            }
        )
        recorder = ResponseRecorder()
        with patch.object(server, "build_token_stats_details", return_value={"ok": True}) as build:
            server.WorkbenchHandler.serve_token_stats_details(recorder, query)

        self.assertEqual(recorder.status, 200)
        self.assertEqual(recorder.payload, {"ok": True})
        kwargs = build.call_args.kwargs
        self.assertEqual(kwargs["start"], date(2026, 9, 15))
        self.assertEqual(kwargs["end"], date(2026, 9, 16))
        self.assertEqual(kwargs["start_time"].utcoffset(), timedelta(hours=8))
        self.assertEqual(kwargs["end_time"].utcoffset(), timedelta(hours=8))


if __name__ == "__main__":
    unittest.main()
