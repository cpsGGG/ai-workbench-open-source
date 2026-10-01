import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

import server


class ClaudeLegacyTokenStatsTests(unittest.TestCase):
    def descriptor(self, path: Path) -> dict:
        stat = path.stat()
        resolved = path.resolve()
        return {
            "path": str(resolved),
            "pathKey": str(resolved).lower(),
            "size": stat.st_size,
            "mtimeNs": stat.st_mtime_ns,
        }

    def write_json(self, path: Path, payload) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")

    def write_jsonl(self, path: Path, rows: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    def fixture(self, root: Path, zero_authoritative_usage: bool = False):
        home = root / ".claude"
        projects = home / "projects"
        current_path = projects / "project-current" / "current.jsonl"
        current_usage = (
            {"input_tokens": 0, "output_tokens": 0}
            if zero_authoritative_usage
            else {"input_tokens": 10, "output_tokens": 5}
        )
        self.write_jsonl(
            current_path,
            [
                {
                    "timestamp": "2026-01-13T02:00:00Z",
                    "sessionId": "current",
                    "message": {
                        "id": "message-current",
                        "role": "assistant",
                        "model": "model-current",
                        "usage": current_usage,
                    },
                }
            ],
        )
        self.write_jsonl(
            home / "history.jsonl",
            [
                {
                    "timestamp": int(
                        datetime(2026, 1, 12, 1, tzinfo=timezone.utc).timestamp() * 1000
                    ),
                    "sessionId": "legacy-history",
                    "project": "C:\\old",
                },
                {
                    "timestamp": int(
                        datetime(2026, 1, 12, 2, tzinfo=timezone.utc).timestamp() * 1000
                    ),
                    "sessionId": "legacy-history",
                    "project": "C:\\old",
                },
                {
                    "timestamp": int(
                        datetime(2026, 1, 13, 2, tzinfo=timezone.utc).timestamp() * 1000
                    ),
                    "sessionId": "current",
                    "project": "C:\\current",
                },
            ],
        )
        self.write_json(
            projects / "project-old" / "sessions-index.json",
            {
                "version": 1,
                "entries": [
                    {
                        "sessionId": "legacy-history",
                        "created": "2026-01-12T01:00:00Z",
                        "modified": "2026-01-12T02:00:00Z",
                        "projectPath": "C:\\old",
                    },
                    {
                        "sessionId": "legacy-index",
                        "created": "2026-01-11T01:00:00Z",
                        "modified": "2026-01-11T01:05:00Z",
                        "projectPath": "C:\\indexed",
                    },
                ],
            },
        )
        self.write_json(
            home / "stats-cache.json",
            {
                "version": 2,
                "firstSessionDate": "2026-01-11T01:00:00Z",
                "lastComputedDate": "2026-01-13",
                "totalSessions": 3,
                "totalMessages": 8,
                "dailyActivity": [
                    {"date": "2026-01-11", "sessionCount": 1, "messageCount": 1, "toolCallCount": 0},
                    {"date": "2026-01-12", "sessionCount": 1, "messageCount": 6, "toolCallCount": 2},
                    {"date": "2026-01-13", "sessionCount": 1, "messageCount": 1, "toolCallCount": 0},
                ],
                "dailyModelTokens": [
                    {"date": "2026-01-12", "tokensByModel": {"legacy-model": 100}},
                    {"date": "2026-01-13", "tokensByModel": {"model-current": 200}},
                ],
                "modelUsage": {
                    "legacy-model": {
                        "inputTokens": 90,
                        "outputTokens": 10,
                        "cacheReadInputTokens": 999,
                        "cacheCreationInputTokens": 7,
                    }
                },
            },
        )
        self.write_json(
            root / ".claude.json",
            {"firstStartTime": "2025-11-25T06:09:42Z", "installMethod": "npm"},
        )
        return home, projects, current_path

    def test_summary_accepts_cache_write_alias_without_double_counting(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / ".claude"
            path = home / "projects" / "alias-project" / "alias.jsonl"
            self.write_jsonl(
                path,
                [
                    {
                        "timestamp": "2026-02-01T01:00:00Z",
                        "sessionId": "alias-session",
                        "message": {
                            "id": "alias-message",
                            "role": "assistant",
                            "model": "alias-model",
                            "usage": {
                                "input_tokens": 10,
                                "output_tokens": 5,
                                "cache_write_input_tokens": 7,
                            },
                        },
                    }
                ],
            )
            with patch.object(server, "CHAT_HISTORY_CLAUDE_HOME", home):
                summary = server.token_stats_build_claude()

        active_day = next(row for row in summary["days"] if row["tokens"])
        self.assertEqual(summary["canonicalTotal"], 22)
        self.assertEqual(active_day["tokens"], 22)
        self.assertEqual(active_day["cacheCreationTokens"], 7)
        self.assertEqual(active_day["cacheCreationCoverage"], "full")
        self.assertEqual(active_day["cacheCreationKnownRequestCount"], 1)
        self.assertEqual(active_day["cacheCreationUnknownRequestCount"], 0)

    def test_summary_restores_only_missing_activity_and_nonoverlapping_daily_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, _projects, _current = self.fixture(Path(tmp))
            with patch.object(server, "CHAT_HISTORY_CLAUDE_HOME", home):
                summary = server.token_stats_build_claude()

        active = {row["date"]: row for row in summary["days"] if row["sessions"] or row["tokens"]}
        self.assertEqual(summary["canonicalTotal"], 115)
        self.assertEqual(sum(row["tokens"] for row in summary["days"]), 115)
        self.assertEqual(summary["totalWithoutCacheRead"], 115)
        self.assertEqual(summary["totalSessions"], 3)
        self.assertEqual(active["2026-01-12"]["tokens"], 100)
        self.assertEqual(active["2026-01-12"]["restoredSessions"], 1)
        self.assertEqual(active["2026-01-12"]["restoredUserMessages"], 2)
        self.assertTrue(active["2026-01-12"]["hasUnknownTokenActivity"])
        self.assertEqual(active["2026-01-13"]["tokens"], 15)
        self.assertEqual(active["2026-01-13"]["restoredSessions"], 0)
        coverage = summary["coverage"]
        self.assertEqual(coverage["fallbackSessionsAdded"], 2)
        self.assertEqual(coverage["fallbackTokenBucketsAdded"], 1)
        self.assertEqual(coverage["legacyFallback"]["statsCache"]["tokenBucketsSkipped"], 1)
        self.assertEqual(coverage["unallocatedCacheReadTokens"], 999)
        self.assertEqual(coverage["unallocatedCacheCreationTokens"], 7)
        self.assertEqual(coverage["legacyFallback"]["firstStart"]["date"], "2025-11-25")

    def test_zero_authoritative_usage_does_not_hide_confirmed_fallback_bucket(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, projects, current_path = self.fixture(
                Path(tmp), zero_authoritative_usage=True
            )
            with patch.object(server, "CHAT_HISTORY_CLAUDE_HOME", home):
                summary = server.token_stats_build_claude()
            rows, coverage = server.token_stats_build_claude_request_rows(
                [self.descriptor(current_path)], projects, None
            )
            legacy = {
                "activities": coverage.pop("_legacyActivities"),
                "tokenBuckets": coverage.pop("_legacyTokenBuckets"),
                "dailyActivity": coverage.pop("_legacyDailyActivity"),
                "authoritativeActivities": coverage.pop(
                    "_authoritativeActivities"
                ),
            }
            index = {
                "generatedAt": "2026-01-14T00:00:00+08:00",
                "requests": rows,
                "legacyAggregates": {"claude": legacy},
                "coverage": {"claude": coverage},
            }
            with patch.object(server, "token_stats_details_index", return_value=index):
                details = server.build_token_stats_details(
                    date(2026, 1, 11), date(2026, 1, 13), source="claude"
                )

        active = {row["date"]: row for row in summary["days"] if row["sessions"] or row["tokens"]}
        self.assertEqual(active["2026-01-13"]["tokens"], 200)
        self.assertEqual(active["2026-01-13"]["authoritativeSessions"], 1)
        self.assertEqual(active["2026-01-13"]["restoredSessions"], 0)
        self.assertEqual(active["2026-01-13"]["unknownTokenSessions"], 1)
        self.assertTrue(active["2026-01-13"]["hasUnknownTokenActivity"])
        self.assertEqual(summary["coverage"]["fallbackTokenBucketsAdded"], 2)
        self.assertEqual(details["requestTotal"], 0)
        self.assertEqual(details["metrics"]["requestCount"], 0)
        self.assertEqual(details["metrics"]["sessionCount"], summary["totalSessions"])
        self.assertEqual(details["metrics"]["activityOnlySessions"], 2)
        jan_13 = next(row for row in details["trend"] if row["date"] == "2026-01-13")
        self.assertEqual(jan_13["sessionCount"], 1)
        self.assertEqual(jan_13["requestCount"], 0)

    def test_token_fallback_deduplicates_by_date_and_model_bucket(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, _projects, _current = self.fixture(Path(tmp))
            stats_path = home / "stats-cache.json"
            stats = json.loads(stats_path.read_text(encoding="utf-8"))
            stats["dailyModelTokens"][1]["tokensByModel"]["other-model"] = 300
            self.write_json(stats_path, stats)
            with patch.object(server, "CHAT_HISTORY_CLAUDE_HOME", home):
                summary = server.token_stats_build_claude()

        active = {row["date"]: row for row in summary["days"] if row["sessions"] or row["tokens"]}
        self.assertEqual(active["2026-01-13"]["tokens"], 315)
        self.assertEqual(summary["canonicalTotal"], 415)
        self.assertEqual(summary["coverage"]["fallbackTokenBucketsAdded"], 2)
        self.assertEqual(
            summary["coverage"]["legacyFallback"]["statsCache"]["tokenBucketsSkipped"],
            1,
        )

    def test_summary_uses_same_canonical_message_usage_as_details(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / ".claude"
            project = home / "projects" / "same-project"
            rows = [
                (
                    "one.jsonl",
                    "2026-02-01T01:00:00Z",
                    {"input_tokens": 10, "output_tokens": 5},
                ),
                (
                    "two.jsonl",
                    "2026-02-01T02:00:00Z",
                    {"input_tokens": 20, "output_tokens": 5},
                ),
            ]
            paths = []
            for name, timestamp, usage in rows:
                path = project / name
                paths.append(path)
                self.write_jsonl(
                    path,
                    [
                        {
                            "timestamp": timestamp,
                            "sessionId": "duplicate-session",
                            "message": {
                                "id": "same-message",
                                "role": "assistant",
                                "model": "same-model",
                                "usage": usage,
                            },
                        }
                    ],
                )
            with patch.object(server, "CHAT_HISTORY_CLAUDE_HOME", home):
                summary = server.token_stats_build_claude()
                detail_rows, detail_coverage = server.token_stats_build_claude_request_rows(
                    [self.descriptor(path) for path in paths], project.parent, None
                )

        self.assertEqual(summary["canonicalTotal"], 25)
        self.assertEqual(sum(row["tokens"] for row in summary["days"]), 25)
        self.assertEqual(summary["totalSessions"], 1)
        self.assertEqual(sum(row["assistantMessages"] for row in summary["days"]), 1)
        self.assertEqual(summary["coverage"]["usageRows"], 2)
        self.assertEqual(summary["coverage"]["canonicalUsageRows"], 1)
        self.assertEqual(summary["coverage"]["duplicateUsageRows"], 1)
        self.assertEqual(len(detail_rows), 1)
        self.assertEqual(detail_rows[0]["totalTokens"], 25)
        self.assertEqual(detail_coverage["duplicateUsageRows"], 1)

    def test_details_aggregates_fallback_without_creating_request_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, projects, current_path = self.fixture(Path(tmp))
            rows, coverage = server.token_stats_build_claude_request_rows(
                [self.descriptor(current_path)], projects, None
            )
            legacy = {
                "activities": coverage.pop("_legacyActivities"),
                "tokenBuckets": coverage.pop("_legacyTokenBuckets"),
                "dailyActivity": coverage.pop("_legacyDailyActivity"),
                "authoritativeActivities": coverage.pop(
                    "_authoritativeActivities"
                ),
            }
            index = {
                "generatedAt": "2026-01-14T00:00:00+08:00",
                "requests": rows,
                "legacyAggregates": {"claude": legacy},
                "coverage": {"claude": coverage},
            }
            with patch.object(server, "token_stats_details_index", return_value=index):
                details = server.build_token_stats_details(
                    date(2026, 1, 11), date(2026, 1, 13), source="claude"
                )

        self.assertEqual(details["requestTotal"], 1)
        self.assertEqual(details["requestReturned"], 1)
        self.assertEqual(details["metrics"]["requestCount"], 1)
        self.assertEqual(details["metrics"]["sessionCount"], 3)
        self.assertEqual(details["metrics"]["totalTokens"], 115)
        self.assertEqual(details["metrics"]["unattributedTokens"], 100)
        self.assertEqual(sum(row["totalTokens"] for row in details["trend"]), 115)
        self.assertEqual(details["legacyFallback"]["requestRowsAdded"], 0)
        self.assertEqual(details["legacyFallback"]["tokenBuckets"], 1)

    def test_anonymous_daily_activity_does_not_inflate_global_session_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, projects, current_path = self.fixture(Path(tmp))
            stats_path = home / "stats-cache.json"
            stats = json.loads(stats_path.read_text(encoding="utf-8"))
            stats["dailyActivity"][1]["sessionCount"] = 3
            self.write_json(stats_path, stats)
            with patch.object(server, "CHAT_HISTORY_CLAUDE_HOME", home):
                summary = server.token_stats_build_claude()
            rows, coverage = server.token_stats_build_claude_request_rows(
                [self.descriptor(current_path)], projects, None
            )
            legacy = {
                "activities": coverage.pop("_legacyActivities"),
                "tokenBuckets": coverage.pop("_legacyTokenBuckets"),
                "dailyActivity": coverage.pop("_legacyDailyActivity"),
                "authoritativeActivities": coverage.pop(
                    "_authoritativeActivities"
                ),
            }
            index = {
                "generatedAt": "2026-01-14T00:00:00+08:00",
                "requests": rows,
                "legacyAggregates": {"claude": legacy},
                "coverage": {"claude": coverage},
            }
            with patch.object(server, "token_stats_details_index", return_value=index):
                details = server.build_token_stats_details(
                    date(2026, 1, 11), date(2026, 1, 13), source="claude"
                )
                single_day_details = server.build_token_stats_details(
                    date(2026, 1, 12), date(2026, 1, 12), source="claude"
                )

        self.assertEqual(summary["totalSessions"], 3)
        self.assertEqual(summary["coverage"]["anonymousActivitySessionDays"], 2)
        self.assertEqual(details["metrics"]["sessionCount"], summary["totalSessions"])
        self.assertEqual(details["metrics"]["activityOnlySessions"], 2)
        jan_12 = next(row for row in details["trend"] if row["date"] == "2026-01-12")
        self.assertEqual(jan_12["sessionCount"], 3)
        self.assertEqual(jan_12["anonymousActivitySessionDays"], 2)
        self.assertEqual(jan_12["activityOnlySessions"], 3)
        self.assertEqual(single_day_details["metrics"]["sessionCount"], 3)
        self.assertEqual(single_day_details["metrics"]["activityOnlySessions"], 3)
        claude_provider = next(
            row
            for row in single_day_details["providers"]
            if row["provider"] == "Claude Code"
        )
        unknown_model = next(
            row for row in single_day_details["models"] if row["model"] == "unknown"
        )
        self.assertEqual(claude_provider["sessionCount"], 3)
        self.assertEqual(claude_provider["activityOnlySessions"], 3)
        self.assertEqual(unknown_model["sessionCount"], 3)
        self.assertEqual(unknown_model["activityOnlySessions"], 3)

    def test_single_legacy_day_uses_daily_trend_without_inventing_an_hour(self):
        index = {
            "generatedAt": "2026-01-14T00:00:00+08:00",
            "requests": [],
            "legacyAggregates": {
                "claude": {
                    "activities": [
                        {
                            "sessionId": "legacy",
                            "date": "2026-01-12",
                            "sources": ["history"],
                        }
                    ],
                    "tokenBuckets": [
                        {"date": "2026-01-12", "model": "legacy-model", "tokens": 100}
                    ],
                    "dailyActivity": {},
                }
            },
            "coverage": {"claude": {}},
        }
        with patch.object(server, "token_stats_details_index", return_value=index):
            details = server.build_token_stats_details(
                date(2026, 1, 12), date(2026, 1, 12), source="claude"
            )

        self.assertEqual(details["period"]["trendGrain"], "day")
        self.assertEqual(len(details["trend"]), 1)
        self.assertEqual(details["trend"][0]["totalTokens"], 100)
        self.assertEqual(details["requestTotal"], 0)


if __name__ == "__main__":
    unittest.main()
