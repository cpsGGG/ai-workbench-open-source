import copy
import json
import os
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

import server


class TokenStatsPerformanceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.log_path = self.root / "rollout.jsonl"
        self.cache_path = self.root / "token-stats.sqlite"
        self.cache_patch = mock.patch.object(
            server, "TOKEN_STATS_DETAILS_FILE_CACHE_DB", self.cache_path
        )
        self.state_patch = mock.patch.object(
            server,
            "token_stats_load_codex_state",
            return_value=({}, 0, []),
        )
        self.cache_patch.start()
        self.state_patch.start()

    def tearDown(self):
        db_key = os.path.normcase(os.path.abspath(os.fspath(self.cache_path)))
        with server.TOKEN_STATS_DETAILS_PAYLOAD_CACHE_LOCK:
            stale = [
                key
                for key in server.TOKEN_STATS_DETAILS_PAYLOAD_CACHE
                if key[0] == db_key
            ]
            for key in stale:
                server.TOKEN_STATS_DETAILS_PAYLOAD_CACHE.pop(key, None)
        self.state_patch.stop()
        self.cache_patch.stop()
        self.temp_dir.cleanup()

    def write_lines(self, rows, mode="w"):
        with self.log_path.open(mode, encoding="utf-8", newline="") as target:
            for row in rows:
                target.write(json.dumps(row, ensure_ascii=False) + "\n")

    def descriptor(self):
        stat = self.log_path.stat()
        resolved = self.log_path.resolve()
        return {
            "path": str(resolved),
            "pathKey": os.path.normcase(str(resolved)),
            "size": stat.st_size,
            "mtimeNs": stat.st_mtime_ns,
            "contextKey": "test-context",
        }

    @staticmethod
    def session_rows(total=10):
        return [
            {
                "timestamp": "2026-09-28T01:00:00Z",
                "type": "session_meta",
                "payload": {"id": "session-1"},
            },
            {
                "timestamp": "2026-09-28T01:01:00Z",
                "type": "response_item",
                "payload": {"type": "message", "role": "user"},
            },
            {
                "timestamp": "2026-09-28T01:01:00Z",
                "type": "event_msg",
                "payload": {"type": "user_message"},
            },
            {
                "timestamp": "2026-09-28T01:02:00Z",
                "type": "response_item",
                "payload": {"type": "message", "role": "assistant"},
            },
            {
                "timestamp": "2026-09-28T01:03:00Z",
                "type": "response_item",
                "payload": {"type": "tool_call"},
            },
            {
                "timestamp": "2026-09-28T01:04:00Z",
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {"total_token_usage": {"total_tokens": total}},
                },
            },
        ]

    @staticmethod
    def detail_token_event(timestamp, cumulative, request_total):
        return {
            "timestamp": timestamp,
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {
                        "input_tokens": cumulative - 3,
                        "output_tokens": 3,
                        "total_tokens": cumulative,
                    },
                    "last_token_usage": {
                        "input_tokens": request_total - 2,
                        "output_tokens": 2,
                        "total_tokens": request_total,
                    },
                },
            },
        }

    @staticmethod
    def metric_row(row_id, timestamp, source="codex", provider="Codex (Session)"):
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        local = parsed.astimezone(server.TOKEN_STATS_TZ)
        return {
            "id": row_id,
            "timestamp": local.isoformat(),
            "timestampUtc": timestamp,
            "date": local.date().isoformat(),
            "provider": provider,
            "model": "test-model",
            "source": source,
            "sessionId": row_id,
            "project": "test",
            "inputTokens": 8,
            "rawInputTokens": 10,
            "cachedInputTokens": 2,
            "cacheCreationInputTokens": 0,
            "cacheCreationKnown": False,
            "outputTokens": 2,
            "reasoningTokens": 0,
            "totalTokens": 12,
            "costUsd": 0,
            "priceKnown": False,
        }

    def test_codex_summary_cache_hits_appends_and_rebuilds_rewrites(self):
        self.write_lines(self.session_rows())
        first = server.token_stats_build_codex([self.descriptor()])
        self.assertEqual(first["canonicalTotal"], 10)
        self.assertEqual(first["coverage"]["filesParsed"], 1)
        self.assertEqual(first["coverage"]["fileCacheHits"], 0)
        day = next(item for item in first["days"] if item["tokens"])
        self.assertEqual(day["userMessages"], 1)
        self.assertEqual(day["assistantMessages"], 1)
        self.assertEqual(day["toolCalls"], 1)

        unchanged = server.token_stats_build_codex([self.descriptor()])
        self.assertEqual(unchanged["canonicalTotal"], 10)
        self.assertEqual(unchanged["coverage"]["fileCacheHits"], 1)
        self.assertEqual(unchanged["coverage"]["filesParsed"], 0)

        self.write_lines(
            [
                {
                    "timestamp": "2026-09-28T01:05:00Z",
                    "type": "event_msg",
                    "payload": {"type": "user_message"},
                },
                {
                    "timestamp": "2026-09-28T01:06:00Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {"total_token_usage": {"total_tokens": 16}},
                    },
                },
            ],
            mode="a",
        )
        appended = server.token_stats_build_codex([self.descriptor()])
        self.assertEqual(appended["canonicalTotal"], 16)
        self.assertEqual(appended["coverage"]["filesParsed"], 1)
        self.assertEqual(appended["coverage"]["filesParsedIncrementally"], 1)
        self.assertLess(appended["coverage"]["bytesParsed"], self.log_path.stat().st_size)
        day = next(item for item in appended["days"] if item["tokens"])
        self.assertEqual(day["userMessages"], 2)

        self.write_lines(self.session_rows(total=4))
        rewritten = server.token_stats_build_codex([self.descriptor()])
        self.assertEqual(rewritten["canonicalTotal"], 4)
        self.assertEqual(rewritten["coverage"]["filesParsed"], 1)
        self.assertEqual(rewritten["coverage"]["filesParsedIncrementally"], 0)

    def test_force_refresh_keeps_unchanged_file_cache(self):
        self.write_lines(self.session_rows())
        descriptor = self.descriptor()
        manifest = {
            "files": {
                "codex": [descriptor],
                "deepseek": [],
                "deepseekCline": [],
                "workbuddy": [],
                "antigravity": [],
            }
        }
        empty_summary = {"days": [], "coverage": {}}
        patches = [
            mock.patch.object(server, "token_stats_details_manifest", return_value=manifest),
            mock.patch.object(server, "token_stats_build_claude", return_value=empty_summary),
            mock.patch.object(
                server, "token_stats_build_deepseek_combined", return_value=empty_summary
            ),
            mock.patch.object(server, "token_stats_build_workbuddy", return_value=empty_summary),
            mock.patch.object(
                server, "token_stats_build_antigravity", return_value=empty_summary
            ),
        ]
        old_cache = server.TOKEN_STATS_CACHE
        old_built_at = server.TOKEN_STATS_CACHE_BUILT_AT
        try:
            for patch in patches:
                patch.start()
            server.TOKEN_STATS_CACHE = None
            server.TOKEN_STATS_CACHE_BUILT_AT = 0.0
            first = server.build_token_stats(force=True)
            second = server.build_token_stats(force=True)
        finally:
            for patch in reversed(patches):
                patch.stop()
            server.TOKEN_STATS_CACHE = old_cache
            server.TOKEN_STATS_CACHE_BUILT_AT = old_built_at

        self.assertEqual(first["codex"]["coverage"]["filesParsed"], 1)
        self.assertEqual(second["codex"]["coverage"]["filesParsed"], 0)
        self.assertEqual(second["codex"]["coverage"]["fileCacheHits"], 1)

    def test_complete_json_tail_without_newline_is_counted(self):
        rows = self.session_rows(total=13)
        self.log_path.write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in rows),
            encoding="utf-8",
        )
        result = server.token_stats_build_codex([self.descriptor()])
        self.assertEqual(result["canonicalTotal"], 13)
        self.assertEqual(result["coverage"]["lines"], len(rows))
        self.assertEqual(result["coverage"]["badLines"], 0)

    def test_non_activity_rows_do_not_extend_calendar(self):
        self.write_lines(
            [
                {
                    "timestamp": "2020-01-01T01:00:00Z",
                    "type": "session_meta",
                    "payload": {"id": "session-only"},
                }
            ]
        )
        coverage = server.token_stats_codex_summary_file_coverage()
        payload = server.token_stats_codex_summary_file_incremental(
            self.log_path,
            self.descriptor(),
            coverage,
            None,
        )
        self.assertEqual(payload["contribution"]["rawDays"], {})

    def test_parse_failure_is_visible_and_tier_context_does_not_invalidate(self):
        self.write_lines(self.session_rows())
        descriptor = self.descriptor()
        server.token_stats_build_codex([descriptor])
        changed_context = {**descriptor, "contextKey": "different-tier-config"}
        cached = server.token_stats_build_codex([changed_context])
        self.assertEqual(cached["coverage"]["fileCacheHits"], 1)

        missing = {
            **descriptor,
            "path": str(self.root / "missing.jsonl"),
            "pathKey": os.path.normcase(str(self.root / "missing.jsonl")),
        }
        failed = server.token_stats_build_codex([missing])
        self.assertEqual(failed["coverage"]["filesFailed"], 1)
        self.assertEqual(len(failed["coverage"]["fileErrors"]), 1)

    def test_payload_memory_cache_updates_and_supports_concurrent_reads(self):
        self.write_lines(self.session_rows())
        descriptor = self.descriptor()
        first_payload = {"signature": {"size": 1, "mtimeNs": 1}, "rows": [{"id": 1}]}
        second_payload = {"signature": {"size": 2, "mtimeNs": 2}, "rows": [{"id": 2}]}
        connection = server.token_stats_details_cache_connection()
        try:
            server.token_stats_details_cache_save(
                connection, "memory-test", descriptor, first_payload
            )
            connection.commit()
            self.assertIs(
                server.token_stats_details_cache_load(
                    connection, "memory-test", descriptor["pathKey"]
                ),
                first_payload,
            )
            server.token_stats_details_cache_save(
                connection, "memory-test", descriptor, second_payload
            )
            connection.commit()
        finally:
            connection.close()

        def load_from_thread(_index):
            thread_connection = server.token_stats_details_cache_connection()
            try:
                return server.token_stats_details_cache_load(
                    thread_connection, "memory-test", descriptor["pathKey"]
                )
            finally:
                thread_connection.close()

        with ThreadPoolExecutor(max_workers=8) as executor:
            loaded = list(executor.map(load_from_thread, range(32)))
        self.assertTrue(all(payload is second_payload for payload in loaded))

    def test_changed_detail_file_uses_cached_payload_without_mutating_old_value(self):
        self.write_lines(
            [
                {
                    "timestamp": "2026-09-28T01:00:00Z",
                    "type": "session_meta",
                    "payload": {"id": "session-1"},
                },
                {
                    "timestamp": "2026-09-28T01:00:30Z",
                    "type": "turn_context",
                    "payload": {"model": "test-model"},
                },
                self.detail_token_event("2026-09-28T01:01:00Z", 10, 10),
            ]
        )
        first_descriptor = self.descriptor()
        connection = server.token_stats_details_cache_connection()
        try:
            first_rows, _coverage = server.token_stats_build_codex_request_rows(
                [first_descriptor], connection
            )
            connection.commit()
            cached_before = server.token_stats_details_cache_load(
                connection, "codex", first_descriptor["pathKey"]
            )
            snapshot = copy.deepcopy(cached_before)
            self.assertEqual(len(first_rows), 1)

            self.write_lines(
                [self.detail_token_event("2026-09-28T01:02:00Z", 16, 6)],
                mode="a",
            )
            second_rows, coverage = server.token_stats_build_codex_request_rows(
                [self.descriptor()], connection
            )
            connection.commit()
            cached_after = server.token_stats_details_cache_load(
                connection, "codex", first_descriptor["pathKey"]
            )
            cached_after_snapshot = copy.deepcopy(cached_after)
            third_rows, third_coverage = server.token_stats_build_codex_request_rows(
                [self.descriptor()], connection
            )
        finally:
            connection.close()

        self.assertEqual(len(second_rows), 2)
        self.assertEqual(coverage["filesParsedIncrementally"], 1)
        self.assertEqual(cached_before, snapshot)
        self.assertEqual(third_rows, second_rows)
        self.assertEqual(third_coverage["fileCacheHits"], 1)
        self.assertEqual(cached_after, cached_after_snapshot)

    def test_date_source_index_preserves_details_response_and_order(self):
        rows = [
            self.metric_row("codex-end", "2026-09-29T08:30:00Z"),
            self.metric_row("codex-new", "2026-09-29T08:00:00Z"),
            self.metric_row(
                "workbuddy-new",
                "2026-09-29T07:00:00Z",
                source="workbuddy",
                provider="WorkBuddy",
            ),
            self.metric_row("codex-old", "2026-09-28T08:00:00Z"),
        ]
        rows[2]["model"] = "workbuddy-model"
        untimestamped = self.metric_row("codex-untimestamped", "2026-09-29T06:00:00Z")
        untimestamped["timestamp"] = None
        untimestamped["timestampUtc"] = None
        rows.append(untimestamped)
        rows.sort(
            key=lambda item: (item.get("timestampUtc") or "", item["id"]),
            reverse=True,
        )
        legacy = {
            "activities": [{"sessionId": "legacy-session", "date": "2026-09-29"}],
            "tokenBuckets": [
                {"date": "2026-09-29", "model": "legacy-model", "tokens": 99}
            ],
        }
        base_index = {
            "generatedAt": "2026-09-29T12:00:00+08:00",
            "requests": rows,
            "legacyAggregates": {"claude": legacy},
            "coverage": {},
        }
        indexed = {
            **base_index,
            "_requestsBySourceDate": server.token_stats_index_requests_by_source_date(rows),
            "_requestAvailableStart": "2026-09-28",
            "_requestAvailableEnd": "2026-09-29",
        }

        def assert_equivalent(**kwargs):
            with mock.patch.object(
                server, "token_stats_details_index", return_value=base_index
            ):
                expected = server.build_token_stats_details(
                    date(2026, 9, 28),
                    date(2026, 9, 29),
                    limit=20,
                    **kwargs,
                )
            with mock.patch.object(
                server, "token_stats_details_index", return_value=indexed
            ):
                actual = server.build_token_stats_details(
                    date(2026, 9, 28),
                    date(2026, 9, 29),
                    limit=20,
                    **kwargs,
                )
            self.assertEqual(actual, expected)

        for filters in (
            {"source": "all"},
            {
                "source": "codex",
                "provider": "Codex (Session)",
                "model": "test-model",
            },
            {
                "source": "workbuddy",
                "provider": "WorkBuddy",
                "model": "workbuddy-model",
            },
            {
                "source": "claude",
                "provider": "Claude Code",
                "model": "legacy-model",
            },
        ):
            assert_equivalent(**filters)

        exact_start = datetime(2026, 9, 29, 15, 0, tzinfo=server.TOKEN_STATS_TZ)
        exact_end = datetime(2026, 9, 29, 16, 30, tzinfo=server.TOKEN_STATS_TZ)
        for filters in (
            {"source": "all"},
            {
                "source": "codex",
                "provider": "Codex (Session)",
                "model": "test-model",
            },
            {
                "source": "workbuddy",
                "provider": "WorkBuddy",
                "model": "workbuddy-model",
            },
            {
                "source": "claude",
                "provider": "Claude Code",
                "model": "legacy-model",
            },
        ):
            assert_equivalent(
                start_time=exact_start,
                end_time=exact_end,
                **filters,
            )

    def test_deepseek_combination_does_not_mutate_cached_rows(self):
        client = self.metric_row(
            "client", "2026-09-29T08:00:00Z", source="deepseek", provider="deepseek"
        )
        client["threadSource"] = "deepseek-client"
        cline = {**client, "id": "cline", "provider": "Cline", "threadSource": "cline"}
        cline["cacheCreationKnown"] = True
        snapshot = copy.deepcopy(client)
        first, _duplicates = server.token_stats_combine_deepseek_rows([client], [cline])
        second, _duplicates = server.token_stats_combine_deepseek_rows([client], [cline])
        self.assertEqual(client, snapshot)
        self.assertEqual(first, second)

    def test_details_releases_sqlite_writer_before_workbuddy_lock(self):
        first_record = {
            "id": "workbuddy-first",
            "timestamp": 1_700_000_000_000,
            "sessionId": "workbuddy-session",
            "cwd": str(self.root),
            "providerData": {
                "messageId": "message-first",
                "model": "test-model",
                "usage": {"inputTokens": 10, "outputTokens": 2, "totalTokens": 12},
            },
        }
        second_record = {
            **first_record,
            "id": "workbuddy-second",
            "timestamp": 1_700_000_001_000,
            "providerData": {
                **first_record["providerData"],
                "messageId": "message-second",
            },
        }
        self.write_lines([first_record])
        initial_descriptor = {
            key: value
            for key, value in self.descriptor().items()
            if key != "contextKey"
        }
        seed_connection = server.token_stats_details_cache_connection()
        try:
            seed_rows, _coverage = server.token_stats_build_workbuddy_request_rows(
                [initial_descriptor], seed_connection
            )
            seed_connection.commit()
        finally:
            seed_connection.close()
        self.assertEqual(len(seed_rows), 1)

        self.write_lines([second_record], mode="a")
        changed_descriptor = {
            key: value
            for key, value in self.descriptor().items()
            if key != "contextKey"
        }
        manifest = {
            "files": {
                "codex": [changed_descriptor],
                "claude": [],
                "deepseek": [],
                "deepseekCline": [],
                "workbuddy": [changed_descriptor],
                "antigravity": [],
            },
            "claudeRoot": self.root,
        }
        details_writer_started = threading.Event()
        summary_holds_workbuddy = threading.Event()
        original_parse = server.token_stats_parse_workbuddy_segment

        def codex_builder(_descriptors, connection):
            server.token_stats_details_cache_save(
                connection,
                "lock-probe",
                changed_descriptor,
                {
                    "signature": {
                        "size": changed_descriptor["size"],
                        "mtimeNs": changed_descriptor["mtimeNs"],
                    },
                    "rows": [],
                },
            )
            details_writer_started.set()
            self.assertTrue(summary_holds_workbuddy.wait(2))
            return [], server.token_stats_details_source_coverage("codex")

        def parse_workbuddy(*args, **kwargs):
            if threading.current_thread().name == "workbuddy-summary":
                summary_holds_workbuddy.set()
            return original_parse(*args, **kwargs)

        claude_coverage = server.token_stats_details_source_coverage("claude")
        claude_coverage.update(
            {
                "_legacyActivities": [],
                "_legacyTokenBuckets": [],
                "_legacyDailyActivity": {},
                "_authoritativeActivities": [],
            }
        )
        results = {}
        errors = []

        def build_details():
            try:
                results["details"] = server.token_stats_build_details_index_uncached(
                    manifest
                )
            except Exception as exc:
                errors.append(exc)

        def build_summary():
            try:
                results["summary"] = server.token_stats_build_workbuddy(
                    [changed_descriptor]
                )
            except Exception as exc:
                errors.append(exc)

        patches = (
            mock.patch.object(
                server, "token_stats_build_codex_request_rows", side_effect=codex_builder
            ),
            mock.patch.object(
                server,
                "token_stats_build_claude_request_rows",
                return_value=([], claude_coverage),
            ),
            mock.patch.object(
                server,
                "token_stats_build_deepseek_request_rows",
                return_value=([], server.token_stats_details_source_coverage("deepseek")),
            ),
            mock.patch.object(
                server,
                "token_stats_build_cline_request_rows",
                return_value=([], server.token_stats_details_source_coverage("deepseek")),
            ),
            mock.patch.object(
                server,
                "token_stats_build_antigravity_request_rows",
                return_value=([], server.token_stats_details_source_coverage("antigravity")),
            ),
            mock.patch.object(
                server, "token_stats_parse_workbuddy_segment", side_effect=parse_workbuddy
            ),
        )
        started_at = time.monotonic()
        try:
            for patcher in patches:
                patcher.start()
            details_thread = threading.Thread(target=build_details, name="details-index")
            details_thread.start()
            self.assertTrue(details_writer_started.wait(2))
            summary_thread = threading.Thread(target=build_summary, name="workbuddy-summary")
            summary_thread.start()
            details_thread.join(3)
            summary_thread.join(3)
        finally:
            for patcher in reversed(patches):
                patcher.stop()
        elapsed = time.monotonic() - started_at

        self.assertFalse(details_thread.is_alive())
        self.assertFalse(summary_thread.is_alive())
        self.assertEqual(errors, [])
        self.assertLess(elapsed, 3)
        self.assertEqual(results["summary"]["coverage"]["filesAppended"], 1)
        self.assertEqual(results["summary"]["coverage"]["requestRows"], 2)
        self.assertEqual(results["details"]["coverage"]["workbuddy"]["fileCacheHits"], 1)

        db_key = os.path.normcase(os.path.abspath(os.fspath(self.cache_path)))
        with server.TOKEN_STATS_DETAILS_PAYLOAD_CACHE_LOCK:
            for key in list(server.TOKEN_STATS_DETAILS_PAYLOAD_CACHE):
                if key[0] == db_key:
                    server.TOKEN_STATS_DETAILS_PAYLOAD_CACHE.pop(key, None)
        verify_connection = server.token_stats_details_cache_connection()
        try:
            workbuddy_payload = server.token_stats_details_cache_load(
                verify_connection, "workbuddy", changed_descriptor["pathKey"]
            )
            lock_payload = server.token_stats_details_cache_load(
                verify_connection, "lock-probe", changed_descriptor["pathKey"]
            )
        finally:
            verify_connection.close()
        self.assertEqual(len(workbuddy_payload["rows"]), 2)
        self.assertIsNotNone(lock_payload)

    def test_antigravity_wal_change_invalidates_manifest_and_details_index(self):
        db_path = self.root / "conversation.db"
        db_path.write_bytes(b"stable-main-database")
        main_stat = db_path.stat()
        empty_files = mock.patch.object(server, "chat_history_jsonl_files", return_value=[])
        patches = (
            mock.patch.object(
                server,
                "token_stats_codex_config_tier",
                return_value={
                    "tier": "standard",
                    "requestedTier": "unknown",
                    "source": "test",
                },
            ),
            empty_files,
            mock.patch.object(server, "token_stats_claude_legacy_files", return_value=[]),
            mock.patch.object(server, "token_stats_deepseek_files", return_value=[]),
            mock.patch.object(server, "token_stats_workbuddy_files", return_value=[]),
            mock.patch.object(
                server, "token_stats_antigravity_files", return_value=[db_path]
            ),
            mock.patch.object(server, "token_stats_cline_task_files", return_value=[]),
        )
        old_cache = server.TOKEN_STATS_DETAILS_CACHE
        old_signature = server.TOKEN_STATS_DETAILS_CACHE_SIGNATURE
        old_built_at = server.TOKEN_STATS_DETAILS_CACHE_BUILT_AT
        old_revision = server.TOKEN_STATS_DETAILS_CACHE_REVISION
        try:
            for patcher in patches:
                patcher.start()
            before = server.token_stats_details_manifest()
            before_descriptor = before["files"]["antigravity"][0]
            self.assertEqual(before_descriptor["contextKey"], "wal:missing")

            Path(f"{db_path}-wal").write_bytes(b"new committed pages in wal")
            after = server.token_stats_details_manifest()
            after_descriptor = after["files"]["antigravity"][0]
            self.assertEqual(db_path.stat().st_size, main_stat.st_size)
            self.assertEqual(db_path.stat().st_mtime_ns, main_stat.st_mtime_ns)
            self.assertNotEqual(after_descriptor["contextKey"], before_descriptor["contextKey"])
            self.assertNotEqual(after["signature"], before["signature"])
            stale_payload = {
                "signature": {
                    "size": before_descriptor["size"],
                    "mtimeNs": before_descriptor["mtimeNs"],
                    "contextKey": before_descriptor["contextKey"],
                },
                "rows": [],
            }
            self.assertFalse(server.token_stats_cache_is_exact(stale_payload, after_descriptor))

            stale_index = {
                "generatedAt": "before-wal",
                "requests": [],
                "legacyAggregates": {"claude": {}},
                "coverage": {},
            }
            rebuilt_index = {**stale_index, "generatedAt": "after-wal"}
            server.TOKEN_STATS_DETAILS_CACHE = stale_index
            server.TOKEN_STATS_DETAILS_CACHE_SIGNATURE = before["signature"]
            server.TOKEN_STATS_DETAILS_CACHE_BUILT_AT = 1.0
            with mock.patch.object(
                server,
                "token_stats_build_details_index_uncached",
                return_value=rebuilt_index,
            ) as rebuild:
                result = server.token_stats_details_index()
            self.assertIs(result, rebuilt_index)
            rebuild.assert_called_once()
            self.assertEqual(
                rebuild.call_args.args[0]["files"]["antigravity"][0]["contextKey"],
                after_descriptor["contextKey"],
            )
        finally:
            for patcher in reversed(patches):
                patcher.stop()
            server.TOKEN_STATS_DETAILS_CACHE = old_cache
            server.TOKEN_STATS_DETAILS_CACHE_SIGNATURE = old_signature
            server.TOKEN_STATS_DETAILS_CACHE_BUILT_AT = old_built_at
            server.TOKEN_STATS_DETAILS_CACHE_REVISION = old_revision

    def test_details_index_stale_manifest_cannot_overwrite_newer_cache(self):
        stale_manifest = {"signature": "stale", "files": {}}
        fresh_manifest = {"signature": "fresh", "files": {}}
        stale_manifest_started = threading.Event()
        fresh_build_finished = threading.Event()
        stale_manifest_calls = 0
        built_signatures = []

        def build_manifest():
            nonlocal stale_manifest_calls
            if threading.current_thread().name == "stale-index":
                stale_manifest_calls += 1
                if stale_manifest_calls == 1:
                    stale_manifest_started.set()
                    self.assertTrue(fresh_build_finished.wait(2))
                    return stale_manifest
            return fresh_manifest

        def build_index(manifest):
            built_signatures.append(manifest["signature"])
            return {
                "generatedAt": manifest["signature"],
                "requests": [],
                "legacyAggregates": {"claude": {}},
                "coverage": {},
            }

        old_cache = server.TOKEN_STATS_DETAILS_CACHE
        old_signature = server.TOKEN_STATS_DETAILS_CACHE_SIGNATURE
        old_built_at = server.TOKEN_STATS_DETAILS_CACHE_BUILT_AT
        old_revision = server.TOKEN_STATS_DETAILS_CACHE_REVISION
        try:
            server.TOKEN_STATS_DETAILS_CACHE = None
            server.TOKEN_STATS_DETAILS_CACHE_SIGNATURE = None
            server.TOKEN_STATS_DETAILS_CACHE_BUILT_AT = 0.0
            server.TOKEN_STATS_DETAILS_CACHE_REVISION = 0
            with mock.patch.object(
                server, "token_stats_details_manifest", side_effect=build_manifest
            ), mock.patch.object(
                server, "token_stats_build_details_index_uncached", side_effect=build_index
            ):
                stale_result = []

                def build_stale():
                    stale_result.append(server.token_stats_details_index(force=True))

                stale_thread = threading.Thread(target=build_stale, name="stale-index")
                stale_thread.start()
                self.assertTrue(stale_manifest_started.wait(2))
                fresh_result = server.token_stats_details_index(force=True)
                fresh_build_finished.set()
                stale_thread.join(2)

            self.assertFalse(stale_thread.is_alive())
            self.assertEqual(fresh_result["generatedAt"], "fresh")
            self.assertEqual(stale_result[0]["generatedAt"], "fresh")
            self.assertEqual(server.TOKEN_STATS_DETAILS_CACHE_SIGNATURE, "fresh")
            self.assertEqual(server.TOKEN_STATS_DETAILS_CACHE["generatedAt"], "fresh")
            self.assertEqual(built_signatures, ["fresh", "fresh"])
            self.assertEqual(stale_manifest_calls, 2)
        finally:
            server.TOKEN_STATS_DETAILS_CACHE = old_cache
            server.TOKEN_STATS_DETAILS_CACHE_SIGNATURE = old_signature
            server.TOKEN_STATS_DETAILS_CACHE_BUILT_AT = old_built_at
            server.TOKEN_STATS_DETAILS_CACHE_REVISION = old_revision

    def test_stale_v1_summary_payload_is_rebuilt_without_empty_days(self):
        rows = [
            {
                "timestamp": "2020-01-01T01:00:00Z",
                "type": "session_meta",
                "payload": {"id": "session-1"},
            },
            {
                "timestamp": "2026-09-28T01:04:00Z",
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {"total_token_usage": {"total_tokens": 10}},
                },
            },
        ]
        self.write_lines(rows)
        descriptor = self.descriptor()
        stale_payload = {
            "signature": {
                "size": descriptor["size"],
                "mtimeNs": descriptor["mtimeNs"],
                "contextKey": descriptor["contextKey"],
            },
            "anchor": server.token_stats_file_anchor(
                self.log_path, descriptor["size"]
            ),
            "offset": descriptor["size"],
            "parserState": {"sessionId": "session-1", "previousTotal": 10},
            "contribution": {
                "sessionKey": "session-1",
                "path": descriptor["path"],
                "pathKey": descriptor["pathKey"],
                "lifetimeTotal": 10,
                "rawDays": {
                    "2020-01-01": {
                        "tokens": 0,
                        "tokenEvents": 0,
                        "eventUserMessages": 0,
                        "responseUserMessages": 0,
                        "assistantMessages": 0,
                        "toolCalls": 0,
                    },
                    "2026-09-28": {
                        "tokens": 10,
                        "tokenEvents": 1,
                        "eventUserMessages": 0,
                        "responseUserMessages": 0,
                        "assistantMessages": 0,
                        "toolCalls": 0,
                    },
                },
            },
            "coverage": server.token_stats_codex_summary_file_coverage(),
        }
        connection = server.token_stats_details_cache_connection()
        try:
            server.token_stats_details_cache_save(
                connection, "codex-summary", descriptor, stale_payload
            )
            connection.commit()
        finally:
            connection.close()

        rebuilt = server.token_stats_build_codex([descriptor])
        self.assertEqual(rebuilt["coverage"]["fileCacheHits"], 0)
        self.assertEqual(rebuilt["coverage"]["filesParsed"], 1)
        self.assertEqual(rebuilt["canonicalTotal"], 10)
        self.assertNotIn("2020-01-01", {item["date"] for item in rebuilt["days"]})

        connection = server.token_stats_details_cache_connection()
        try:
            sources = {
                row[0]
                for row in connection.execute(
                    "select distinct source from file_cache order by source"
                ).fetchall()
            }
        finally:
            connection.close()
        self.assertIn("codex-summary", sources)
        self.assertIn(server.TOKEN_STATS_CODEX_SUMMARY_CACHE_SOURCE, sources)


if __name__ == "__main__":
    unittest.main()
