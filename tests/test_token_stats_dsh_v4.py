import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server


@unittest.skipIf(server.zstandard is None, "zstandard is not installed")
class DshV4TokenStatsTests(unittest.TestCase):
    def write_session(self, home, filename, messages):
        path = home / "sessions" / "fixture" / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = [{"type": "session", "id": "same-session", "version": 4}]
        rows += [{
            "type": "assistant/message",
            "time": 1_788_000_000_000,
            "data": {"usage": usage, "message": {
                "id": message_id, "source": {"model": "fixture-model"}
            }},
        } for message_id, usage in messages]
        body = "\n".join(json.dumps(row) for row in rows).encode() + b"\n"
        path.write_bytes(server.zstandard.ZstdCompressor().compress(body))
        return path

    def descriptor(self, path):
        stat = path.stat()
        return {"path": str(path), "pathKey": str(path).lower(),
                "size": stat.st_size, "mtimeNs": stat.st_mtime_ns}

    def test_v4_is_included_with_legacy_and_v3_and_formats_are_reported(self):
        usage = {"inputTokens": 10, "outputTokens": 2, "cacheReadTokens": 3}
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self.write_session(home, "session.v4.jsonl.zstd", [("new", usage)])
            with patch.object(server, "TOKEN_STATS_DEEPSEEK_HOME", home):
                files = server.token_stats_deepseek_files()
                summary = server.token_stats_build_deepseek()
                rows, coverage = server.token_stats_build_deepseek_request_rows(
                    [self.descriptor(path) for path in files], None
                )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sessionLogVersion"], 4)
        self.assertEqual(summary["canonicalTotal"], 15)
        self.assertEqual(summary["coverage"]["sourceFormats"], {"session.v4.jsonl.zstd": 1})
        self.assertEqual(coverage["sourceFormats"], {"session.v4.jsonl.zstd": 1})
        self.assertEqual(summary["cacheReadCoverage"], "full")
        active_day = next(day for day in summary["days"] if day["tokens"])
        self.assertEqual(active_day["cacheReadCoverage"], "full")
        self.assertEqual(active_day["cacheReadKnownRequestCount"], 1)

    def test_migration_dedupes_ids_and_keeps_unique_equal_token_requests(self):
        usage = {"inputTokens": 10, "outputTokens": 2, "cacheReadTokens": 3}
        complete = {**usage, "cacheWriteTokens": 0}
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            files = [
                self.write_session(home, "session.jsonl.zstd", [("same", usage), ("legacy-only", usage)]),
                self.write_session(home, "session.v3.jsonl.zstd", [("same", usage), ("v3-only", usage)]),
                self.write_session(home, "session.v4.jsonl.zstd", [("same", complete), ("v4-only", complete)]),
            ]
            with patch.object(server, "TOKEN_STATS_DEEPSEEK_HOME", home):
                summary = server.token_stats_build_deepseek()
                rows, _ = server.token_stats_build_deepseek_request_rows(
                    [self.descriptor(path) for path in reversed(files)], None
                )
        self.assertEqual(len(rows), 4)
        self.assertEqual(summary["canonicalTotal"], 60)
        self.assertEqual(summary["coverage"]["duplicateUsageRows"], 2)
        self.assertEqual(sum(row["cacheCreationKnown"] for row in rows), 2)
        self.assertEqual(sum(row["sessionLogVersion"] == 4 for row in rows), 2)


if __name__ == "__main__":
    unittest.main()
