import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

import server


FIXED_NOW = datetime(2024, 6, 4, 12, tzinfo=timezone.utc)
SESSION_ID = "fixture-session"
MODEL = "gemini-fixture-pro"


class FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls.fromtimestamp(FIXED_NOW.timestamp(), tz=tz)


def proto_varint(value):
    result = bytearray()
    while value >= 128:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def proto_field(number, value):
    if isinstance(value, int):
        return proto_varint(number << 3) + proto_varint(value)
    if isinstance(value, str):
        value = value.encode("utf-8")
    return proto_varint((number << 3) | 2) + proto_varint(len(value)) + value


def response_metadata(timestamp, usage, request_id):
    return (
        proto_field(1, proto_field(1, timestamp))
        + proto_field(9, b"".join(proto_field(key, value) for key, value in usage.items()))
        + proto_field(12, request_id)
    )


class AntigravityTokenStatsTests(unittest.TestCase):
    def setUp(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.root = Path(temp_dir.name)
        self.home = self.root / "antigravity"
        self.conversations = self.home / "conversations"
        self.conversations.mkdir(parents=True)
        self.db_path = self.conversations / f"{SESSION_ID}.db"
        self.cache_path = self.root / "cache" / "details.sqlite"

        # Exercise the real manifest/index/cache pipeline, with every source and
        # cache redirected away from the user's logs, configuration, and data.
        patcher = mock.patch.multiple(
            server,
            CODEX_HOME=self.root / "codex",
            CODEX_LOG_DB=self.root / "codex" / "logs_2.sqlite",
            CODEX_SESSIONS_DIR=self.root / "codex" / "sessions",
            CODEX_SESSION_INDEX=self.root / "codex" / "session_index.jsonl",
            TOKEN_STATS_CODEX_CONFIG=self.root / "codex" / "config.toml",
            TOKEN_STATS_CODEX_STATE_DB=self.root / "codex" / "state_5.sqlite",
            CHAT_HISTORY_CLAUDE_HOME=self.root / "claude",
            TOKEN_STATS_DEEPSEEK_HOME=self.root / "deepseek",
            TOKEN_STATS_WORKBUDDY_HOME=self.root / "workbuddy",
            TOKEN_STATS_CLINE_HOME=self.root / "cline",
            TOKEN_STATS_CLINE_CHINESE_HOME=self.root / "cline-chinese",
            TOKEN_STATS_ANTIGRAVITY_HOME=self.home,
            TOKEN_STATS_DETAILS_FILE_CACHE_DB=self.cache_path,
            TOKEN_STATS_DETAILS_CACHE=None,
            TOKEN_STATS_DETAILS_CACHE_BUILT_AT=0.0,
            TOKEN_STATS_DETAILS_CACHE_SIGNATURE=None,
            TOKEN_STATS_DETAILS_CACHE_REVISION=0,
            TOKEN_STATS_DETAILS_PAYLOAD_CACHE={},
            datetime=FixedDateTime,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        with closing(sqlite3.connect(self.home / "conversation_summaries.db")) as connection, connection:
            connection.execute(
                "CREATE TABLE conversation_summaries (conversation_id TEXT, title TEXT, "
                "preview TEXT, workspace_uris TEXT, last_modified_time INTEGER)"
            )
            connection.executemany(
                "INSERT INTO conversation_summaries VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        SESSION_ID, "  Fixture conversation  ", "  Fixture preview  ",
                        json.dumps(["file:///D:/projects/%E6%BC%94%E7%A4%BA/"]),
                        1717237200,
                    ),
                    ("preview-only", None, "  Preview title  ", "invalid-json", 1717237201),
                ],
            )

        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            connection.execute("CREATE TABLE gen_metadata (idx INTEGER, data BLOB)")
            connection.execute(
                "CREATE TABLE steps (idx INTEGER, step_type INTEGER, metadata BLOB)"
            )
            connection.execute(
                "INSERT INTO gen_metadata VALUES (?, ?)",
                (1, proto_field(1, proto_field(19, MODEL)) + proto_field(4, "request-1")),
            )
            connection.executemany(
                "INSERT INTO steps VALUES (?, ?, ?)",
                [
                    (
                        3, 15,
                        response_metadata(
                            int(datetime(2024, 6, 1, 10, 20, tzinfo=timezone.utc).timestamp()),
                            {1: 1318, 2: 19888, 3: 659, 9: 326}, "request-1",
                        ),
                    ),
                    (
                        7, 15,
                        response_metadata(
                            int(datetime(2024, 6, 1, 23, 45, tzinfo=timezone.utc).timestamp()),
                            {1: 80, 2: 20, 3: 30, 9: 10}, "request-without-model",
                        ),
                    ),
                    # Non-response steps must not contribute usage.
                    (8, 1, response_metadata(1717285500, {1: 999999}, "ignored")),
                ],
            )

    def test_varint_and_protobuf_decoder(self):
        # Known wire bytes are independent of the fixture encoder above.
        raw = bytes([0x08, 0xa6, 0x0a, 0x10, 0xb0, 0x9b, 0x01, 0x18, 0x93, 0x05])
        parsed = server.token_stats_parse_proto(raw)
        self.assertEqual(parsed[1][0][1], 1318)
        self.assertEqual(parsed[2][0][1], 19888)
        self.assertEqual(parsed[3][0][1], 659)

    def test_antigravity_files_discovery(self):
        other_db = self.conversations / "another.DB"
        other_db.touch()
        for name in (f"{SESSION_ID}.db-shm", f"{SESSION_ID}.db-wal", "notes.txt"):
            (self.conversations / name).touch()
        (self.conversations / "directory.db").mkdir()
        self.assertEqual(server.token_stats_antigravity_files(), [other_db, self.db_path])
        self.assertEqual(server.token_stats_antigravity_files(self.root / "missing"), [])

    def test_antigravity_summaries(self):
        summaries = server.token_stats_antigravity_summaries()
        self.assertEqual(set(summaries), {SESSION_ID, "preview-only"})
        self.assertEqual(
            summaries[SESSION_ID],
            {
                "title": "Fixture conversation", "preview": "Fixture preview",
                "project": "演示", "lastModifiedTime": 1717237200,
            },
        )
        self.assertEqual(summaries["preview-only"]["title"], "Preview title")
        self.assertEqual(summaries["preview-only"]["project"], "Antigravity")

    def test_antigravity_db_candidates(self):
        coverage = server.token_stats_details_source_coverage("antigravity")
        candidates = server.token_stats_antigravity_db_candidates(
            self.db_path, server.token_stats_antigravity_summaries(), coverage
        )
        self.assertEqual(len(candidates), 2)
        first, second = candidates
        self.assertEqual(first["source"], "antigravity")
        self.assertEqual(first["provider"], "Antigravity")
        self.assertEqual(first["sessionId"], SESSION_ID)
        self.assertEqual(first["sessionTitle"], "Fixture conversation")
        self.assertEqual(first["project"], "演示")
        self.assertEqual(first["model"], MODEL)
        self.assertEqual(first["inputTokens"], 1318)
        self.assertEqual(first["rawInputTokens"], 21206)
        self.assertEqual(first["cachedInputTokens"], 19888)
        self.assertEqual(first["outputTokens"], 659)
        self.assertEqual(first["reasoningTokens"], 326)
        self.assertEqual(first["totalTokens"], 21865)
        self.assertEqual(first["timestampUtc"], "2024-06-01T10:20:00Z")
        self.assertEqual(first["timestamp"], "2024-06-01T18:20:00+08:00")
        self.assertEqual(second["model"], "gemini-3.8-flash")
        self.assertEqual(second["date"], "2024-06-02")
        self.assertEqual(second["totalTokens"], 130)
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(coverage["modelsFromMetadata"], 1)
        self.assertEqual(coverage["responseSteps"], 2)
        self.assertEqual(coverage["usageCandidates"], 2)
        self.assertEqual(coverage["filesRead"], 1)
        self.assertEqual(coverage["filesFailed"], 0)

    def test_build_antigravity_summary(self):
        summary = server.token_stats_build_antigravity()
        self.assertEqual(summary["canonicalTotal"], 21995)
        self.assertEqual(summary["totalWithCacheRead"], 21995)
        self.assertEqual(summary["totalWithoutCacheRead"], 2087)
        self.assertEqual(summary["totalSessions"], 1)
        self.assertEqual(summary["totalProviders"], 1)
        self.assertEqual(summary["totalModels"], 2)
        self.assertEqual(
            [(day["date"], day["tokens"], day["requests"]) for day in summary["days"]],
            [("2024-06-01", 21865, 1), ("2024-06-02", 130, 1),
             ("2024-06-03", 0, 0), ("2024-06-04", 0, 0)],
        )
        self.assertEqual(summary["providers"][0]["provider"], "Antigravity")
        self.assertEqual(summary["providers"][0]["totalTokens"], 21995)
        self.assertEqual(summary["models"][0]["model"], MODEL)
        self.assertEqual(summary["coverage"]["dailyTokenTotal"], 21995)

    def test_source_whitelist_in_details(self):
        details = server.build_token_stats_details(
            start=date(2024, 6, 1), end=date(2024, 6, 2), source="antigravity", limit=10
        )
        self.assertEqual(details["period"]["source"], "antigravity")
        self.assertEqual(details["generatedAt"], "2024-06-04T20:00:00+08:00")
        self.assertEqual(details["requestTotal"], 2)
        self.assertEqual(details["summary"]["totalTokens"], 21995)
        self.assertEqual(details["filterOptions"]["sources"], ["antigravity"])
        self.assertEqual({row["sessionId"] for row in details["requests"]}, {SESSION_ID})
        self.assertEqual(details["coverage"]["fileCache"], str(self.cache_path))
        self.assertTrue(self.cache_path.is_file())
        for source in ("codex", "claude", "deepseek", "workbuddy"):
            self.assertEqual(details["coverage"][source]["requestRows"], 0)

        next_day = server.build_token_stats_details(
            start=date(2024, 6, 2), end=date(2024, 6, 2), source="antigravity", limit=10
        )
        self.assertEqual(next_day["requestTotal"], 1)
        self.assertEqual(next_day["summary"]["totalTokens"], 130)
        with self.assertRaisesRegex(ValueError, "source must be"):
            server.build_token_stats_details(
                start=date(2024, 6, 1), end=date(2024, 6, 2), source="invalid-source"
            )


if __name__ == "__main__":
    unittest.main()
