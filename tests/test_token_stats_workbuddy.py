import json
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

import server


class WorkBuddyTokenStatsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.projects = self.root / "projects"
        self.session_dir = self.projects / "c-Users-example-WorkBuddy-demo"
        self.session_dir.mkdir(parents=True)
        self.path = self.session_dir / "session-1.jsonl"
        self.timestamp = 1_700_000_000_000
        self.current = {
            "id": "record-current",
            "timestamp": self.timestamp,
            "type": "function_call",
            "sessionId": "session-1",
            "cwd": r"C:\Users\example\WorkBuddy\demo",
            "providerData": {
                "messageId": "message-current",
                "model": "deepseek-flash",
                "requestModelId": "custom-local:deepseek-v4-flash",
                "requestModelName": "DeepSeek-V4 Flash",
                "rawUsage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "total_tokens": 120,
                    "prompt_cache_hit_tokens": 40,
                    "prompt_cache_miss_tokens": 60,
                    "completion_tokens_details": {"reasoning_tokens": 5},
                },
                "usage": {
                    "inputTokens": 100,
                    "outputTokens": 20,
                    "totalTokens": 120,
                    "inputTokensDetails": [{"cached_tokens": 40}],
                    "outputTokensDetails": [{"reasoning_tokens": 5}],
                },
            },
            "message": {
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "total_tokens": 120,
                    "cache_read_input_tokens": 40,
                }
            },
        }
        self.legacy = {
            "id": "record-legacy",
            "timestamp": self.timestamp + 1_000,
            "type": "message",
            "role": "assistant",
            "sessionId": "session-1",
            "providerData": {
                "model": "auto",
                "usage": {
                    "input_tokens": 50,
                    "output_tokens": 10,
                    "total_tokens": 60,
                    "cache_read_input_tokens": 5,
                    "reasoning_tokens": 2,
                },
            },
        }

    def tearDown(self):
        self.temp_dir.cleanup()

    def write_lines(self, rows, trailing=""):
        content = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
        self.path.write_text(content + trailing, encoding="utf-8")

    def descriptor(self):
        stat = self.path.stat()
        resolved = self.path.resolve()
        return {
            "path": str(resolved),
            "pathKey": str(resolved).lower(),
            "size": stat.st_size,
            "mtimeNs": stat.st_mtime_ns,
        }

    def cache_connection(self):
        connection = sqlite3.connect(self.root / "cache.sqlite")
        connection.execute(
            """
            create table file_cache (
                version text not null,
                source text not null,
                path_key text not null,
                size integer not null,
                mtime_ns integer not null,
                payload blob not null,
                primary key (version, source, path_key)
            )
            """
        )
        return connection

    def test_files_and_usage_normalization(self):
        self.write_lines([self.current, self.legacy])
        self.assertEqual(server.token_stats_workbuddy_files(self.projects), [self.path])

        current = server.token_stats_workbuddy_request_usage(self.current)
        self.assertEqual(current["rawInputTokens"], 100)
        self.assertEqual(current["inputTokens"], 60)
        self.assertEqual(current["cachedInputTokens"], 40)
        self.assertEqual(current["outputTokens"], 20)
        self.assertEqual(current["reasoningTokens"], 5)
        self.assertEqual(current["totalTokens"], 120)
        self.assertFalse(current["cacheCreationKnown"])

        legacy = server.token_stats_workbuddy_request_usage(self.legacy)
        self.assertEqual(legacy["inputTokens"], 45)
        self.assertEqual(legacy["cachedInputTokens"], 5)
        self.assertEqual(legacy["totalTokens"], 60)

    def test_rows_are_exact_deduplicated_and_safe(self):
        self.write_lines([self.current, self.current, self.legacy], trailing="{incomplete")
        rows, coverage = server.token_stats_build_workbuddy_request_rows(
            [self.descriptor()], None
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(coverage["duplicateUsageRowsSkipped"], 1)
        self.assertEqual(coverage["invalidJsonLines"], 0)
        row = next(item for item in rows if item["model"] == "DeepSeek-V4 Flash")
        expected_day = datetime.fromtimestamp(
            self.timestamp / 1000, tz=timezone.utc
        ).astimezone(server.TOKEN_STATS_TZ).date().isoformat()
        self.assertEqual(row["date"], expected_day)
        self.assertEqual(row["source"], "workbuddy")
        self.assertEqual(row["provider"], "WorkBuddy")
        self.assertEqual(row["project"], "demo")
        self.assertNotIn("content", row)
        self.assertNotIn("reasoning", row)

        summary = server.token_stats_deepseek_summary_from_rows(rows, coverage)
        self.assertEqual(summary["canonicalTotal"], 180)
        self.assertEqual(summary["totalSessions"], 1)
        self.assertEqual(summary["providers"][0]["provider"], "WorkBuddy")

    def test_file_cache_appends_without_losing_or_recounting_rows(self):
        self.write_lines([self.current, self.legacy])
        connection = self.cache_connection()
        try:
            first_rows, first_coverage = server.token_stats_build_workbuddy_request_rows(
                [self.descriptor()], connection
            )
            connection.commit()
            self.assertEqual(len(first_rows), 2)
            self.assertEqual(first_coverage["filesParsed"], 1)

            appended = {
                **self.current,
                "id": "record-appended",
                "timestamp": self.timestamp + 2_000,
                "providerData": {
                    **self.current["providerData"],
                    "messageId": "message-appended",
                },
            }
            partial = {
                **self.current,
                "id": "record-partial",
                "timestamp": self.timestamp + 3_000,
                "providerData": {
                    **self.current["providerData"],
                    "messageId": "message-partial",
                },
            }
            partial_text = json.dumps(partial)
            split_at = len(partial_text) // 2
            with self.path.open("a", encoding="utf-8") as target:
                target.write(json.dumps(appended) + "\n")
                target.write(partial_text[:split_at])
            second_rows, second_coverage = server.token_stats_build_workbuddy_request_rows(
                [self.descriptor()], connection
            )
            self.assertEqual(len(second_rows), 3)
            self.assertEqual(second_coverage["filesAppended"], 1)
            self.assertEqual(second_coverage["duplicateUsageRowsSkipped"], 0)

            with self.path.open("a", encoding="utf-8") as target:
                target.write(partial_text[split_at:] + "\n")
            third_rows, third_coverage = server.token_stats_build_workbuddy_request_rows(
                [self.descriptor()], connection
            )
            self.assertEqual(len(third_rows), 4)
            self.assertEqual(third_coverage["filesAppended"], 1)
            self.assertEqual(len({row["id"] for row in third_rows}), 4)
        finally:
            connection.close()

    def test_details_accepts_workbuddy_source(self):
        day = date(2023, 11, 15)
        row = {
            "id": "request-1",
            "source": "workbuddy",
            "provider": "WorkBuddy",
            "model": "DeepSeek-V4 Flash",
            "sessionId": "session-1",
            "timestamp": "2023-11-15T00:00:00+08:00",
            "timestampUtc": "2023-11-14T16:00:00Z",
            "date": day.isoformat(),
            "inputTokens": 60,
            "rawInputTokens": 100,
            "outputTokens": 20,
            "cachedInputTokens": 40,
            "cacheCreationInputTokens": 0,
            "cacheCreationKnown": False,
            "reasoningTokens": 5,
            "totalTokens": 120,
            "costUsd": 0,
            "priceKnown": False,
        }
        fake_index = {
            "generatedAt": "2023-11-15T01:00:00+08:00",
            "requests": [row],
            "legacyAggregates": {"claude": {}},
            "coverage": {"workbuddy": {}},
        }
        with mock.patch.object(server, "token_stats_details_index", return_value=fake_index):
            result = server.build_token_stats_details(
                day, day, source="workbuddy", limit=10
            )
        self.assertEqual(result["period"]["source"], "workbuddy")
        self.assertEqual(result["metrics"]["totalTokens"], 120)
        self.assertEqual(result["requests"][0]["provider"], "WorkBuddy")


if __name__ == "__main__":
    unittest.main()
