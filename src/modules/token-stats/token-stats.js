import { activityHeatmapMarkup, bindActivityHeatmap } from "./activity-heatmap.js?v=20260912-log-scale-v1";

const SUMMARY_ENDPOINT = "/api/token-stats/summary";
const DETAILS_ENDPOINT = "/api/token-stats/details";
const FILTER_STORAGE_KEY = "ai-workbench:token-stats-filters:v1";
const TABLE_PAGE_SIZE = 20;
const QUICK_RANGE_PRESETS = ["24h", "today", "yesterday", "7", "30", "all"];

export function createTokenStatsWorkspace(root) {
  if (!root) return;

  root.classList.add("tokenStatsWorkspace");

  const saved = loadFilters();
  const now = new Date();
  const today = localDateKey(now);
  const rangePreset = QUICK_RANGE_PRESETS.includes(saved.rangePreset) ? saved.rangePreset : "";
  const initialPresetDates = rangePreset && rangePreset !== "all"
    ? quickRangeDates(rangePreset, now)
    : null;
  const state = {
    started: false,
    loading: false,
    error: "",
    notice: "",
    source: ["all", "codex", "claude", "deepseek", "antigravity", "workbuddy"].includes(saved.source) ? saved.source : "all",
    provider: saved.provider || "all",
    model: saved.model || "all",
    start: initialPresetDates?.start || (validDate(saved.start) ? saved.start : today),
    end: initialPresetDates?.end || (validDate(saved.end) ? saved.end : today),
    rangePreset,
    tab: ["requests", "providers", "models"].includes(saved.tab) ? saved.tab : "requests",
    pages: { requests: 1, providers: 1, models: 1 },
    data: null,
    summary: null,
    summaryLoading: false,
    summaryError: "",
  };

  let controller = null;
  let summaryController = null;
  let summaryTask = null;
  let summaryRequestVersion = 0;
  let requestVersion = 0;
  let visibilityObserver = null;

  root.innerHTML = `
    <header class="tokenStatsTopbar">
      <div class="tokenStatsHeading">
        <p class="tokenStatsEyebrow">AI 工作台 / 使用洞察</p>
        <h1>使用统计</h1>
        <p class="tokenStatsScanLine" data-token-stats-role="status" aria-live="polite">
          选择时间范围，查看本机 AI 模型的 Token 使用情况
        </p>
      </div>
      <button class="tokenStatsRefreshButton" type="button" data-action="refresh">刷新数据</button>
    </header>

    <section class="tokenStatsFilterBar" aria-label="统计筛选">
      <div class="tokenStatsSourceControl" role="group" aria-label="来源">
        ${sourceButton("all", "全部来源")}
        ${sourceButton("codex", "Codex")}
        ${sourceButton("claude", "Claude Code")}
        ${sourceButton("deepseek", "DeepSeek")}
        ${sourceButton("antigravity", "Antigravity")}
        ${sourceButton("workbuddy", "WorkBuddy")}
      </div>
      <div class="tokenStatsDateRange">
        <label><span>开始日期</span><input type="date" data-role="start" value="${state.start}" /></label>
        <span class="tokenStatsDateSeparator">至</span>
        <label><span>结束日期</span><input type="date" data-role="end" value="${state.end}" /></label>
        <button class="tokenStatsQueryButton" type="button" data-action="query">查询</button>
      </div>
      <div class="tokenStatsQuickRanges" aria-label="快捷日期">
        <button type="button" data-action="range" data-range="24h">最近24小时</button>
        <button type="button" data-action="range" data-range="today">今天</button>
        <button type="button" data-action="range" data-range="yesterday">昨天</button>
        <button type="button" data-action="range" data-range="7">近 7 天</button>
        <button type="button" data-action="range" data-range="30">近 30 天</button>
        <button type="button" data-action="range" data-range="all">全部</button>
      </div>
      <div class="tokenStatsAdvancedFilters" data-role="advanced-filters"></div>
    </section>

    <div class="tokenStatsContent" data-token-stats-role="content"></div>
  `;

  const elements = {
    status: root.querySelector('[data-token-stats-role="status"]'),
    content: root.querySelector('[data-token-stats-role="content"]'),
    start: root.querySelector('[data-role="start"]'),
    end: root.querySelector('[data-role="end"]'),
    advancedFilters: root.querySelector('[data-role="advanced-filters"]'),
    refresh: root.querySelector('[data-action="refresh"]'),
  };

  root.addEventListener("click", handleClick);
  root.addEventListener("change", handleChange);
  root.addEventListener("keydown", handleKeydown);

  if (typeof MutationObserver === "function") {
    visibilityObserver = new MutationObserver(maybeStart);
    visibilityObserver.observe(root, { attributes: true, attributeFilter: ["hidden"] });
  }

  syncControls();
  renderContent();
  queueMicrotask(maybeStart);

  function maybeStart() {
    if (state.started || root.hidden) return;
    state.started = true;
    visibilityObserver?.disconnect();
    visibilityObserver = null;
    void loadData(false);
  }

  function handleClick(event) {
    const button = event.target.closest("[data-action]");
    if (!button || !root.contains(button)) return;
    const action = button.dataset.action;

    if (action === "refresh") {
      void loadData(true);
      return;
    }

    if (action === "source") {
      if (!["all", "codex", "claude", "deepseek", "antigravity", "workbuddy"].includes(button.dataset.source)) return;
      state.source = button.dataset.source;
      state.provider = "all";
      state.model = "all";
      resetPages();
      persistFilters();
      syncControls();
      void loadData(false);
      return;
    }

    if (action === "query") {
      applyDateInputs();
      return;
    }

    if (action === "range") {
      setQuickRange(button.dataset.range);
      return;
    }

    if (action === "tab") {
      state.tab = button.dataset.tab;
      persistFilters();
      renderDetails();
      return;
    }

    if (action === "page") {
      changePage(button.dataset.scope, button.dataset.page);
      return;
    }

    if (action === "page-jump") {
      jumpToInputPage(button.dataset.scope);
    }
  }

  function handleChange(event) {
    if (event.target.matches('[data-role="provider-filter"]')) {
      state.provider = event.target.value || "all";
      state.model = "all";
      resetPages();
      persistFilters();
      void loadData(false);
      return;
    }
    if (event.target.matches('[data-role="model-filter"]')) {
      state.model = event.target.value || "all";
      resetPages();
      persistFilters();
      void loadData(false);
    }
  }

  function handleKeydown(event) {
    if (event.key !== "Enter" || !event.target.matches('[data-role="page-input"]')) return;
    event.preventDefault();
    jumpToInputPage(event.target.dataset.scope);
  }

  function resetPages() {
    state.pages.requests = 1;
    state.pages.providers = 1;
    state.pages.models = 1;
  }

  function changePage(scope, pageValue) {
    if (!Object.prototype.hasOwnProperty.call(state.pages, scope)) return;
    const total = scope === "requests"
      ? number(state.data?.requestMeta?.total)
      : (scope === "providers" ? state.data?.providerStats.length : state.data?.modelStats.length);
    const pageCount = Math.max(1, Math.ceil(total / TABLE_PAGE_SIZE));
    const page = Math.min(pageCount, Math.max(1, Math.trunc(number(pageValue, 1))));
    if (page === state.pages[scope]) {
      const input = root.querySelector(`[data-role="page-input"][data-scope="${scope}"]`);
      if (input) input.value = String(page);
      return;
    }
    state.pages[scope] = page;
    if (scope === "requests") void loadData(false);
    else renderDetails();
  }

  function jumpToInputPage(scope) {
    const input = root.querySelector(`[data-role="page-input"][data-scope="${scope}"]`);
    if (!input) return;
    changePage(scope, input.value);
  }

  function applyDateInputs() {
    const start = elements.start.value;
    const end = elements.end.value;
    if (!validDate(start) || !validDate(end)) {
      setError("请选择有效的开始和结束日期");
      return;
    }
    if (start > end) {
      setError("开始日期不能晚于结束日期");
      return;
    }
    state.start = start;
    state.end = end;
    state.rangePreset = "";
    resetPages();
    persistFilters();
    void loadData(false);
  }

  function setQuickRange(range) {
    if (!QUICK_RANGE_PRESETS.includes(range)) return;
    const end = new Date();
    state.rangePreset = range;
    if (range === "all") {
      const available = availableDates(state.summary);
      state.start = state.data?.availableRange?.start || available[0] || "2020-01-01";
      state.end = state.data?.availableRange?.end || available.at(-1) || localDateKey(end);
    } else {
      const dates = quickRangeDates(range, end);
      state.start = dates.start;
      state.end = dates.end;
    }
    elements.start.value = state.start;
    elements.end.value = state.end;
    resetPages();
    persistFilters();
    void loadData(false);
  }

  async function loadData(refresh) {
    const requestNow = new Date();
    if (state.rangePreset && state.rangePreset !== "all") {
      const dates = quickRangeDates(state.rangePreset, requestNow);
      state.start = dates.start;
      state.end = dates.end;
    }
    controller?.abort();
    controller = new AbortController();
    const version = ++requestVersion;
    state.loading = true;
    state.error = "";
    state.notice = "";
    syncControls();
    renderStatus();
    if (!state.data) renderContent();

    try {
      const detailsParams = new URLSearchParams({
        start: state.start,
        end: state.end,
        source: state.source,
        limit: String(TABLE_PAGE_SIZE),
        offset: String((state.pages.requests - 1) * TABLE_PAGE_SIZE),
      });
      const exactBounds = rollingRangeBounds(state.rangePreset, requestNow);
      if (exactBounds) {
        detailsParams.set("start_time", exactBounds.startTime);
        detailsParams.set("end_time", exactBounds.endTime);
      }
      if (state.provider !== "all") detailsParams.set("provider", state.provider);
      if (state.model !== "all") detailsParams.set("model", state.model);
      if (refresh) detailsParams.set("refresh", "1");

      const detailsResult = await fetchJson(`${DETAILS_ENDPOINT}?${detailsParams}`, controller.signal);
      if (version !== requestVersion) return;
      state.data = normalizeDetails(detailsResult, state);
      state.pages.requests = Math.min(
        state.pages.requests,
        Math.max(1, state.data.requestMeta.pageCount),
      );

      if (ensureFilterValues()) {
        resetPages();
        persistFilters();
        void loadData(false);
        return;
      }
      persistFilters();
      // Details and summary share the same on-disk file cache. Starting both scans
      // together makes them contend for SQLite and delays the data the user is
      // actively waiting for. Render the selected range first, then refresh the
      // annual heatmap in the background.
      void ensureSummary(refresh);
    } catch (error) {
      if (error?.name === "AbortError" || version !== requestVersion) return;
      if (state.rangePreset === "24h") {
        state.error = error?.message || "最近24小时统计需要带精确时间的请求明细";
      } else {
        try {
          const summary = state.summary || await ensureSummary(refresh);
          if (!summary) throw error;
          if (version !== requestVersion) return;
          state.summary = summary;
          state.data = detailsFromSummary(summary, state);
          state.notice = "当前数据源暂未提供逐请求明细，趋势与汇总已从本机日统计生成";
        } catch (fallbackError) {
          if (fallbackError?.name === "AbortError" || version !== requestVersion) return;
          state.error = error?.message || fallbackError?.message || "统计读取失败";
        }
      }
    } finally {
      if (version === requestVersion) {
        state.loading = false;
        syncControls();
        renderStatus();
        renderAdvancedFilters();
        renderContent();
      }
    }
  }

  function ensureSummary(refresh) {
    if (state.summary && !refresh) return Promise.resolve(state.summary);
    if (summaryTask && !refresh) return summaryTask;

    summaryController?.abort();
    summaryController = new AbortController();
    const summaryVersion = ++summaryRequestVersion;
    const summaryUrl = refresh ? `${SUMMARY_ENDPOINT}?refresh=1` : SUMMARY_ENDPOINT;
    state.summaryLoading = true;
    state.summaryError = "";
    renderActivity();

    const task = fetchJson(summaryUrl, summaryController.signal)
      .then((summary) => {
        if (summaryVersion !== summaryRequestVersion) return null;
        state.summary = summary;
        return summary;
      })
      .catch((error) => {
        if (error?.name === "AbortError" || summaryVersion !== summaryRequestVersion) return null;
        state.summaryError = state.summary
          ? "年度数据刷新失败，当前显示上次结果"
          : "年度数据暂不可用，当前仅显示所选日期范围";
        return null;
      })
      .finally(() => {
        if (summaryVersion !== summaryRequestVersion) return;
        state.summaryLoading = false;
        summaryTask = null;
        renderActivity();
        renderLegacyCoverage();
      });
    summaryTask = task;
    return task;
  }

  function ensureFilterValues() {
    const providers = state.data?.filterOptions.providers || [];
    const models = state.data?.filterOptions.models || [];
    let changed = false;
    if (state.provider !== "all" && !providers.includes(state.provider)) {
      state.provider = "all";
      changed = true;
    }
    if (state.model !== "all" && !models.includes(state.model)) {
      state.model = "all";
      changed = true;
    }
    return changed;
  }

  function syncControls() {
    root.querySelectorAll('[data-action="source"]').forEach((button) => {
      const selected = button.dataset.source === state.source;
      button.classList.toggle("tokenStatsSourceSelected", selected);
      button.setAttribute("aria-pressed", selected ? "true" : "false");
    });
    elements.start.value = state.start;
    elements.end.value = state.end;
    elements.refresh.disabled = state.loading;
    elements.refresh.textContent = state.loading ? "读取中…" : "刷新数据";
    const today = new Date();
    root.querySelectorAll('[data-action="range"]').forEach((button) => {
      const range = button.dataset.range;
      const dates = range === "all" ? null : quickRangeDates(range, today);
      const selected = state.rangePreset
        ? range === state.rangePreset
        : Boolean(dates && range !== "24h" && state.start === dates.start && state.end === dates.end);
      button.classList.toggle("is-active", selected);
      button.setAttribute("aria-pressed", String(selected));
    });
  }

  function renderStatus() {
    elements.status.classList.toggle("tokenStatsScanError", Boolean(state.error));
    if (state.loading) {
      elements.status.textContent = `正在统计 ${selectedRangeLabel(state)} 的本机记录…`;
    } else if (state.error) {
      elements.status.textContent = state.error;
    } else if (state.notice) {
      elements.status.textContent = state.notice;
    } else if (state.data) {
      const precisionNote = state.rangePreset === "24h" && state.data.exactRangeCoverage.excludedUntimestampedRows
        ? ` · ${formatInteger(state.data.exactRangeCoverage.excludedUntimestampedRows)} 条无精确时间的旧汇总未纳入`
        : "";
      elements.status.textContent = `${selectedRangeLabel(state)} · ${sourceLabel(state.source)} · 数据更新于 ${formatDateTime(state.data.generatedAt)}${precisionNote}`;
    }
  }

  function renderAdvancedFilters() {
    const providers = state.data?.filterOptions.providers || [];
    const models = state.data?.filterOptions.models || [];
    elements.advancedFilters.innerHTML = `
      <label>
        <span>Provider</span>
        <select data-role="provider-filter" ${providers.length ? "" : "disabled"}>
          <option value="all">全部 Provider</option>
          ${providers.map((value) => optionMarkup(value, state.provider)).join("")}
        </select>
      </label>
      <label>
        <span>模型</span>
        <select data-role="model-filter" ${models.length ? "" : "disabled"}>
          <option value="all">全部模型</option>
          ${models.map((value) => optionMarkup(value, state.model)).join("")}
        </select>
      </label>
    `;
  }

  function renderContent() {
    if (!state.data) {
      elements.content.innerHTML = stateMarkup(
        state.loading ? "正在读取使用统计…" : state.error || "打开统计模块后开始读取本机记录",
        Boolean(state.error),
      );
      return;
    }

    const metrics = state.data.metrics;
    elements.content.innerHTML = `
      <section class="tokenStatsOverview" aria-label="使用概览">
        <div class="tokenStatsOverviewLead">
          <span class="tokenStatsOverviewIcon" aria-hidden="true">↯</span>
          <div>
            <small>Token 总用量</small>
            <strong title="精确值：${formatInteger(metrics.totalTokens)} Tokens">${formatCompact(metrics.totalTokens)}</strong>
            <span>含缓存读写 · 悬停查看精确值</span>
          </div>
        </div>
        <div class="tokenStatsOverviewSide">
          <div><small>总请求数</small><strong>${formatInteger(metrics.requestCount)}</strong></div>
          <div title="按公开 API 单价估算，非 Codex 订阅实际扣费"><small>API 等价估算</small><strong class="tokenStatsCostValue">${metrics.costPartial && !metrics.costKnown ? `$${number(metrics.totalCost).toFixed(4)}<span class="tokenStatsCostBadge">部分定价</span>` : formatCost(metrics.totalCost, metrics.costKnown, metrics.costPartial)}</strong></div>
        </div>
        <div class="tokenStatsMetricGrid">
          ${overviewMetric("新增输入", metrics.inputTokens, "blue")}
          ${overviewMetric("输出", metrics.outputTokens, "green")}
          ${cacheOverviewMetric(metrics)}
          ${overviewMetric("推理 Token", metrics.reasoningTokens, "slate")}
          ${cacheRateMetric(metrics.cacheHitRate, metrics.cacheReadTokens, metrics.rawInputTokens, metrics.cacheReadCoverage, metrics.requestCount)}
        </div>
      </section>
      <div data-token-coverage-holder>${sourceCoverageMarkup(state.summary, state.source)}</div>
      ${activityHeatmapMarkup(state.summary, state.source, state.data.trend, state.summaryLoading, state.summaryError)}
      ${trendMarkup(state.data.trend, state.start, state.end, state.data.trendGrain)}
      <section class="tokenStatsDetailsCard">
        <div class="tokenStatsTabs" role="tablist" aria-label="统计明细">
          ${tabButton("requests", "请求日志", "≡")}
          ${tabButton("providers", "Provider 统计", "⌁")}
          ${tabButton("models", "模型统计", "▥")}
        </div>
        <div class="tokenStatsTabPanel" data-role="tab-panel"></div>
      </section>
    `;
    bindActivityHeatmap(elements.content);
    renderDetails();
  }

  function renderActivity() {
    if (!state.data) return;
    const holder = document.createElement("div");
    holder.innerHTML = activityHeatmapMarkup(
      state.summary,
      state.source,
      state.data.trend,
      state.summaryLoading,
      state.summaryError,
    ).trim();
    const nextCard = holder.firstElementChild;
    const currentCard = elements.content.querySelector("[data-token-activity-root]");
    if (!nextCard || !currentCard) return;
    currentCard.replaceWith(nextCard);
    bindActivityHeatmap(elements.content);
  }

  function renderLegacyCoverage() {
    if (!state.data) return;
    const holder = elements.content.querySelector("[data-token-coverage-holder]");
    if (!holder) return;
    holder.innerHTML = sourceCoverageMarkup(state.summary, state.source);
  }

  function renderDetails() {
    if (!state.data) return;
    root.querySelectorAll('[data-action="tab"]').forEach((button) => {
      const selected = button.dataset.tab === state.tab;
      button.classList.toggle("tokenStatsTabSelected", selected);
      button.setAttribute("aria-selected", selected ? "true" : "false");
    });
    const panel = root.querySelector('[data-role="tab-panel"]');
    if (!panel) return;
    if (state.tab === "providers") {
      state.pages.providers = clampPage(state.pages.providers, state.data.providerStats.length);
      panel.innerHTML = aggregateTableMarkup(state.data.providerStats, "provider", state.pages.providers);
    } else if (state.tab === "models") {
      state.pages.models = clampPage(state.pages.models, state.data.modelStats.length);
      panel.innerHTML = aggregateTableMarkup(state.data.modelStats, "model", state.pages.models);
    } else {
      state.pages.requests = clampPage(state.pages.requests, state.data.requestMeta.total);
      panel.innerHTML = requestTableMarkup(state.data.requestLogs, state.data.requestMeta, state.pages.requests);
    }
  }

  function persistFilters() {
    try {
      localStorage.setItem(FILTER_STORAGE_KEY, JSON.stringify({
        source: state.source,
        provider: state.provider,
        model: state.model,
        start: state.start,
        end: state.end,
        rangePreset: state.rangePreset,
        tab: state.tab,
      }));
    } catch {
    }
  }
}

function sourceButton(value, label) {
  return `<button type="button" data-action="source" data-source="${value}">${label}</button>`;
}

function tabButton(value, label, icon) {
  return `<button type="button" role="tab" data-action="tab" data-tab="${value}"><span>${icon}</span>${label}</button>`;
}

function optionMarkup(value, selected) {
  return `<option value="${escapeAttr(value)}" ${value === selected ? "selected" : ""}>${escapeHtml(value)}</option>`;
}

function overviewMetric(label, value, tone) {
  return `
    <div class="tokenStatsOverviewMetric tokenStatsTone-${tone}">
      <small>${label}</small>
      <strong title="${formatInteger(value)}">${formatCompact(value)}</strong>
    </div>
  `;
}

function combinedCache(row, requestCount = row.requestCount ?? row.requests ?? 1) {
  const readCoverage = row.cacheReadCoverage || "none";
  const writeCoverage = row.cacheCreationCoverage || "none";
  const coverage = readCoverage === "full" && writeCoverage === "full"
    ? "full"
    : readCoverage === "none" && writeCoverage === "none" ? "none" : "partial";
  return {
    tokens: (readCoverage === "none" ? 0 : number(row.cacheReadTokens))
      + (writeCoverage === "none" ? 0 : number(row.cacheCreationTokens)),
    coverage,
    requestCount: number(requestCount),
    hasActivity: number(requestCount) > 0 || number(row.totalTokens) > 0,
  };
}

function cacheTotalTitle(row, cache) {
  const activityCount = cache.hasActivity ? Math.max(1, cache.requestCount) : 0;
  const read = formatCacheWriteText(row.cacheReadTokens, row.cacheReadCoverage, activityCount);
  const write = formatCacheWriteText(row.cacheCreationTokens, row.cacheCreationCoverage, activityCount);
  const note = cache.coverage === "partial"
    ? "合计仅包含已知部分；未提供的缓存字段仍未知"
    : cache.coverage === "none" && cache.hasActivity
      ? "缓存读取和写入字段均未提供，不能视为 0"
      : "合计为缓存读取 + 缓存写入";
  return `缓存 Token：${formatCacheWriteText(cache.tokens, cache.coverage, activityCount)}\n读取：${read}\n写入：${write}\n${note}`;
}

function cacheOverviewMetric(metrics) {
  const cache = combinedCache(metrics);
  const unavailable = cache.coverage === "none" && cache.hasActivity;
  return `
    <div class="tokenStatsOverviewMetric tokenStatsTone-orange">
      <small>缓存 Token</small>
      <strong title="${escapeAttr(cacheTotalTitle(metrics, cache))}">${unavailable ? "未提供" : formatCompact(cache.tokens)}</strong>
      ${cache.coverage === "partial" ? '<span class="tokenStatsCacheCoverage">已知部分</span>' : ""}
    </div>
  `;
}

function cacheRateMetric(rate, cacheReadTokens, rawInputTokens, coverage = "full", requestCount = 0) {
  const unavailable = coverage === "none" && requestCount > 0;
  const partial = coverage === "partial";
  const percent = optionalNumber(rate);
  const known = !unavailable && !partial && percent !== null && rawInputTokens > 0;
  const bounded = Math.min(100, Math.max(0, percent ?? 0));
  const label = unavailable ? "未提供" : partial ? "部分未知" : known ? `${bounded.toFixed(1)}%` : "—";
  const description = unavailable
    ? "日志未提供缓存读取字段，不能视为 0"
    : partial
      ? `已知缓存读取 ${formatCompact(cacheReadTokens)} Token；总命中率未知`
      : known
        ? `命中 ${formatCompact(cacheReadTokens)} / 输入 ${formatCompact(rawInputTokens)}`
        : "所选范围没有可计算命中率的输入";
  return `
    <div class="tokenStatsCacheRate${known ? "" : " is-unknown"}" title="${escapeAttr(known ? "缓存读取 ÷ 原始输入 Token" : description)}">
      <div><small>缓存命中率</small><strong>${label}</strong></div>
      ${known ? `<div class="tokenStatsCacheTrack" aria-label="缓存命中率 ${bounded.toFixed(1)}%"><i style="width:${bounded}%"></i></div>` : ""}
      <span>${escapeHtml(description)}</span>
    </div>
  `;
}

function legacyCoverageMarkup(payload, source) {
  if (!payload || !["all", "claude"].includes(source)) return "";
  const root = unwrap(payload);
  const claude = root.claude || root.sources?.claude || {};
  const coverage = claude.coverage || root.coverage?.claude || {};
  const legacy = coverage.legacyFallback || coverage.legacy_fallback || {};
  const statsCache = legacy.statsCache || legacy.stats_cache || {};
  const history = legacy.history || {};
  const sessionsIndex = legacy.sessionsIndex || legacy.sessions_index || {};
  const firstStartRaw = legacy.firstStart ?? legacy.first_start;
  const firstStartValue = firstStartRaw && typeof firstStartRaw === "object"
    ? string(firstStartRaw.date, firstStartRaw.timestamp, firstStartRaw.startedAt, firstStartRaw.started_at)
    : string(firstStartRaw);
  const firstStartDate = String(firstStartValue).slice(0, 10);
  const fallbackSessions = number(coverage.fallbackSessionsAdded, coverage.fallback_sessions_added);
  const fallbackDays = number(coverage.fallbackActivityDays, coverage.fallback_activity_days);
  const tokenBuckets = number(coverage.fallbackTokenBucketsAdded, coverage.fallback_token_buckets_added, statsCache.tokenBucketsAdded, statsCache.token_buckets_added);
  const fallbackTokens = number(coverage.fallbackTokenTotal, coverage.fallback_token_total);
  const restoredBreakdownUnavailable = Boolean(
    coverage.restoredTokenBreakdownUnavailable ?? coverage.restored_token_breakdown_unavailable,
  );
  const unallocatedCacheTokens = number(statsCache.unallocatedCacheReadTokens, statsCache.unallocated_cache_read_tokens)
    + number(statsCache.unallocatedCacheCreationTokens, statsCache.unallocated_cache_creation_tokens);
  const activityOnlySessions = number(coverage.activityOnlySessions, coverage.activity_only_sessions);
  const missingTranscripts = number(
    coverage.missingTranscriptSessions,
    coverage.missing_transcript_sessions,
    history.missingJsonlSessions,
    history.missing_jsonl_sessions,
    sessionsIndex.missingJsonlSessions,
    sessionsIndex.missing_jsonl_sessions,
  );
  const hasLegacyCoverage = fallbackSessions > 0 || fallbackDays > 0 || tokenBuckets > 0 || fallbackTokens > 0 || unallocatedCacheTokens > 0
    || activityOnlySessions > 0 || missingTranscripts > 0 || validDate(firstStartDate)
    || Boolean(statsCache.exists || statsCache.read || history.exists || history.read)
    || number(sessionsIndex.files, sessionsIndex.filesRead, sessionsIndex.files_read) > 0;
  if (!hasLegacyCoverage) return "";

  const facts = [];
  if (validDate(firstStartDate)) facts.push(`检测到首次启动 ${firstStartDate}（仅启动证据，不计为会话或 Token）`);
  if (fallbackSessions > 0) facts.push(`从本机旧索引补回 ${formatInteger(fallbackSessions)} 个会话${fallbackDays > 0 ? `，覆盖 ${formatInteger(fallbackDays)} 个活跃日` : ""}`);
  else if (fallbackDays > 0) facts.push(`已补回 ${formatInteger(fallbackDays)} 个历史活跃日`);
  if (tokenBuckets > 0) facts.push(`${formatInteger(tokenBuckets)} 个无冲突历史 Token 桶已计入可确认合计${fallbackTokens > 0 ? `（${formatInteger(fallbackTokens)} Token）` : ""}`);
  if (restoredBreakdownUnavailable && fallbackTokens > 0) facts.push("历史 Token 桶仅保留总量，新增输入/输出/缓存拆分不可用");
  if (unallocatedCacheTokens > 0) facts.push(`另有 ${formatInteger(unallocatedCacheTokens)} 缓存 Token 无法按日归属，未计入 Token 合计`);
  if (activityOnlySessions > 0) facts.push(`${formatInteger(activityOnlySessions)} 个会话仅能确认曾使用，Token 未知`);
  else if (missingTranscripts > 0) facts.push(`${formatInteger(missingTranscripts)} 个会话缺少完整日志，不会伪造 Token`);
  if (!facts.length) facts.push("已检测 Claude Code CLI 历史索引；仅活动记录不计入 Token 合计");

  return `
    <aside class="tokenStatsCoverageNote" aria-label="Claude Code CLI 历史数据覆盖说明">
      <span class="tokenStatsCoverageIcon" aria-hidden="true">◷</span>
      <div>
        <div class="tokenStatsCoverageTitle"><strong>Claude Code CLI 历史已纳入</strong><span>本机历史覆盖</span></div>
        <p>${facts.map(escapeHtml).join(" · ")}</p>
        <small class="tokenStatsCoverageScope">本卡展示全年/本机历史覆盖，不随上方日期筛选变化；历史回退数据已按规则合并并去重。</small>
      </div>
    </aside>
  `;
}

function sourceCoverageMarkup(payload, source) {
  const legacy = legacyCoverageMarkup(payload, source);
  if (!payload || !["all", "deepseek"].includes(source)) return legacy;
  const root = unwrap(payload);
  const deepseek = root.deepseek || root.sources?.deepseek || {};
  const coverage = deepseek.coverage || root.coverage?.deepseek || {};
  const formats = coverage.sourceFormats || coverage.source_formats;
  if (!formats || typeof formats !== "object") return legacy;
  const files = Object.values(formats).reduce((sum, count) => sum + number(count), 0);
  const desktopVersionFiles = number(formats["session.v4.jsonl.zstd"]);
  const description = desktopVersionFiles > 0
    ? `检测到 ${formatInteger(files)} 份 dsh 本机会话日志，其中桌面新版 v4 日志 ${formatInteger(desktopVersionFiles)} 份；扫描已支持该格式。`
    : `检测到 ${formatInteger(files)} 份 dsh 本机会话日志；扫描同时支持桌面新版 v4 格式。`;
  const failureNotice = string(coverage.dependencyError, coverage.dependency_error)
    ? "本机压缩日志暂时无法读取，当前覆盖不完整。"
    : number(coverage.filesFailed, coverage.files_failed) > 0
      ? `有 ${formatInteger(number(coverage.filesFailed, coverage.files_failed))} 份 DeepSeek 日志未能读取，当前覆盖不完整。`
      : "";
  const sessionsRoot = string(coverage.sessionsRoot, coverage.sessions_root);
  return `${legacy}
    <aside class="tokenStatsCoverageNote" aria-label="DeepSeek dsh 本机日志覆盖说明">
      <span class="tokenStatsCoverageIcon" aria-hidden="true">◷</span>
      <div>
        <div class="tokenStatsCoverageTitle"><strong>DeepSeek / dsh 本机记录</strong><span>桌面与 CLI 日志</span></div>
        <p>${escapeHtml(description)} ${escapeHtml(failureNotice)} 缓存 Token 为已知读取与写入的合计；缺失字段仍未知，日志明确记录的 0 保留为 0。</p>
        <small class="tokenStatsCoverageScope">本卡展示本机日志覆盖，不随日期筛选变化。${sessionsRoot ? ` 日志目录：${escapeHtml(sessionsRoot)}` : ""}</small>
      </div>
    </aside>`;
}

function trendMarkup(rows, start, end, grain = "day") {
  const points = rows.map((row) => {
    const cache = combinedCache(row, row.requests);
    return { ...row, cacheTotalTokens: cache.tokens, cacheTotalCoverage: cache.coverage };
  });
  const hourly = grain === "hour";
  const width = Math.max(1040, points.length * (hourly ? 32 : 7));
  const height = 280;
  const pad = { left: 58, right: 22, top: 20, bottom: 44 };
  const plotWidth = width - pad.left - pad.right;
  const plotHeight = height - pad.top - pad.bottom;
  const max = Math.max(1, ...points.flatMap((row) => [
    row.totalTokens,
    row.inputTokens,
    row.outputTokens,
    row.cacheTotalTokens,
  ]));
  const x = (index) => pad.left + (points.length <= 1 ? plotWidth / 2 : (index / (points.length - 1)) * plotWidth);
  const y = (value) => pad.top + plotHeight - (Math.max(0, value) / max) * plotHeight;
  const series = [
    ["总量", "totalTokens", "#4f5fd5"],
    ["新增输入", "inputTokens", "#3182b6"],
    ["输出", "outputTokens", "#238b7b"],
    ["缓存 Token", "cacheTotalTokens", "#b68b4e"],
  ];
  const grid = [0, 0.25, 0.5, 0.75, 1].map((ratio) => {
    const py = pad.top + plotHeight * (1 - ratio);
    return `<line x1="${pad.left}" y1="${py}" x2="${width - pad.right}" y2="${py}" />
      <text x="${pad.left - 10}" y="${py + 4}" text-anchor="end">${formatCompact(max * ratio)}</text>`;
  }).join("");
  const paths = series.map(([, key, color], seriesIndex) => {
    const cacheSeries = key === "cacheTotalTokens";
    const partial = cacheSeries && points.some((row) => row.cacheTotalCoverage === "partial");
    const path = cacheSeries ? coveredLinePath(points, key, "cacheTotalCoverage", x, y, true) : linePath(points, key, x, y);
    const area = seriesIndex === 0 && points.length
      ? `<path class="tokenStatsChartArea" d="${areaPath(points, key, x, y, pad.top + plotHeight)}" fill="url(#tokenStatsArea)" />`
      : "";
    return `${area}<path class="tokenStatsChartLine" d="${path}" stroke="${color}"${partial ? ' stroke-dasharray="6 4"' : ""} />`;
  }).join("");
  const dots = points.map((row, index) => `
    <circle cx="${x(index)}" cy="${y(row.totalTokens)}" r="7" class="tokenStatsChartHit">
      <title>${escapeHtml(row.label)}\n总量 ${formatInteger(row.totalTokens)}\n新增输入 ${formatInteger(row.inputTokens)}\n输出 ${formatInteger(row.outputTokens)}\n${escapeHtml(cacheTotalTitle(row, combinedCache(row, row.requests)))}\n请求 ${formatInteger(row.requests)}</title>
    </circle>
  `).join("");
  const dailyLabelCount = points.length <= 10 ? points.length : points.length <= 31 ? 8 : 10;
  const labelIndexes = chartLabelIndexes(points.length, hourly ? 9 : dailyLabelCount);
  const labels = labelIndexes.map((index) => `<text x="${x(index)}" y="${height - 17}" text-anchor="middle">${escapeHtml(hourly ? hourAxisLabel(points[index]?.label, index) : shortDay(points[index]?.label))}</text>`).join("");

  return `
    <section class="tokenStatsTrendCard">
      <div class="tokenStatsCardHeader">
        <div><h2>使用趋势</h2><p>${formatRange(start, end)} · 按${hourly ? "小时" : "天"}汇总${points.some((row) => (row.requests > 0 || row.totalTokens > 0) && row.cacheTotalCoverage !== "full") ? " · 缓存虚线为已知部分；未提供处断线" : ""}</p></div>
        <div class="tokenStatsChartLegend">
          ${series.map(([label, , color]) => `<span><i style="background:${color}"></i>${label}</span>`).join("")}
        </div>
      </div>
      ${points.length ? `
        <div class="tokenStatsChartScroll">
          <svg class="tokenStatsChart" viewBox="0 0 ${width} ${height}" style="min-width:${Math.max(860, points.length * 7)}px" role="img" aria-label="Token 使用趋势图">
            <defs><linearGradient id="tokenStatsArea" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="#4f5fd5" stop-opacity=".10"/><stop offset="1" stop-color="#4f5fd5" stop-opacity="0"/></linearGradient></defs>
            <g class="tokenStatsChartGrid">${grid}${labels}</g>
            ${paths}${dots}
          </svg>
        </div>
      ` : '<div class="tokenStatsEmpty">所选时间内没有趋势数据</div>'}
    </section>
  `;
}

function linePath(rows, key, x, y) {
  if (!rows.length) return "";
  const points = rows.map((row, index) => ({ x: x(index), y: y(row[key]) }));
  if (points.length === 1) return `M${points[0].x.toFixed(2)},${points[0].y.toFixed(2)}`;

  const segmentSlopes = points.slice(1).map((point, index) => (
    (point.y - points[index].y) / Math.max(1, point.x - points[index].x)
  ));
  const tangents = points.map((_, index) => {
    if (index === 0) return segmentSlopes[0];
    if (index === points.length - 1) return segmentSlopes.at(-1);
    const before = segmentSlopes[index - 1];
    const after = segmentSlopes[index];
    if (!before || !after || before * after <= 0) return 0;
    return (2 * before * after) / (before + after);
  });

  segmentSlopes.forEach((slope, index) => {
    if (!slope) {
      tangents[index] = 0;
      tangents[index + 1] = 0;
      return;
    }
    const beforeRatio = tangents[index] / slope;
    const afterRatio = tangents[index + 1] / slope;
    const magnitude = Math.hypot(beforeRatio, afterRatio);
    if (magnitude <= 3) return;
    const scale = 3 / magnitude;
    tangents[index] = scale * beforeRatio * slope;
    tangents[index + 1] = scale * afterRatio * slope;
  });

  return points.slice(1).reduce((path, point, index) => {
    const previous = points[index];
    const distance = point.x - previous.x;
    return `${path} C${(previous.x + distance / 3).toFixed(2)},${(previous.y + tangents[index] * distance / 3).toFixed(2)} ${(point.x - distance / 3).toFixed(2)},${(point.y - tangents[index + 1] * distance / 3).toFixed(2)} ${point.x.toFixed(2)},${point.y.toFixed(2)}`;
  }, `M${points[0].x.toFixed(2)},${points[0].y.toFixed(2)}`);
}

function areaPath(rows, key, x, y, baseline) {
  if (!rows.length) return "";
  return `${linePath(rows, key, x, y)} L${x(rows.length - 1).toFixed(2)},${baseline} L${x(0).toFixed(2)},${baseline} Z`;
}

function coveredLinePath(rows, key, coverageKey, x, y, includePartial = false) {
  const segments = [];
  let segment = [];
  rows.forEach((row, index) => {
    if ((row.requests > 0 || row.totalTokens > 0) && (row[coverageKey] === "none" || (!includePartial && row[coverageKey] !== "full"))) {
      if (segment.length) segments.push(segment);
      segment = [];
      return;
    }
    segment.push({ row, index });
  });
  if (segment.length) segments.push(segment);
  return segments.map((entries) => linePath(
    entries.map(({ row }) => row),
    key,
    (index) => x(entries[index].index),
    y,
  )).join(" ");
}

function chartLabelIndexes(length, count) {
  if (!length) return [];
  if (length <= count) return Array.from({ length }, (_, index) => index);
  return [...new Set(Array.from({ length: count }, (_, index) => Math.round((index / (count - 1)) * (length - 1))))];
}

function requestTableMarkup(rows, meta = {}, page = 1) {
  if (!rows.length) return emptyTable("所选时间内没有可用的逐请求日志");
  const total = Math.max(number(meta.total), rows.length);
  return `
    <div class="tokenStatsTableScroller">
      <table class="tokenStatsDataTable tokenStatsRequestTable">
        <thead><tr><th>时间</th><th>Provider</th><th>模型</th><th>服务档位</th><th>新增输入</th><th>输出</th><th>缓存 Token</th><th>推理</th><th>总 Token</th><th>状态</th><th>来源</th></tr></thead>
        <tbody>${rows.map((row) => `
          <tr>
            <td title="${escapeAttr(formatDateTime(row.time))}">${escapeHtml(formatLogTime(row.time))}</td>
            <td>${escapeHtml(row.provider || "-")}</td>
            <td class="tokenStatsMono" title="${escapeAttr(row.model)}">${escapeHtml(row.model || "-")}</td>
            <td title="${escapeAttr(tierDescription(row.tier, row.tierSource))}">${escapeHtml(tierLabel(row.tier))}</td>
            <td>${formatInteger(row.inputTokens)}</td>
            <td>${formatInteger(row.outputTokens)}</td>
            <td>${cacheTotalCellMarkup(row, 1)}</td>
            <td>${formatInteger(row.reasoningTokens)}</td>
            <td><strong>${formatInteger(row.totalTokens)}</strong></td>
            <td><span class="tokenStatsStatus ${row.success === true ? "is-success" : row.success === false ? "is-error" : ""}">${escapeHtml(row.status)}</span></td>
            <td>${escapeHtml(row.source || "-")}</td>
          </tr>
        `).join("")}</tbody>
      </table>
    </div>
    ${paginationMarkup("requests", page, total, TABLE_PAGE_SIZE, number(meta.offset), rows.length)}
  `;
}

function aggregateTableMarkup(rows, type, page = 1) {
  const firstLabel = type === "provider" ? "Provider" : "模型";
  if (!rows.length) return emptyTable(`所选时间内没有${firstLabel}统计`);
  const scope = type === "provider" ? "providers" : "models";
  const currentPage = clampPage(page, rows.length);
  const offset = (currentPage - 1) * TABLE_PAGE_SIZE;
  const visibleRows = rows.slice(offset, offset + TABLE_PAGE_SIZE);
  return `
    <div class="tokenStatsTableScroller">
      <table class="tokenStatsDataTable">
        <thead><tr><th>${firstLabel}</th>${type === "model" ? "<th>Provider</th>" : ""}<th>请求数</th><th>Tokens</th><th>新增输入</th><th>输出</th><th>缓存 Token</th><th>成功率</th><th>平均延迟</th><th title="按公开 API 单价估算，非 Codex 订阅实际扣费">API 等价估算</th></tr></thead>
        <tbody>${visibleRows.map((row) => `
          <tr>
            <td class="tokenStatsMono" title="${escapeAttr(row.name)}"><strong>${escapeHtml(row.name)}</strong></td>
            ${type === "model" ? `<td>${escapeHtml(row.provider || "-")}</td>` : ""}
            <td>${formatInteger(row.requests)}</td>
            <td>${formatInteger(row.totalTokens)}</td>
            <td>${formatInteger(row.inputTokens)}</td>
            <td>${formatInteger(row.outputTokens)}</td>
            <td>${cacheTotalCellMarkup(row, row.requests)}</td>
            <td>${formatPercent(row.successRate)}</td>
            <td>${formatLatency(row.averageLatency)}</td>
            <td>${formatCost(row.cost, row.costKnown, row.costPartial)}</td>
          </tr>
        `).join("")}</tbody>
      </table>
    </div>
    ${paginationMarkup(scope, currentPage, rows.length, TABLE_PAGE_SIZE)}
  `;
}

function clampPage(page, total, pageSize = TABLE_PAGE_SIZE) {
  const pageCount = Math.max(1, Math.ceil(number(total) / pageSize));
  return Math.min(pageCount, Math.max(1, Math.trunc(number(page, 1))));
}

function paginationMarkup(scope, currentPage, total, pageSize, actualOffset = null, returned = null) {
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const page = clampPage(currentPage, total, pageSize);
  const offset = actualOffset === null ? (page - 1) * pageSize : Math.max(0, number(actualOffset));
  const visibleCount = returned === null ? Math.min(pageSize, Math.max(0, total - offset)) : Math.max(0, number(returned));
  const start = total && visibleCount ? offset + 1 : 0;
  const end = Math.min(total, offset + visibleCount);
  const pages = paginationWindow(page, pageCount);
  return `
    <div class="tokenStatsPagination">
      <p>共 ${formatInteger(total)} 条${pageCount > 1 ? `<span>第 ${formatInteger(start)}–${formatInteger(end)} 条</span>` : ""}</p>
      ${pageCount > 1 ? `<nav aria-label="${scope === "requests" ? "请求日志" : scope === "providers" ? "Provider 统计" : "模型统计"}分页">
        <button type="button" data-action="page" data-scope="${scope}" data-page="${page - 1}" ${page <= 1 ? "disabled" : ""} aria-label="上一页">‹</button>
        ${pages.map((value) => value === "…"
          ? '<span class="tokenStatsPageEllipsis">…</span>'
          : `<button type="button" data-action="page" data-scope="${scope}" data-page="${value}" class="${value === page ? "is-current" : ""}" ${value === page ? 'aria-current="page"' : ""}>${value}</button>`).join("")}
        <button type="button" data-action="page" data-scope="${scope}" data-page="${page + 1}" ${page >= pageCount ? "disabled" : ""} aria-label="下一页">›</button>
        <label class="tokenStatsPageJump"><span>页码</span><input type="number" min="1" max="${pageCount}" value="${page}" data-role="page-input" data-scope="${scope}" inputmode="numeric" aria-label="输入页码" /><button type="button" data-action="page-jump" data-scope="${scope}">跳转</button></label>
      </nav>` : ""}
    </div>
  `;
}

function paginationWindow(currentPage, pageCount) {
  if (pageCount <= 7) return Array.from({ length: pageCount }, (_, index) => index + 1);
  const selected = new Set([1, pageCount, currentPage - 1, currentPage, currentPage + 1]);
  if (currentPage <= 3) [2, 3, 4].forEach((page) => selected.add(page));
  if (currentPage >= pageCount - 2) [pageCount - 3, pageCount - 2, pageCount - 1].forEach((page) => selected.add(page));
  const pages = [...selected].filter((page) => page >= 1 && page <= pageCount).sort((a, b) => a - b);
  const result = [];
  pages.forEach((page, index) => {
    if (index && page - pages[index - 1] > 1) result.push("…");
    result.push(page);
  });
  return result;
}

function emptyTable(message) {
  return `<div class="tokenStatsEmpty tokenStatsEmptyTable"><span>⌁</span><strong>${escapeHtml(message)}</strong><small>缓存 Token 只合计已知读写，两项均缺失显示“未提供”；费用缺少依据时显示“未定价”</small></div>`;
}

function stateMarkup(message, retry) {
  return `<div class="tokenStatsState"><span class="tokenStatsStateIcon">↗</span><p>${escapeHtml(message)}</p>${retry ? '<button type="button" data-action="refresh">重试</button>' : ""}</div>`;
}

async function fetchJson(url, signal) {
  const response = await fetch(url, { cache: "no-store", signal });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload?.error || `请求失败（HTTP ${response.status}）`);
  return payload;
}

function normalizeDetails(payload, filters) {
  const root = unwrap(payload);
  const periodRaw = root.period && typeof root.period === "object" ? root.period : {};
  const exactCoverageRaw = root.exactRangeCoverage && typeof root.exactRangeCoverage === "object"
    ? root.exactRangeCoverage
    : {};
  const trendGrain = ["hour", "day"].includes(periodRaw.trendGrain || root.trendGrain)
    ? (periodRaw.trendGrain || root.trendGrain)
    : "day";
  const metricsRaw = root.metrics || root.summary || root.totals || {};
  const trendRaw = pickArray(root.trend, root.daily, root.usageTrend, root.timeline, root.series);
  const requestRaw = pickArray(root.requestLogs, root.logs, root.requests, root.request_logs);
  const providerRaw = pickArray(root.providerStats, root.providers, root.byProvider, root.provider_stats);
  const modelRaw = pickArray(root.modelStats, root.models, root.byModel, root.model_stats);
  const requestLogs = requestRaw.map(normalizeRequest).sort((a, b) => String(b.time).localeCompare(String(a.time)));
  const trend = trendRaw.map((row) => normalizeTrend(row, trendGrain)).filter((row) => row.label).sort((a, b) => a.label.localeCompare(b.label));
  const providerStats = providerRaw.map((row) => normalizeAggregate(row, "provider"));
  const modelStats = modelRaw.map((row) => normalizeAggregate(row, "model"));
  const derived = deriveMetrics(trend, requestLogs, providerStats, modelStats);
  const totalTokens = number(metricsRaw.totalTokens, metricsRaw.total_tokens, metricsRaw.tokens, derived.totalTokens);
  const costValue = optionalNumber(metricsRaw.totalCost, metricsRaw.total_cost, metricsRaw.costUsd, metricsRaw.cost_usd, metricsRaw.cost);
  const pricedRequestCount = number(metricsRaw.pricedRequestCount, metricsRaw.priced_request_count);
  const requestCount = number(metricsRaw.requestCount, metricsRaw.request_count, metricsRaw.requests, derived.requestCount);
  const cacheCreationTokens = number(metricsRaw.cacheWriteTokens, metricsRaw.cache_write_tokens, metricsRaw.cacheCreationTokens, metricsRaw.cacheCreationInputTokens, metricsRaw.cache_creation_tokens, metricsRaw.cache_creation_input_tokens, derived.cacheCreationTokens);
  const cacheCreationCoverage = normalizeCacheCreationCoverage(
    metricsRaw,
    cacheCreationTokens,
    requestCount,
    derived.cacheCreationCoverage,
  );
  const cacheReadCoverage = normalizeCacheReadCoverage(metricsRaw, requestCount, derived.cacheReadCoverage);
  const cacheReadCounts = normalizeCacheReadCounts(metricsRaw, cacheReadCoverage, requestCount,
    derived.requestCount === requestCount && derived.cacheReadCoverage === cacheReadCoverage ? derived : {});
  const cacheCreationCounts = normalizeCacheCreationCounts(metricsRaw, cacheCreationCoverage, requestCount,
    derived.requestCount === requestCount && derived.cacheCreationCoverage === cacheCreationCoverage ? derived : {});
  const rawInputTokens = number(
    metricsRaw.rawInputTokens,
    metricsRaw.raw_input_tokens,
    metricsRaw.originalInputTokens,
    number(metricsRaw.inputTokens, metricsRaw.input_tokens, derived.inputTokens)
      + number(metricsRaw.cachedInputTokens, metricsRaw.cacheReadTokens, derived.cacheReadTokens)
      + number(metricsRaw.cacheCreationInputTokens, metricsRaw.cacheCreationTokens, derived.cacheCreationTokens),
  );
  const explicitCacheHitRate = optionalNumber(metricsRaw.cacheHitRate, metricsRaw.cache_hit_rate);
  const cacheHitRatio = optionalNumber(metricsRaw.cacheHitRatio, metricsRaw.cache_hit_ratio);
  const cacheReadTokens = number(metricsRaw.cacheReadTokens, metricsRaw.cache_read_tokens, metricsRaw.cachedInputTokens, derived.cacheReadTokens);
  const cacheHitRate = cacheReadCoverage === "full"
    ? explicitCacheHitRate ?? (cacheHitRatio !== null ? cacheHitRatio * 100 : (rawInputTokens ? cacheReadTokens / rawInputTokens * 100 : null))
    : null;
  const hasPriceCoverage = metricsRaw.pricedRequestCount !== undefined || metricsRaw.priced_request_count !== undefined;
  const explicitCostKnown = metricsRaw.costKnown ?? metricsRaw.cost_known;
  const costKnown = explicitCostKnown === false
    ? false
    : explicitCostKnown === true || (hasPriceCoverage ? requestCount > 0 && pricedRequestCount >= requestCount : costValue !== null);
  const costPartial = !costKnown && pricedRequestCount > 0;
  const filterRoot = root.filters || root.filterOptions || {};
  const providers = uniqueStrings(
    pickArray(filterRoot.providers, root.availableProviders).concat(providerStats.map((row) => row.name), requestLogs.map((row) => row.provider)),
  );
  const models = uniqueStrings(
    pickArray(filterRoot.models, root.availableModels).concat(modelStats.map((row) => row.name), requestLogs.map((row) => row.model)),
  );

  return {
    generatedAt: string(root.generatedAt, root.generated_at, new Date().toISOString()),
    metrics: {
      totalTokens,
      rawInputTokens,
      inputTokens: number(metricsRaw.inputTokens, metricsRaw.input_tokens, metricsRaw.input, derived.inputTokens),
      outputTokens: number(metricsRaw.outputTokens, metricsRaw.output_tokens, metricsRaw.output, derived.outputTokens),
      cacheReadTokens,
      cacheReadCoverage,
      ...cacheReadCounts,
      cacheCreationTokens,
      cacheCreationCoverage,
      ...cacheCreationCounts,
      reasoningTokens: number(metricsRaw.reasoningTokens, metricsRaw.reasoning_tokens, derived.reasoningTokens),
      cacheHitRate,
      requestCount,
      totalCost: costValue ?? derived.totalCost,
      costKnown,
      costPartial,
    },
    trend,
    trendGrain,
    requestLogs,
    requestMeta: {
      total: number(root.requestTotal, root.request_total, requestLogs.length),
      returned: number(root.requestReturned, root.request_returned, requestLogs.length),
      offset: number(root.requestOffset, root.request_offset, periodRaw.requestOffset, 0),
      limit: number(root.requestLimit, root.request_limit, periodRaw.requestLimit, TABLE_PAGE_SIZE),
      page: number(root.requestPage, root.request_page, periodRaw.requestPage, 1),
      pageCount: number(root.requestPageCount, root.request_page_count, periodRaw.requestPageCount, 1),
      truncated: Boolean(root.requestTruncated ?? root.request_truncated),
    },
    providerStats,
    modelStats,
    filterOptions: { providers, models },
    availableRange: {
      start: validDate(root.availableStart || root.available_start)
        ? (root.availableStart || root.available_start)
        : "",
      end: validDate(root.availableEnd || root.available_end)
        ? (root.availableEnd || root.available_end)
        : "",
    },
    exactRangeCoverage: {
      excludedUntimestampedRows: number(
        exactCoverageRaw.excludedUntimestampedRows,
        exactCoverageRaw.excluded_untimestamped_rows,
      ),
    },
    range: { start: filters.start, end: filters.end },
  };
}

function normalizeTrend(row, grain = "") {
  const label = string(row.timestamp, row.bucket, row.time, row.date, row.label);
  const requests = number(row.requests, row.requestCount, row.request_count, row.usageRows, row.usage_rows, row.tokenEvents, row.token_events, row.sessions);
  const cacheCreationTokens = number(row.cacheWriteTokens, row.cache_write_tokens, row.cacheCreationTokens, row.cacheCreationInputTokens, row.cache_creation_tokens, row.cache_creation_input_tokens);
  const cacheCreationCoverage = normalizeCacheCreationCoverage(row, cacheCreationTokens, requests);
  const cacheCreationCounts = normalizeCacheCreationCounts(row, cacheCreationCoverage, requests);
  const cacheReadCoverage = normalizeCacheReadCoverage(row, requests);
  const cacheReadCounts = normalizeCacheReadCounts(row, cacheReadCoverage, requests);
  return {
    label,
    hourly: grain ? grain === "hour" : /T(?!00:00:00)|\s(?!00:00)\d{1,2}:/.test(label),
    totalTokens: number(row.totalTokens, row.total_tokens, row.tokens, row.total),
    inputTokens: number(row.inputTokens, row.input_tokens, row.input),
    outputTokens: number(row.outputTokens, row.output_tokens, row.output),
    cacheReadTokens: number(row.cacheReadTokens, row.cache_read_tokens, row.cachedInputTokens),
    cacheReadCoverage,
    ...cacheReadCounts,
    cacheCreationTokens,
    cacheCreationCoverage,
    ...cacheCreationCounts,
    reasoningTokens: number(row.reasoningTokens, row.reasoning_tokens),
    requests,
    cost: number(row.cost, row.totalCost, row.total_cost),
  };
}

function normalizeRequest(row) {
  const inputTokens = number(row.inputTokens, row.input_tokens, row.input);
  const rawInputTokens = number(row.rawInputTokens, row.raw_input_tokens, row.originalInputTokens);
  const outputTokens = number(row.outputTokens, row.output_tokens, row.output);
  const cacheReadTokens = number(row.cacheReadTokens, row.cache_read_tokens, row.cachedInputTokens, row.cached_input_tokens);
  const cacheCreationTokens = number(row.cacheWriteTokens, row.cache_write_tokens, row.cacheCreationTokens, row.cache_creation_tokens, row.cacheCreationInputTokens, row.cache_creation_input_tokens);
  const cacheCreationCoverage = normalizeCacheCreationCoverage(row, cacheCreationTokens, 1);
  const cacheCreationCounts = normalizeCacheCreationCounts(row, cacheCreationCoverage, 1);
  const cacheReadCoverage = normalizeCacheReadCoverage(row, 1);
  const cacheReadCounts = normalizeCacheReadCounts(row, cacheReadCoverage, 1);
  const reasoningTokens = number(row.reasoningTokens, row.reasoning_tokens);
  const totalTokens = number(row.totalTokens, row.total_tokens, row.tokens, inputTokens + outputTokens);
  const statusValue = row.status ?? row.statusCode ?? row.status_code;
  const status = string(statusValue, "—");
  return {
    time: string(row.time, row.timestamp, row.createdAt, row.created_at, row.date),
    provider: string(row.provider, row.vendor, "-"),
    model: string(row.model, row.modelName, row.model_name, "-"),
    source: string(row.source, row.origin, "-"),
    tier: string(row.tier, row.serviceTier, row.service_tier, "unknown"),
    tierSource: string(row.tierSource, row.tier_source, row.serviceTierSource, row.service_tier_source, "unknown"),
    inputTokens,
    rawInputTokens,
    outputTokens,
    cacheReadTokens,
    cacheReadCoverage,
    ...cacheReadCounts,
    cacheCreationTokens,
    cacheCreationCoverage,
    ...cacheCreationCounts,
    reasoningTokens,
    totalTokens,
    status,
    success: typeof row.success === "boolean"
      ? row.success
      : statusValue === null || statusValue === undefined || statusValue === ""
        ? null
        : !/^(4|5|error|failed)/i.test(status),
    cost: row.priceKnown === false || row.costKnown === false ? null : optionalNumber(row.cost, row.totalCost, row.total_cost, row.costUsd, row.cost_usd),
    latency: number(row.latency, row.latencyMs, row.latency_ms),
  };
}

function tierLabel(value) {
  const tier = String(value || "").trim().toLowerCase();
  if (tier === "priority" || tier === "fast") return "Fast";
  if (tier === "default" || tier === "standard") return "Standard";
  if (tier === "batch") return "Batch";
  if (tier === "flex") return "Flex";
  return "未知";
}

function tierDescription(value, source) {
  const label = tierLabel(value);
  const normalizedSource = String(source || "").trim().toLowerCase();
  if (normalizedSource === "log.session_meta.thread_settings") return `${label}（会话请求档位估算，未确认响应实际档位）`;
  if (normalizedSource === "log.turn_context") return `${label}（请求上下文档位估算，未确认响应实际档位）`;
  if (normalizedSource === "log.thread_settings_applied") return `${label}（会话请求档位估算，未确认响应实际档位）`;
  if (normalizedSource === "log.claude.usage") return `${label}（Claude usage 档位）`;
  if (normalizedSource === "config.service_tier") {
    return `${label}（当前 Codex 请求配置推断，未确认历史响应实际档位）`;
  }
  return `${label}（日志未提供可确认档位）`;
}

function normalizeAggregate(row, type) {
  const cost = optionalNumber(row.cost, row.totalCost, row.total_cost, row.costUsd, row.cost_usd);
  const pricedRequestCount = number(row.pricedRequestCount, row.priced_request_count);
  const requests = number(row.requests, row.requestCount, row.request_count, row.sessions);
  const hasPriceCoverage = row.pricedRequestCount !== undefined || row.priced_request_count !== undefined;
  const explicitCostKnown = row.costKnown ?? row.cost_known;
  const costKnown = explicitCostKnown === false
    ? false
    : explicitCostKnown === true || (hasPriceCoverage ? requests > 0 && pricedRequestCount >= requests : cost !== null && row.priceKnown !== false);
  const cacheCreationTokens = number(row.cacheWriteTokens, row.cache_write_tokens, row.cacheCreationTokens, row.cacheCreationInputTokens, row.cache_creation_tokens, row.cache_creation_input_tokens);
  const cacheCreationCoverage = normalizeCacheCreationCoverage(row, cacheCreationTokens, requests);
  const cacheCreationCounts = normalizeCacheCreationCounts(row, cacheCreationCoverage, requests);
  const cacheReadCoverage = normalizeCacheReadCoverage(row, requests);
  const cacheReadCounts = normalizeCacheReadCounts(row, cacheReadCoverage, requests);
  return {
    name: string(type === "provider" ? row.provider : row.model, row.name, row.label, "未知"),
    provider: string(row.provider, Array.isArray(row.providers) ? row.providers.join(", ") : "", row.vendor),
    requests,
    totalTokens: number(row.totalTokens, row.total_tokens, row.tokens),
    inputTokens: number(row.inputTokens, row.input_tokens, row.input),
    outputTokens: number(row.outputTokens, row.output_tokens, row.output),
    cacheReadTokens: number(row.cacheReadTokens, row.cache_read_tokens, row.cachedInputTokens),
    cacheReadCoverage,
    ...cacheReadCounts,
    cacheCreationTokens,
    cacheCreationCoverage,
    ...cacheCreationCounts,
    successRate: normalizeRate(row.successRate ?? row.success_rate ?? row.success),
    averageLatency: optionalNumber(row.averageLatency, row.avgLatency, row.average_latency, row.latency),
    cost: cost ?? 0,
    costKnown,
    costPartial: !costKnown && pricedRequestCount > 0,
  };
}

function deriveMetrics(trend, requests, providers, models) {
  const sum = (rows, key) => rows.reduce((total, row) => total + number(row[key]), 0);
  const aggregates = providers.length ? providers : models;
  const source = trend.length ? trend : aggregates.length ? aggregates : requests;
  const totalCostValues = requests.map((row) => row.cost).filter((value) => value !== null);
  const cacheCreationKnownRequestCount = sum(source, "cacheCreationKnownRequestCount");
  const cacheCreationUnknownRequestCount = sum(source, "cacheCreationUnknownRequestCount");
  const cacheReadKnownRequestCount = sum(source, "cacheReadKnownRequestCount");
  const cacheReadUnknownRequestCount = sum(source, "cacheReadUnknownRequestCount");
  const requestCount = source === requests ? requests.length : sum(source, "requests");
  return {
    totalTokens: sum(source, "totalTokens") || sum(aggregates, "totalTokens"),
    inputTokens: sum(source, "inputTokens") || sum(aggregates, "inputTokens"),
    outputTokens: sum(source, "outputTokens") || sum(aggregates, "outputTokens"),
    cacheReadTokens: sum(source, "cacheReadTokens") || sum(aggregates, "cacheReadTokens"),
    cacheReadCoverage: cacheCreationCoverageFromCounts(cacheReadKnownRequestCount, cacheReadUnknownRequestCount, requestCount),
    cacheReadKnownRequestCount,
    cacheReadUnknownRequestCount,
    cacheCreationTokens: sum(source, "cacheCreationTokens") || sum(aggregates, "cacheCreationTokens"),
    cacheCreationCoverage: cacheCreationCoverageFromCounts(cacheCreationKnownRequestCount, cacheCreationUnknownRequestCount, requestCount),
    cacheCreationKnownRequestCount,
    cacheCreationUnknownRequestCount,
    reasoningTokens: sum(source, "reasoningTokens"),
    requestCount,
    totalCost: totalCostValues.reduce((sumValue, value) => sumValue + value, 0),
    costKnown: totalCostValues.length > 0,
  };
}

function detailsFromSummary(payload, filters) {
  const root = unwrap(payload);
  const sources = {
    codex: root.codex || root.sources?.codex || {},
    claude: root.claude || root.sources?.claude || {},
    deepseek: root.deepseek || root.sources?.deepseek || {},
    antigravity: root.antigravity || root.sources?.antigravity || {},
    workbuddy: root.workbuddy || root.sources?.workbuddy || {},
  };
  const selectedKeys = filters.source === "all" ? Object.keys(sources) : [filters.source];
  const selected = selectedKeys.map((key) => sources[key]).filter(Boolean);
  const byDate = new Map();
  const filteredDays = (source) => pickArray(source.days, source.daily, source.dailyTotals).filter((row) => {
    const date = String(row.date || row.day || "").slice(0, 10);
    return validDate(date) && date >= filters.start && date <= filters.end;
  });
  selected.flatMap(filteredDays).forEach((row) => {
    const date = String(row.date || row.day || "").slice(0, 10);
    if (!validDate(date) || date < filters.start || date > filters.end) return;
    const current = byDate.get(date) || normalizeTrend({ date }, "day");
    const next = normalizeTrend(row, "day");
    Object.keys(current).forEach((key) => {
      if (typeof current[key] === "number") current[key] += next[key] || 0;
    });
    byDate.set(date, current);
  });
  const trend = [...byDate.values()].map((row) => ({
    ...row,
    cacheReadCoverage: cacheCreationCoverageFromCounts(
      row.cacheReadKnownRequestCount,
      row.cacheReadUnknownRequestCount,
      row.requests,
    ),
    cacheCreationCoverage: cacheCreationCoverageFromCounts(
      row.cacheCreationKnownRequestCount,
      row.cacheCreationUnknownRequestCount,
      row.requests,
    ),
  })).sort((a, b) => a.label.localeCompare(b.label));
  const providerStats = [];
  const aggregateSummarySource = (source, provider) => {
    const rows = filteredDays(source).map((row) => normalizeTrend(row, "day"));
    return normalizeAggregate({
      provider,
      requests: sumTrend(rows, "requests"),
      tokens: sumTrend(rows, "totalTokens"),
      inputTokens: sumTrend(rows, "inputTokens"),
      outputTokens: sumTrend(rows, "outputTokens"),
      cacheReadTokens: sumTrend(rows, "cacheReadTokens"),
      cacheReadKnownRequestCount: sumTrend(rows, "cacheReadKnownRequestCount"),
      cacheReadUnknownRequestCount: sumTrend(rows, "cacheReadUnknownRequestCount"),
      cacheReadCoverage: cacheCreationCoverageFromCounts(
        sumTrend(rows, "cacheReadKnownRequestCount"),
        sumTrend(rows, "cacheReadUnknownRequestCount"),
        sumTrend(rows, "requests"),
      ),
      cacheWriteTokens: sumTrend(rows, "cacheCreationTokens"),
      cacheCreationKnownRequestCount: sumTrend(rows, "cacheCreationKnownRequestCount"),
      cacheCreationUnknownRequestCount: sumTrend(rows, "cacheCreationUnknownRequestCount"),
      cacheCreationCoverage: cacheCreationCoverageFromCounts(
        sumTrend(rows, "cacheCreationKnownRequestCount"),
        sumTrend(rows, "cacheCreationUnknownRequestCount"),
        sumTrend(rows, "requests"),
      ),
    }, "provider");
  };
  const labels = {
    codex: "Codex (Session)",
    claude: "Claude Code",
    deepseek: "DeepSeek",
    antigravity: "Antigravity",
    workbuddy: "WorkBuddy",
  };
  selectedKeys.forEach((key) => {
    if (sources[key]) providerStats.push(aggregateSummarySource(sources[key], labels[key]));
  });
  const derived = deriveMetrics(trend, [], providerStats, []);
  const rawInputTokens = derived.inputTokens + derived.cacheReadTokens + derived.cacheCreationTokens;
  const cacheHitRate = derived.cacheReadCoverage === "full" && rawInputTokens
    ? derived.cacheReadTokens / rawInputTokens * 100
    : null;
  return {
    generatedAt: string(root.generatedAt, root.generated_at, new Date().toISOString()),
    metrics: {
      ...derived,
      rawInputTokens,
      cacheHitRate,
      totalCost: 0,
      costKnown: false,
    },
    trend,
    trendGrain: "day",
    requestLogs: [],
    requestMeta: { total: 0, returned: 0, truncated: false },
    providerStats,
    modelStats: [],
    filterOptions: { providers: providerStats.map((row) => row.name), models: [] },
    exactRangeCoverage: { excludedUntimestampedRows: 0 },
  };
}

function availableDates(payload) {
  if (!payload) return [];
  const root = unwrap(payload);
  return uniqueStrings([
    ...pickArray(root.codex?.days, root.sources?.codex?.days).map((row) => String(row.date || row.day || "").slice(0, 10)),
    ...pickArray(root.claude?.days, root.sources?.claude?.days).map((row) => String(row.date || row.day || "").slice(0, 10)),
    ...pickArray(root.deepseek?.days, root.sources?.deepseek?.days).map((row) => String(row.date || row.day || "").slice(0, 10)),
    ...pickArray(root.antigravity?.days, root.sources?.antigravity?.days).map((row) => String(row.date || row.day || "").slice(0, 10)),
    ...pickArray(root.workbuddy?.days, root.sources?.workbuddy?.days).map((row) => String(row.date || row.day || "").slice(0, 10)),
  ]).filter(validDate).sort();
}

function unwrap(payload) {
  return payload?.data && typeof payload.data === "object" && !Array.isArray(payload.data) ? payload.data : payload || {};
}

function pickArray(...values) {
  return values.find(Array.isArray) || [];
}

function uniqueStrings(values) {
  return [...new Set(values.map((value) => String(value || "").trim()).filter(Boolean))].sort((a, b) => a.localeCompare(b));
}

function optionalNumber(...values) {
  for (const value of values) {
    if (value === null || value === undefined || value === "") continue;
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return null;
}

function number(...values) {
  return optionalNumber(...values) ?? 0;
}

function string(...values) {
  for (const value of values) {
    if (value === null || value === undefined) continue;
    const parsed = String(value).trim();
    if (parsed) return parsed;
  }
  return "";
}

function normalizeCacheCreationCoverage(row, tokens, requestCount, fallback = "") {
  const explicit = string(row.cacheCreationCoverage, row.cache_creation_coverage).toLowerCase();
  if (["full", "partial", "none"].includes(explicit)) return explicit;

  const knownCount = optionalNumber(row.cacheCreationKnownRequestCount, row.cache_creation_known_request_count);
  const unknownCount = optionalNumber(row.cacheCreationUnknownRequestCount, row.cache_creation_unknown_request_count);
  if (knownCount !== null || unknownCount !== null) {
    return cacheCreationCoverageFromCounts(knownCount || 0, unknownCount || 0, requestCount);
  }

  const explicitKnown = row.cacheCreationKnown ?? row.cache_creation_known ?? row.cacheWriteKnown ?? row.cache_write_known;
  if (explicitKnown === true) return "full";
  if (explicitKnown === false) return "none";

  if (number(requestCount) <= 0) return "none";
  const legacyValueProvided = [
    "cacheWriteTokens",
    "cache_write_tokens",
    "cacheCreationTokens",
    "cacheCreationInputTokens",
    "cache_creation_tokens",
    "cache_creation_input_tokens",
  ].some((field) => Object.prototype.hasOwnProperty.call(row, field) && optionalNumber(row[field]) !== null);
  if (legacyValueProvided) return "full";
  return ["full", "partial", "none"].includes(fallback) ? fallback : "none";
}

function normalizeCacheReadCoverage(row, requestCount, fallback = "") {
  const explicit = string(row.cacheReadCoverage, row.cache_read_coverage).toLowerCase();
  if (["full", "partial", "none"].includes(explicit)) return explicit;

  const knownCount = optionalNumber(row.cacheReadKnownRequestCount, row.cache_read_known_request_count);
  const unknownCount = optionalNumber(row.cacheReadUnknownRequestCount, row.cache_read_unknown_request_count);
  if (knownCount !== null || unknownCount !== null) {
    return cacheCreationCoverageFromCounts(knownCount || 0, unknownCount || 0, requestCount);
  }

  const explicitKnown = row.cacheReadKnown ?? row.cache_read_known ?? row.cachedInputKnown ?? row.cached_input_known;
  if (explicitKnown === true) return "full";
  if (explicitKnown === false) return "none";
  if (number(requestCount) <= 0) return "none";

  const legacyValueProvided = ["cacheReadTokens", "cache_read_tokens", "cachedInputTokens", "cached_input_tokens"]
    .some((field) => Object.prototype.hasOwnProperty.call(row, field) && optionalNumber(row[field]) !== null);
  if (legacyValueProvided) return "full";
  return ["full", "partial", "none"].includes(fallback) ? fallback : "none";
}

function normalizeCacheReadCounts(row, coverage, requestCount, fallback = {}) {
  const counts = normalizeCacheCreationCounts({
    cacheCreationKnownRequestCount: row.cacheReadKnownRequestCount ?? row.cache_read_known_request_count ?? fallback.cacheReadKnownRequestCount,
    cacheCreationUnknownRequestCount: row.cacheReadUnknownRequestCount ?? row.cache_read_unknown_request_count ?? fallback.cacheReadUnknownRequestCount,
  }, coverage, requestCount);
  return {
    cacheReadKnownRequestCount: counts.cacheCreationKnownRequestCount,
    cacheReadUnknownRequestCount: counts.cacheCreationUnknownRequestCount,
  };
}

function normalizeCacheCreationCounts(row, coverage, requestCount, fallback = {}) {
  const known = optionalNumber(row.cacheCreationKnownRequestCount, row.cache_creation_known_request_count, fallback.cacheCreationKnownRequestCount);
  const unknown = optionalNumber(row.cacheCreationUnknownRequestCount, row.cache_creation_unknown_request_count, fallback.cacheCreationUnknownRequestCount);
  if (known !== null || unknown !== null) {
    return {
      cacheCreationKnownRequestCount: number(known),
      cacheCreationUnknownRequestCount: number(unknown),
    };
  }
  const requests = Math.max(0, number(requestCount));
  if (coverage === "full") {
    return { cacheCreationKnownRequestCount: requests, cacheCreationUnknownRequestCount: 0 };
  }
  if (coverage === "partial") {
    return {
      cacheCreationKnownRequestCount: requests > 0 ? 1 : 0,
      cacheCreationUnknownRequestCount: Math.max(1, requests - 1),
    };
  }
  return { cacheCreationKnownRequestCount: 0, cacheCreationUnknownRequestCount: requests };
}

function cacheCreationCoverageFromCounts(knownCount, unknownCount, requestCount) {
  const known = Math.max(0, number(knownCount));
  const unknown = Math.max(0, number(unknownCount));
  const requests = Math.max(0, number(requestCount));
  if (requests > 0 && known >= requests && unknown === 0) return "full";
  if (known > 0) return "partial";
  return "none";
}

function normalizeRate(value) {
  const parsed = optionalNumber(value);
  if (parsed === null) return null;
  return parsed > 1 ? parsed / 100 : parsed;
}

function sumTrend(rows, key) {
  return rows.reduce((sum, row) => sum + number(row[key]), 0);
}

function validDate(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(String(value || ""))) return false;
  const date = new Date(`${value}T00:00:00`);
  return !Number.isNaN(date.getTime()) && localDateKey(date) === value;
}

function localDateKey(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function quickRangeDates(range, now = new Date()) {
  const end = new Date(now);
  let start = new Date(end);
  if (range === "24h") start = new Date(end.getTime() - 24 * 60 * 60 * 1000);
  if (range === "yesterday") {
    start.setDate(start.getDate() - 1);
    end.setDate(end.getDate() - 1);
  }
  if (range === "7") start.setDate(start.getDate() - 6);
  if (range === "30") start.setDate(start.getDate() - 29);
  return { start: localDateKey(start), end: localDateKey(end) };
}

function rollingRangeBounds(range, now = new Date()) {
  if (range !== "24h") return null;
  const end = new Date(now);
  const start = new Date(end.getTime() - 24 * 60 * 60 * 1000);
  return { startTime: start.toISOString(), endTime: end.toISOString() };
}

function selectedRangeLabel(filters) {
  return filters.rangePreset === "24h" ? "最近24小时" : formatRange(filters.start, filters.end);
}

function loadFilters() {
  try {
    const parsed = JSON.parse(localStorage.getItem(FILTER_STORAGE_KEY) || "{}");
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function sourceLabel(value) {
  return value === "codex"
    ? "Codex"
    : value === "claude"
      ? "Claude Code"
      : value === "deepseek"
        ? "DeepSeek"
        : value === "antigravity"
          ? "Antigravity"
          : value === "workbuddy"
            ? "WorkBuddy"
            : "全部来源";
}

function formatRange(start, end) {
  if (start === end) return start;
  return `${start} 至 ${end}`;
}

function shortDate(value) {
  const text = String(value || "");
  const date = text.slice(0, 10);
  const time = text.match(/[T\s](\d{2}:\d{2})/);
  return date ? `${date.slice(5)}${time ? ` ${time[1]}` : ""}` : text;
}

function shortDay(value) {
  const text = String(value || "");
  const date = text.slice(0, 10);
  return date.length === 10 ? date.slice(5) : text;
}

function hourAxisLabel(value, index) {
  const text = String(value || "");
  const date = text.slice(5, 10);
  const time = text.match(/[T\s](\d{2}:\d{2})/)?.[1] || "";
  return index === 0 || time === "00:00" ? `${date} ${time}`.trim() : time;
}

function formatLogTime(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).replace("T", " ").slice(0, 16);
  return date.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
}

function formatDateTime(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
}

function formatInteger(value) {
  return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 }).format(number(value));
}

function formatCacheWriteText(value, coverage, requestCount) {
  if (coverage === "none" && number(requestCount) > 0) return "未提供";
  if (coverage === "partial") return `已知 ${formatInteger(value)}（部分未知）`;
  return formatInteger(value);
}

function cacheTotalCellMarkup(row, requestCount) {
  const cache = combinedCache(row, requestCount);
  const unavailable = cache.coverage === "none" && cache.hasActivity;
  const text = unavailable ? "未提供" : `${formatInteger(cache.tokens)}${cache.coverage === "partial" ? "（已知部分）" : ""}`;
  const className = `tokenStatsCacheValue${unavailable ? " is-unknown" : cache.coverage === "partial" ? " is-partial" : ""}`;
  return `<span class="${className}" title="${escapeAttr(cacheTotalTitle(row, cache))}">${escapeHtml(text)}</span>`;
}

function formatCompact(value) {
  const parsed = number(value);
  const absolute = Math.abs(parsed);
  if (absolute >= 1_000_000_000) return `${(parsed / 1_000_000_000).toFixed(2)}B`;
  if (absolute >= 1_000_000) return `${(parsed / 1_000_000).toFixed(2)}M`;
  if (absolute >= 1_000) return `${(parsed / 1_000).toFixed(1)}K`;
  return formatInteger(parsed);
}

function formatWan(value) {
  const parsed = number(value);
  if (Math.abs(parsed) >= 10_000) return `${(parsed / 10_000).toFixed(2)} 万 Token`;
  return `${formatInteger(parsed)} Token`;
}

function formatCost(value, known, partial = false) {
  if (known) return `$${number(value).toFixed(4)}`;
  if (partial) return `$${number(value).toFixed(4)}（部分）`;
  return "未定价";
}

function formatPercent(value) {
  const parsed = normalizeRate(value);
  return parsed === null ? "-" : `${(parsed * 100).toFixed(1)}%`;
}

function formatLatency(value) {
  const parsed = optionalNumber(value);
  if (parsed === null) return "-";
  return parsed >= 1000 ? `${(parsed / 1000).toFixed(1)}s` : `${Math.round(parsed)}ms`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeAttr(value) {
  return escapeHtml(value);
}
