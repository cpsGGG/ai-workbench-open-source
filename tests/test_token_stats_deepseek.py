import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

import server


@unittest.skipIf(server.zstandard is None, "zstandard is not installed")
class DeepSeekTokenStatsTests(unittest.TestCase):
    def write_session(
        self,
        root: Path,
        folder: str,
        rows: list[dict],
        filename: str = "session.jsonl.zstd",
    ) -> Path:
        path = root / "sessions" / folder / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        source = "\n".join(json.dumps(row) for row in rows).encode("utf-8") + b"\n"
        path.write_bytes(server.zstandard.ZstdCompressor().compress(source))
        return path

    def descriptor(self, path: Path) -> dict:
        stat = path.stat()
        resolved = path.resolve()
        return {
            "path": str(resolved),
            "pathKey": str(resolved).lower(),
            "size": stat.st_size,
            "mtimeNs": stat.st_mtime_ns,
        }

    def cline_descriptor(self, path: Path, provider="Cline Chinese") -> dict:
        descriptor = self.descriptor(path)
        metadata = path.parent / "task_metadata.json"
        metadata_stat = metadata.stat()
        descriptor.update(
            {
                "contextKey": f"{metadata_stat.st_size}:{metadata_stat.st_mtime_ns}",
                "metadataPath": str(metadata.resolve()),
                "provider": provider,
            }
        )
        return descriptor

    def write_cline_task(
        self, root: Path, task_id: str, messages: list[dict], model_usage: list[dict]
    ) -> Path:
        task = root / "tasks" / task_id
        task.mkdir(parents=True, exist_ok=True)
        ui_path = task / "ui_messages.json"
        ui_path.write_text(json.dumps(messages), encoding="utf-8")
        (task / "task_metadata.json").write_text(
            json.dumps({"model_usage": model_usage}), encoding="utf-8"
        )
        return ui_path

    def cline_message(self, timestamp, index, usage=None, model_info=None):
        payload = {"request": "omitted"}
        if usage is not None:
            payload.update(usage)
        row = {
            "type": "say",
            "say": "api_req_started",
            "ts": timestamp,
            "conversationHistoryIndex": index,
            "text": json.dumps(payload),
        }
        if model_info is not None:
            row["modelInfo"] = model_info
        return row

    def usage_rows(
        self,
        session_id="session-1",
        message_id="message-1",
        usage=None,
        timestamp=1_788_000_000_000,
    ):
        usage = usage or {
            "inputTokens": 100,
            "outputTokens": 20,
            "cacheReadTokens": 30,
            "reasoningTokens": 15,
        }
        message = {
            "type": "assistant/message",
            "time": timestamp,
            "data": {
                "usage": usage,
                "message": {
                    "id": message_id,
                    "role": "assistant",
                    "source": {
                        "provider": "deepseek-official",
                        "model": "deepseek-test",
                    },
                },
            },
        }
        chunk = {
            "type": "assistant/chunk",
            "time": timestamp,
            "data": {"usage": usage, "chunk": {"type": "usage", "usage": usage}},
        }
        return [
            {"type": "session", "id": session_id, "createdAt": timestamp},
            chunk,
            message,
        ]

    def test_usage_total_does_not_double_count_reasoning(self):
        usage = server.token_stats_deepseek_request_usage(
            {
                "inputTokens": 100,
                "outputTokens": 20,
                "cacheReadTokens": 30,
                "reasoningTokens": 15,
            }
        )
        self.assertEqual(usage["inputTokens"], 100)
        self.assertEqual(usage["rawInputTokens"], 130)
        self.assertEqual(usage["cachedInputTokens"], 30)
        self.assertEqual(usage["outputTokens"], 20)
        self.assertEqual(usage["reasoningTokens"], 15)
        self.assertEqual(usage["totalTokens"], 150)
        self.assertFalse(usage["cacheCreationKnown"])

    def test_cache_write_coverage_distinguishes_missing_zero_and_mixed_aggregate(self):
        missing = server.token_stats_deepseek_request_usage(
            {"inputTokens": 10, "outputTokens": 2}
        )
        explicit_zero = server.token_stats_deepseek_request_usage(
            {"inputTokens": 20, "outputTokens": 3, "cacheWriteTokens": 0}
        )
        self.assertFalse(missing["cacheCreationKnown"])
        self.assertTrue(explicit_zero["cacheCreationKnown"])
        self.assertEqual(explicit_zero["cacheWriteTokens"], 0)

        rows = []
        for index, usage in enumerate((missing, explicit_zero), start=1):
            rows.append(
                {
                    "id": str(index),
                    "provider": "DeepSeek client",
                    "model": "deepseek-test",
                    "source": "deepseek",
                    "sessionId": "session-1",
                    "priceKnown": False,
                    "costUsd": 0,
                    **usage,
                }
            )
        metrics = server.token_stats_request_metrics(rows)
        provider = server.token_stats_group_requests(rows, "provider")[0]
        model = server.token_stats_group_models(rows)[0]

        for aggregate in (metrics, provider, model):
            self.assertEqual(aggregate["cacheCreationCoverage"], "partial")
            self.assertEqual(aggregate["cacheCreationKnownRequestCount"], 1)
            self.assertEqual(aggregate["cacheCreationUnknownRequestCount"], 1)
            self.assertEqual(aggregate["cacheWriteTokens"], 0)

    def test_codex_cumulative_delta_preserves_missing_cache_write_field(self):
        delta = server.token_stats_usage_delta(
            {"input_tokens": 12, "output_tokens": 3, "total_tokens": 15}, None
        )
        usage = server.token_stats_codex_request_usage(delta)

        self.assertNotIn("cache_write_input_tokens", delta)
        self.assertNotIn("cache_creation_input_tokens", delta)
        self.assertFalse(usage["cacheCreationKnown"])

    def test_codex_cache_write_placeholder_is_not_treated_as_reported(self):
        usage = server.token_stats_codex_request_usage(
            {
                "input_tokens": 12,
                "cached_input_tokens": 2,
                "cache_write_input_tokens": 0,
                "output_tokens": 3,
                "total_tokens": 15,
            }
        )

        self.assertEqual(usage["cacheWriteTokens"], 0)
        self.assertFalse(usage["cacheCreationKnown"])

    def test_summary_reads_only_final_message_and_converts_epoch_ms(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            path = self.write_session(home, "one", self.usage_rows())
            with patch.object(server, "TOKEN_STATS_DEEPSEEK_HOME", home):
                summary = server.token_stats_build_deepseek()
                rows, coverage = server.token_stats_build_deepseek_request_rows(
                    [self.descriptor(path)], None
                )

        self.assertEqual(summary["canonicalTotal"], 150)
        self.assertEqual(summary["coverage"]["requestRows"], 1)
        active_day = next(row for row in summary["days"] if row["tokens"])
        self.assertEqual(active_day["cacheCreationCoverage"], "none")
        self.assertEqual(active_day["cacheCreationKnownRequestCount"], 0)
        self.assertEqual(active_day["cacheCreationUnknownRequestCount"], 1)
        self.assertEqual(rows[0]["totalTokens"], 150)
        self.assertEqual(rows[0]["provider"], "deepseek-official")
        self.assertEqual(rows[0]["model"], "deepseek-test")
        self.assertTrue(rows[0]["timestamp"].endswith("+08:00"))
        self.assertEqual(coverage["usageCandidates"], 1)

    def test_epoch_ms_respects_plus_eight_day_boundary(self):
        before_midnight = int(
            datetime(2026, 9, 11, 15, 59, tzinfo=timezone.utc).timestamp() * 1000
        )
        after_midnight = int(
            datetime(2026, 9, 11, 16, 0, tzinfo=timezone.utc).timestamp() * 1000
        )
        first = self.usage_rows(message_id="before", timestamp=before_midnight)
        second = self.usage_rows(message_id="after", timestamp=after_midnight)[1:]
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            path = self.write_session(home, "boundary", first + second)
            rows, _coverage = server.token_stats_build_deepseek_request_rows(
                [self.descriptor(path)], None
            )

        self.assertEqual(sorted(row["date"] for row in rows), ["2026-09-11", "2026-09-12"])

    def test_duplicate_session_message_is_counted_once_across_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self.write_session(
                home,
                "one",
                self.usage_rows(usage={"inputTokens": 10, "outputTokens": 5}),
            )
            self.write_session(
                home,
                "two",
                self.usage_rows(usage={"inputTokens": 20, "outputTokens": 5}),
            )
            with patch.object(server, "TOKEN_STATS_DEEPSEEK_HOME", home):
                summary = server.token_stats_build_deepseek()

        self.assertEqual(summary["canonicalTotal"], 25)
        self.assertEqual(summary["coverage"]["requestRows"], 1)
        self.assertEqual(summary["coverage"]["duplicateUsageRows"], 1)

    def test_v3_session_file_is_counted_when_legacy_file_is_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            path = self.write_session(
                home,
                "v3-only",
                self.usage_rows(),
                filename="session.v3.jsonl.zstd",
            )
            with patch.multiple(
                server,
                TOKEN_STATS_DEEPSEEK_HOME=home,
                CODEX_HOME=home / "codex",
                TOKEN_STATS_CODEX_CONFIG=home / "codex" / "config.toml",
                CHAT_HISTORY_CLAUDE_HOME=home / "claude",
                TOKEN_STATS_WORKBUDDY_HOME=home / "workbuddy",
                TOKEN_STATS_ANTIGRAVITY_HOME=home / "antigravity",
                TOKEN_STATS_CLINE_HOME=home / "cline",
                TOKEN_STATS_CLINE_CHINESE_HOME=home / "cline-chinese",
            ):
                summary = server.token_stats_build_deepseek()
                manifest = server.token_stats_details_manifest()

        self.assertEqual(summary["canonicalTotal"], 150)
        self.assertEqual(summary["coverage"]["requestRows"], 1)
        self.assertEqual(summary["coverage"]["zstdFiles"], 1)
        self.assertEqual(
            [Path(item["path"]).name for item in manifest["files"]["deepseek"]],
            [path.name],
        )
        self.assertEqual(manifest["files"]["deepseek"][0]["path"], str(path.resolve()))
        for source, descriptors in manifest["files"].items():
            if source != "deepseek":
                self.assertEqual(descriptors, [], source)

    def test_legacy_and_v3_overlap_is_deduplicated_by_session_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self.write_session(
                home,
                "both",
                self.usage_rows(usage={"inputTokens": 10, "outputTokens": 5}),
            )
            self.write_session(
                home,
                "both",
                self.usage_rows(usage={"inputTokens": 20, "outputTokens": 5}),
                filename="session.v3.jsonl.zstd",
            )
            with patch.object(server, "TOKEN_STATS_DEEPSEEK_HOME", home):
                summary = server.token_stats_build_deepseek()

        self.assertEqual(summary["canonicalTotal"], 25)
        self.assertEqual(summary["coverage"]["zstdFiles"], 2)
        self.assertEqual(summary["coverage"]["requestRows"], 1)
        self.assertEqual(summary["coverage"]["duplicateUsageRows"], 1)

    def test_legacy_and_v3_keep_each_files_unique_requests(self):
        common_legacy = self.usage_rows(
            message_id="common", usage={"inputTokens": 10, "outputTokens": 5}
        )
        legacy_unique = self.usage_rows(
            message_id="legacy-only", usage={"inputTokens": 30, "outputTokens": 5}
        )[1:]
        common_v3 = self.usage_rows(
            message_id="common", usage={"inputTokens": 20, "outputTokens": 5}
        )
        v3_unique = self.usage_rows(
            message_id="v3-only", usage={"inputTokens": 40, "outputTokens": 5}
        )[1:]
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            legacy_path = self.write_session(home, "both", common_legacy + legacy_unique)
            v3_path = self.write_session(
                home,
                "both",
                common_v3 + v3_unique,
                filename="session.v3.jsonl.zstd",
            )
            with patch.object(server, "TOKEN_STATS_DEEPSEEK_HOME", home):
                summary = server.token_stats_build_deepseek()
                rows, coverage = server.token_stats_build_deepseek_request_rows(
                    [self.descriptor(legacy_path), self.descriptor(v3_path)], None
                )

        self.assertEqual(summary["canonicalTotal"], 105)
        self.assertEqual(len(rows), 3)
        self.assertEqual(sum(row["totalTokens"] for row in rows), 105)
        self.assertEqual(coverage["duplicateUsageRows"], 1)

    def test_corrupt_file_does_not_discard_valid_deepseek_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self.write_session(home, "good", self.usage_rows())
            bad = home / "sessions" / "bad" / "session.jsonl.zstd"
            bad.parent.mkdir(parents=True)
            bad.write_bytes(b"not-zstandard")
            with patch.object(server, "TOKEN_STATS_DEEPSEEK_HOME", home):
                summary = server.token_stats_build_deepseek()

        self.assertEqual(summary["canonicalTotal"], 150)
        self.assertEqual(summary["coverage"]["filesFailed"], 1)
        self.assertEqual(len(summary["coverage"]["fileErrors"]), 1)

    def test_missing_zstandard_returns_empty_with_coverage_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            path = self.write_session(home, "one", self.usage_rows())
            with (
                patch.object(server, "TOKEN_STATS_DEEPSEEK_HOME", home),
                patch.object(server, "zstandard", None),
            ):
                summary = server.token_stats_build_deepseek()
                rows, coverage = server.token_stats_build_deepseek_request_rows(
                    [self.descriptor(path)], None
                )

        self.assertEqual(summary["canonicalTotal"], 0)
        self.assertFalse(summary["coverage"]["dependencyAvailable"])
        self.assertIn("zstandard", summary["coverage"]["dependencyError"])
        self.assertEqual(rows, [])
        self.assertIn("zstandard", coverage["dependencyError"])

    def test_details_accepts_deepseek_source_filter(self):
        row = {
            "id": "one",
            "timestamp": "2026-09-12T08:00:00+08:00",
            "timestampUtc": "2026-09-12T00:00:00Z",
            "date": "2026-09-12",
            "provider": "deepseek-official",
            "model": "deepseek-test",
            "source": "deepseek",
            "sessionId": "session-1",
            "inputTokens": 100,
            "rawInputTokens": 130,
            "outputTokens": 20,
            "cachedInputTokens": 30,
            "cacheCreationInputTokens": 0,
            "reasoningTokens": 15,
            "totalTokens": 150,
            "costUsd": 0,
            "priceKnown": False,
        }
        index = {
            "generatedAt": "2026-09-12T08:00:00+08:00",
            "requests": [row],
            "coverage": {"deepseek": {"requestRows": 1}},
        }
        with patch.object(server, "token_stats_details_index", return_value=index):
            details = server.build_token_stats_details(
                date(2026, 9, 12), date(2026, 9, 12), source="deepseek"
            )
        self.assertEqual(details["requestTotal"], 1)
        self.assertEqual(details["metrics"]["totalTokens"], 150)
        self.assertEqual(details["filterOptions"]["sources"], ["deepseek"])

    def test_cline_uses_request_usage_and_timestamped_model_events(self):
        base = 1_764_100_000_000
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self.write_cline_task(
                root,
                "same-task-id",
                [
                    self.cline_message(
                        base + 1_000,
                        1,
                        {"tokensIn": 2, "tokensOut": 3, "cacheReads": 5, "cacheWrites": 7},
                    ),
                    self.cline_message(
                        base + 3_000,
                        2,
                        {"tokensIn": 11, "tokensOut": 13, "cacheReads": 17, "cacheWrites": 19},
                    ),
                    self.cline_message(base + 4_000, 3),
                    self.cline_message(
                        base + 5_000,
                        4,
                        {"tokensIn": 0, "tokensOut": 0, "cacheReads": 0, "cacheWrites": 0},
                    ),
                ],
                [
                    {"ts": base, "model_provider_id": "deepseek", "model_id": "deepseek-chat"},
                    {"ts": base + 2_000, "model_provider_id": "deepseek", "model_id": "deepseek-reasoner"},
                ],
            )
            rows, coverage = server.token_stats_build_cline_request_rows(
                [self.cline_descriptor(path)], None
            )

        self.assertEqual(len(rows), 2)
        self.assertEqual(sum(row["totalTokens"] for row in rows), 77)
        self.assertEqual([row["model"] for row in rows], ["deepseek-chat", "deepseek-reasoner"])
        self.assertEqual({row["provider"] for row in rows}, {"Cline Chinese"})
        self.assertEqual({row["source"] for row in rows}, {"deepseek"})
        self.assertEqual({row["sessionId"] for row in rows}, {"cline:Cline Chinese:same-task-id"})
        self.assertEqual(coverage["apiRequestRows"], 4)
        self.assertEqual(coverage["apiRequestRowsWithoutUsage"], 1)
        self.assertEqual(coverage["apiRequestRowsWithZeroUsage"], 1)
        self.assertEqual(coverage["modelsFromMetadata"], 2)

    def test_cline_provider_namespace_prevents_task_id_collision(self):
        base = 1_764_100_000_000
        model_usage = [
            {"ts": base, "model_provider_id": "deepseek", "model_id": "deepseek-chat"}
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            chinese = self.write_cline_task(
                root / "chinese",
                "same-task",
                [self.cline_message(base + 1_000, 1, {"tokensOut": 10})],
                model_usage,
            )
            cline = self.write_cline_task(
                root / "cline",
                "same-task",
                [self.cline_message(base + 1_000, 1, {"tokensOut": 20})],
                model_usage,
            )
            rows, _coverage = server.token_stats_build_cline_request_rows(
                [
                    self.cline_descriptor(chinese, "Cline Chinese"),
                    self.cline_descriptor(cline, "Cline"),
                ],
                None,
            )

        self.assertEqual(len(rows), 2)
        self.assertEqual(len({row["id"] for row in rows}), 2)
        self.assertEqual(len({row["sessionId"] for row in rows}), 2)
        self.assertEqual(sum(row["totalTokens"] for row in rows), 30)

    def test_combined_rows_normalize_provider_and_cross_source_dedupe(self):
        common = {
            "timestampUtc": "2026-09-12T00:00:00Z",
            "timestamp": "2026-09-12T08:00:00+08:00",
            "date": "2026-09-12",
            "model": "deepseek-chat",
            "inputTokens": 1,
            "outputTokens": 2,
            "cachedInputTokens": 3,
            "cacheCreationInputTokens": 4,
            "totalTokens": 10,
        }
        client = {**common, "id": "client", "provider": "deepseek", "threadSource": "deepseek-client"}
        cline = {**common, "id": "cline", "provider": "Cline", "threadSource": "cline"}
        rows, duplicates = server.token_stats_combine_deepseek_rows([client], [cline])

        self.assertEqual(len(rows), 1)
        self.assertEqual(duplicates, 1)
        self.assertEqual(rows[0]["provider"], "DeepSeek client")

    def test_cross_source_dedupe_merges_known_cache_write_coverage(self):
        common = {
            "timestampUtc": "2026-09-12T00:00:00Z",
            "timestamp": "2026-09-12T08:00:00+08:00",
            "date": "2026-09-12",
            "model": "deepseek-chat",
            "inputTokens": 1,
            "outputTokens": 2,
            "cachedInputTokens": 3,
            "cacheCreationInputTokens": 0,
            "cacheWriteTokens": 0,
            "totalTokens": 6,
        }
        client = {
            **common,
            "id": "client",
            "provider": "deepseek",
            "threadSource": "deepseek-client",
            "cacheCreationKnown": False,
        }
        cline = {
            **common,
            "id": "cline",
            "provider": "Cline",
            "threadSource": "cline",
            "cacheCreationKnown": True,
            "cacheWriteKnown": True,
        }

        rows, duplicates = server.token_stats_combine_deepseek_rows([client], [cline])

        self.assertEqual(len(rows), 1)
        self.assertEqual(duplicates, 1)
        self.assertEqual(rows[0]["provider"], "DeepSeek client")
        self.assertTrue(rows[0]["cacheCreationKnown"])
        self.assertTrue(rows[0]["cacheWriteKnown"])


if __name__ == "__main__":
    unittest.main()
