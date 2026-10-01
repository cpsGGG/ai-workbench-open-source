import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOKEN_STATS_JS = ROOT / "src" / "modules" / "token-stats" / "token-stats.js"
ACTIVITY_HEATMAP_JS = ROOT / "src" / "modules" / "token-stats" / "activity-heatmap.js"
TOKEN_STATS_CSS = ROOT / "src" / "modules" / "token-stats" / "token-stats.css"


class TokenStatsFrontendTests(unittest.TestCase):
    def evaluate(self, expression: str):
        script = r"""
const fs = require("fs");
const vm = require("vm");
let source = fs.readFileSync(process.argv[1], "utf8");
source = source.replace(/^import .*;\r?\n/m, "");
source = source.replace("export function createTokenStatsWorkspace", "function createTokenStatsWorkspace");
const context = { console, Intl, URLSearchParams, AbortController, MutationObserver: undefined };
vm.createContext(context);
vm.runInContext(source, context);
const result = vm.runInContext(process.argv[2], context);
process.stdout.write(JSON.stringify(result));
"""
        completed = subprocess.run(
            ["node", "-e", script, str(TOKEN_STATS_JS), expression],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if completed.returncode != 0:
            raise RuntimeError(f"Node execution failed (code {completed.returncode}):\n{completed.stderr}")
        return json.loads(completed.stdout)

    def test_cache_write_normalization_preserves_legacy_zero_and_new_unknown(self):
        result = self.evaluate(
            """[
              normalizeRequest({ cacheCreationInputTokens: 0 }).cacheCreationCoverage,
              normalizeRequest({ cacheCreationInputTokens: 0, cacheCreationKnown: false }).cacheCreationCoverage,
              normalizeRequest({ cacheWriteTokens: 7, cacheWriteKnown: true }),
              normalizeAggregate({ provider: 'mixed', requests: 2, cacheWriteTokens: 7, cacheCreationCoverage: 'partial' }, 'provider')
            ]"""
        )

        self.assertEqual(result[0], "full")
        self.assertEqual(result[1], "none")
        self.assertEqual(result[2]["cacheCreationTokens"], 7)
        self.assertEqual(result[2]["cacheCreationCoverage"], "full")
        self.assertEqual(result[3]["cacheCreationTokens"], 7)
        self.assertEqual(result[3]["cacheCreationCoverage"], "partial")

    def test_summary_fallback_keeps_cache_write_value_and_coverage(self):
        result = self.evaluate(
            """detailsFromSummary({
              generatedAt: '2026-09-13T03:00:00+08:00',
              codex: { days: [{ date: '2026-09-13', tokens: 10, tokenEvents: 1 }] },
              claude: { days: [{
                date: '2026-09-13', tokens: 12, usageRows: 1, inputTokens: 5,
                outputTokens: 0, cacheReadTokens: 3, cacheWriteTokens: 7,
                cacheCreationKnownRequestCount: 1, cacheCreationUnknownRequestCount: 0,
                cacheCreationCoverage: 'full'
              }] },
              deepseek: { days: [{
                date: '2026-09-13', tokens: 8, usageRows: 1, inputTokens: 6,
                outputTokens: 2, cacheReadTokens: 4, cacheCreationTokens: 0,
                cacheCreationKnownRequestCount: 0, cacheCreationUnknownRequestCount: 1,
                cacheCreationCoverage: 'none'
              }] }
            }, { source: 'all', start: '2026-09-13', end: '2026-09-13' })"""
        )

        self.assertEqual(result["metrics"]["cacheCreationTokens"], 7)
        self.assertEqual(result["metrics"]["cacheCreationCoverage"], "partial")
        self.assertEqual(result["metrics"]["cacheCreationKnownRequestCount"], 1)
        self.assertEqual(result["metrics"]["cacheCreationUnknownRequestCount"], 2)
        self.assertEqual(result["metrics"]["rawInputTokens"], 25)
        self.assertEqual(result["metrics"]["cacheReadTokens"], 7)
        self.assertIsNone(result["metrics"]["cacheHitRate"])
        self.assertEqual(result["metrics"]["cacheReadCoverage"], "partial")
        self.assertEqual(result["metrics"]["cacheReadKnownRequestCount"], 2)
        self.assertEqual(result["metrics"]["cacheReadUnknownRequestCount"], 1)
        self.assertEqual(result["trend"][0]["cacheCreationCoverage"], "partial")
        providers = {row["name"]: row for row in result["providerStats"]}
        self.assertEqual(providers["Claude Code"]["cacheCreationTokens"], 7)
        self.assertEqual(providers["Claude Code"]["cacheCreationCoverage"], "full")
        self.assertEqual(providers["Codex (Session)"]["cacheCreationCoverage"], "none")
        self.assertEqual(providers["DeepSeek"]["cacheCreationCoverage"], "none")

    def test_cache_read_distinguishes_missing_explicit_zero_and_partial(self):
        result = self.evaluate(
            """(() => {
              const rows = [
                normalizeRequest({ inputTokens: 10 }),
                normalizeRequest({ cacheReadTokens: 0 }),
                normalizeRequest({ cacheReadTokens: 0, cacheReadKnown: false }),
                normalizeAggregate({ provider: 'mixed', requests: 3, cacheReadTokens: 8,
                  cacheReadKnownRequestCount: 2, cacheReadUnknownRequestCount: 1 }, 'provider'),
              ];
              const filters = { start: '2026-09-30', end: '2026-09-30' };
              const onlyMetrics = normalizeDetails({ metrics: {
                requestCount: 1, inputTokens: 10, cacheReadTokens: 0,
              } }, filters);
              const unknownMetrics = normalizeDetails({
                metrics: { requestCount: 1, inputTokens: 10, cacheReadTokens: 0,
                  cacheReadKnown: false, cacheHitRate: 0,
                  cacheCreationTokens: 0, cacheCreationKnown: false },
                requestLogs: [{ cacheReadTokens: 0, cacheCreationTokens: 0 }],
              }, filters);
              return { rows, onlyMetrics, unknownMetrics };
            })()"""
        )

        self.assertEqual([row["cacheReadCoverage"] for row in result["rows"]], ["none", "full", "none", "partial"])
        self.assertEqual(result["rows"][0]["cacheReadUnknownRequestCount"], 1)
        self.assertEqual(result["rows"][3]["cacheReadKnownRequestCount"], 2)
        self.assertEqual(result["onlyMetrics"]["metrics"]["cacheHitRate"], 0)
        self.assertEqual(result["onlyMetrics"]["metrics"]["cacheReadKnownRequestCount"], 1)
        self.assertIsNone(result["unknownMetrics"]["metrics"]["cacheHitRate"])
        self.assertEqual(result["unknownMetrics"]["metrics"]["cacheReadUnknownRequestCount"], 1)
        self.assertEqual(result["unknownMetrics"]["metrics"]["cacheCreationCoverage"], "none")

    def test_full_range_coverage_does_not_use_the_visible_request_page(self):
        result = self.evaluate(
            """normalizeDetails({
              metrics: { requestCount: 30 },
              providerStats: [{ provider: 'mixed', requests: 30, inputTokens: 300,
                cacheReadTokens: 29, cacheReadCoverage: 'partial',
                cacheReadKnownRequestCount: 29, cacheReadUnknownRequestCount: 1 }],
              requestLogs: [{ inputTokens: 10, cacheReadTokens: 1 }],
            }, { start: '2026-09-30', end: '2026-09-30' })"""
        )

        self.assertEqual(result["metrics"]["cacheReadCoverage"], "partial")
        self.assertEqual(result["metrics"]["cacheReadKnownRequestCount"], 29)
        self.assertEqual(result["metrics"]["cacheReadUnknownRequestCount"], 1)
        self.assertEqual(result["metrics"]["cacheReadTokens"], 29)
        self.assertIsNone(result["metrics"]["cacheHitRate"])

    def test_cache_display_does_not_show_unknown_or_empty_rates_as_zero(self):
        result = self.evaluate(
            """({
              unknown: cacheRateMetric(null, 0, 10, 'none', 1),
              partial: cacheRateMetric(0, 8, 20, 'partial', 3),
              zero: cacheRateMetric(0, 0, 10, 'full', 1),
              empty: cacheRateMetric(null, 0, 0, 'none', 0),
              unknownCell: cacheTotalCellMarkup(normalizeRequest({}), 1),
              partialCell: cacheTotalCellMarkup(normalizeRequest({ cacheReadTokens: 8 }), 1),
            })"""
        )

        self.assertIn("未提供", result["unknown"])
        self.assertNotIn("0.0%", result["unknown"])
        self.assertIn("部分未知", result["partial"])
        self.assertIn("已知缓存读取 8 Token", result["partial"])
        self.assertNotIn("0.0%", result["partial"])
        self.assertIn("0.0%", result["zero"])
        self.assertIn("—", result["empty"])
        self.assertNotIn("0.0%", result["empty"])
        self.assertIn("缓存读取和写入字段均未提供", result["unknownCell"])
        self.assertIn("8（已知部分）", result["partialCell"])
        self.assertIn("写入：未提供", result["partialCell"])

    def test_combined_cache_trend_keeps_known_partial_and_breaks_unknown(self):
        result = self.evaluate(
            """(() => {
              const rows = [
                { requests: 1, cacheTotalTokens: 3, cacheTotalCoverage: 'full' },
                { requests: 1, cacheTotalTokens: 0, cacheTotalCoverage: 'none' },
                { requests: 1, cacheTotalTokens: 4, cacheTotalCoverage: 'partial' },
                { requests: 1, cacheTotalTokens: 5, cacheTotalCoverage: 'full' },
                { requests: 1, cacheTotalTokens: 0, cacheTotalCoverage: 'full' },
              ];
              return coveredLinePath(rows, 'cacheTotalTokens', 'cacheTotalCoverage', (i) => i * 10, (v) => v, true);
            })()"""
        )

        self.assertEqual(result.count("M"), 2)
        self.assertIn("M0.00,3.00", result)
        self.assertIn("M20.00,4.00", result)
        self.assertIn("40.00,0.00", result)
        self.assertNotIn("10.00,0.00", result)

    def test_combined_cache_sums_known_breakdown_once_and_keeps_read_hit_rate(self):
        result = self.evaluate(
            """(() => {
              const rows = [
                { cacheReadTokens: 90, cacheCreationTokens: 10, cacheTokens: 100 },
                { cacheReadTokens: 90, cacheCreationTokens: 0, cacheCreationKnown: false },
                { cacheReadTokens: 0, cacheCreationTokens: 0 },
                { cacheReadTokens: 0, cacheCreationTokens: 0, cacheCreationKnown: false },
                { cacheReadTokens: 0, cacheReadKnown: false, cacheCreationTokens: 0, cacheCreationKnown: false },
                { cacheReadTokens: 500, cacheReadKnown: false, cacheCreationTokens: 8 },
              ].map((row) => combinedCache(normalizeRequest(row), 1));
              const details = normalizeDetails({ metrics: {
                requestCount: 1, inputTokens: 20, outputTokens: 30, totalTokens: 150,
                rawInputTokens: 120, cacheReadTokens: 90, cacheCreationTokens: 10, cacheTokens: 100,
              } }, { start: '2026-09-30', end: '2026-09-30' });
              return { rows, metrics: details.metrics, combined: combinedCache(details.metrics) };
            })()"""
        )

        self.assertEqual([row["tokens"] for row in result["rows"]], [100, 90, 0, 0, 0, 8])
        self.assertEqual([row["coverage"] for row in result["rows"]], ["full", "partial", "full", "partial", "none", "partial"])
        self.assertEqual(result["combined"]["tokens"], 100)
        self.assertEqual(result["metrics"]["totalTokens"], 150)
        self.assertEqual(result["metrics"]["cacheHitRate"], 75)

    def test_deepseek_source_note_uses_scanned_formats_and_escapes_path(self):
        result = self.evaluate(
            """({
              deepseek: sourceCoverageMarkup({ deepseek: { coverage: {
                sourceFormats: { 'session.v3.jsonl.zstd': 29, 'session.v4.jsonl.zstd': 4 },
                sessionsRoot: '<sessions>&folder',
              } } }, 'deepseek'),
              unrelated: sourceCoverageMarkup({ deepseek: { coverage: {
                sourceFormats: { 'session.v4.jsonl.zstd': 4 },
              } } }, 'codex'),
              unreadable: sourceCoverageMarkup({ deepseek: { coverage: {
                sourceFormats: { 'session.v4.jsonl.zstd': 4 },
                filesFailed: 4, dependencyError: 'missing decompressor',
              } } }, 'deepseek'),
            })"""
        )

        self.assertIn("33 份 dsh", result["deepseek"])
        self.assertIn("v4 日志 4 份", result["deepseek"])
        self.assertIn("&lt;sessions&gt;&amp;folder", result["deepseek"])
        self.assertIn("日志明确记录的 0 保留为 0", result["deepseek"])
        self.assertNotIn("已读取", result["deepseek"])
        self.assertIn("当前覆盖不完整", result["unreadable"])
        self.assertEqual(result["unrelated"], "")

    def test_legacy_token_bucket_with_zero_requests_still_has_unknown_cache(self):
        result = self.evaluate(
            """(() => {
              const legacy = normalizeTrend({ date: '2026-09-30', totalTokens: 50, requests: 0,
                cacheReadCoverage: 'none', cacheCreationCoverage: 'none' });
              const cache = combinedCache(legacy, 0);
              const empty = { requestCount: 0, totalTokens: 0, cacheReadCoverage: 'none', cacheCreationCoverage: 'none' };
              return {
                cache,
                overview: cacheOverviewMetric({ ...legacy, requestCount: 0 }),
                cell: cacheTotalCellMarkup(legacy, 0),
                title: cacheTotalTitle(legacy, cache),
                legacyPath: coveredLinePath([{ ...legacy, cacheTotalTokens: cache.tokens,
                  cacheTotalCoverage: cache.coverage }], 'cacheTotalTokens', 'cacheTotalCoverage', (i) => i, (v) => v, true),
                emptyOverview: cacheOverviewMetric(empty),
                emptyCell: cacheTotalCellMarkup(empty, 0),
              };
            })()"""
        )

        self.assertTrue(result["cache"]["hasActivity"])
        self.assertEqual(result["cache"]["requestCount"], 0)
        self.assertIn("未提供", result["overview"])
        self.assertIn("未提供", result["cell"])
        self.assertIn("读取：未提供", result["title"])
        self.assertIn("写入：未提供", result["title"])
        self.assertEqual(result["legacyPath"], "")
        self.assertNotIn("未提供", result["emptyOverview"])
        self.assertNotIn("未提供", result["emptyCell"])

    def test_quick_ranges_use_the_requested_order_and_calendar_boundaries(self):
        source = TOKEN_STATS_JS.read_text(encoding="utf-8")
        labels = ["最近24小时", "今天", "昨天", "近 7 天", "近 30 天", "全部"]
        positions = [source.index(f">{label}</button>") for label in labels]
        self.assertEqual(positions, sorted(positions))

        result = self.evaluate(
            """(() => {
              const now = new Date(2026, 8, 16, 0, 30, 0);
              return {
                last24h: quickRangeDates('24h', now),
                today: quickRangeDates('today', now),
                yesterday: quickRangeDates('yesterday', now),
                seven: quickRangeDates('7', now),
                thirty: quickRangeDates('30', now),
                nextDayToday: quickRangeDates('today', new Date(2026, 8, 17, 0, 30, 0)),
              };
            })()"""
        )

        self.assertEqual(result["last24h"], {"start": "2026-09-15", "end": "2026-09-16"})
        self.assertEqual(result["today"], {"start": "2026-09-16", "end": "2026-09-16"})
        self.assertEqual(result["yesterday"], {"start": "2026-09-15", "end": "2026-09-15"})
        self.assertEqual(result["seven"], {"start": "2026-09-10", "end": "2026-09-16"})
        self.assertEqual(result["thirty"], {"start": "2026-08-18", "end": "2026-09-16"})
        self.assertEqual(result["nextDayToday"], {"start": "2026-09-17", "end": "2026-09-17"})

    def test_last_24_hours_is_an_exact_rolling_window(self):
        result = self.evaluate(
            """(() => {
              const now = new Date('2026-09-16T00:30:00.000Z');
              const bounds = rollingRangeBounds('24h', now);
              return {
                bounds,
                duration: new Date(bounds.endTime) - new Date(bounds.startTime),
                otherPreset: rollingRangeBounds('today', now),
                label: selectedRangeLabel({ rangePreset: '24h', start: 'x', end: 'y' }),
              };
            })()"""
        )

        self.assertEqual(result["duration"], 24 * 60 * 60 * 1000)
        self.assertEqual(result["bounds"]["startTime"], "2026-09-15T00:30:00.000Z")
        self.assertEqual(result["bounds"]["endTime"], "2026-09-16T00:30:00.000Z")
        self.assertIsNone(result["otherPreset"])
        self.assertEqual(result["label"], "最近24小时")

    def test_details_render_before_background_summary_refresh(self):
        source = TOKEN_STATS_JS.read_text(encoding="utf-8")
        load_data = source[
            source.index("  async function loadData(refresh)") :
            source.index("  function ensureSummary(refresh)")
        ]

        details_fetch = load_data.index("await fetchJson(`${DETAILS_ENDPOINT}")
        summary_refresh = load_data.index("void ensureSummary(refresh)")
        self.assertLess(details_fetch, summary_refresh)
        self.assertNotIn("const pendingSummary = ensureSummary(refresh)", load_data)

    def test_antigravity_summary_normalization_and_labels(self):
        source = TOKEN_STATS_JS.read_text(encoding="utf-8")
        self.assertIn('sourceButton("antigravity", "Antigravity")', source)

        result = self.evaluate(
            """(() => {
              const summary = {
                generatedAt: '2026-09-20T10:00:00+08:00',
                antigravity: {
                  days: [{
                    date: '2026-09-20', tokens: 100, usageRows: 5, inputTokens: 40,
                    outputTokens: 20, cacheReadTokens: 40, cacheCreationTokens: 0,
                    cacheCreationKnownRequestCount: 5, cacheCreationUnknownRequestCount: 0,
                    cacheCreationCoverage: 'full'
                  }]
                }
              };
              return {
                details: detailsFromSummary(summary, { source: 'all', start: '2026-09-20', end: '2026-09-20' }),
                antigravityOnly: detailsFromSummary(summary, { source: 'antigravity', start: '2026-09-20', end: '2026-09-20' }),
                dates: availableDates(summary),
                label: sourceLabel('antigravity'),
              };
            })()"""
        )

        self.assertEqual(result["label"], "Antigravity")
        self.assertEqual(result["dates"], ["2026-09-20"])
        self.assertEqual(result["antigravityOnly"]["metrics"]["totalTokens"], 100)
        self.assertEqual(result["antigravityOnly"]["metrics"]["requestCount"], 5)
        providers = {row["name"]: row for row in result["details"]["providerStats"]}
        self.assertIn("Antigravity", providers)
        self.assertEqual(providers["Antigravity"]["totalTokens"], 100)
        self.assertEqual(providers["Antigravity"]["requests"], 5)

    def test_workbuddy_summary_normalization_labels_and_heatmap_source(self):
        source = TOKEN_STATS_JS.read_text(encoding="utf-8")
        activity_source = ACTIVITY_HEATMAP_JS.read_text(encoding="utf-8")
        css = TOKEN_STATS_CSS.read_text(encoding="utf-8")
        self.assertIn('sourceButton("workbuddy", "WorkBuddy")', source)
        self.assertIn('workbuddy: rowsFrom(root.workbuddy || root.sources?.workbuddy)', activity_source)
        self.assertIn('sourceRows.antigravity, sourceRows.workbuddy', activity_source)
        self.assertIn('grid-template-columns: repeat(6, minmax(0, 1fr));', css)
        self.assertIn('grid-template-columns: repeat(3, minmax(0, 1fr));', css)

        result = self.evaluate(
            """(() => {
              const summary = {
                generatedAt: '2026-09-28T16:00:00+08:00',
                workbuddy: {
                  days: [{
                    date: '2026-09-28', tokens: 101, usageRows: 2, inputTokens: 21,
                    outputTokens: 10, cacheReadTokens: 70, cacheCreationTokens: 0,
                    cacheCreationKnownRequestCount: 0, cacheCreationUnknownRequestCount: 2,
                    cacheCreationCoverage: 'none'
                  }]
                }
              };
              return {
                all: detailsFromSummary(summary, { source: 'all', start: '2026-09-28', end: '2026-09-28' }),
                only: detailsFromSummary(summary, { source: 'workbuddy', start: '2026-09-28', end: '2026-09-28' }),
                dates: availableDates(summary),
                label: sourceLabel('workbuddy'),
              };
            })()"""
        )

        self.assertEqual(result["label"], "WorkBuddy")
        self.assertEqual(result["dates"], ["2026-09-28"])
        self.assertEqual(result["only"]["metrics"]["totalTokens"], 101)
        self.assertEqual(result["only"]["metrics"]["requestCount"], 2)
        self.assertEqual(result["only"]["metrics"]["cacheReadTokens"], 70)
        self.assertEqual(result["only"]["metrics"]["cacheReadCoverage"], "full")
        self.assertAlmostEqual(result["only"]["metrics"]["cacheHitRate"], 70 / 91 * 100)
        providers = {row["name"]: row for row in result["all"]["providerStats"]}
        self.assertIn("WorkBuddy", providers)
        self.assertEqual(providers["WorkBuddy"]["totalTokens"], 101)
        self.assertEqual(providers["WorkBuddy"]["cacheCreationCoverage"], "none")


if __name__ == "__main__":
    unittest.main()
