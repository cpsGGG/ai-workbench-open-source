const HEATMAP_WEEKS = 53;
const DAYS_PER_WEEK = 7;
const HEATMAP_LEVELS = 7;
const LOG_SCALE_ORDERS = 3;

export function activityHeatmapMarkup(summary, source, fallbackTrend = [], loading = false, error = "") {
  const activity = normalizeActivity(summary, source, fallbackTrend);
  const today = localDateKey(new Date());
  const calendarEnd = endOfWeek(parseDate(today));
  const calendarStart = addDays(calendarEnd, -(HEATMAP_WEEKS * DAYS_PER_WEEK - 1));
  const cells = [];

  for (let index = 0; index < HEATMAP_WEEKS * DAYS_PER_WEEK; index += 1) {
    const date = addDays(calendarStart, index);
    const dateKey = localDateKey(date);
    const record = activity.get(dateKey) || emptyRecord(dateKey);
    cells.push({ ...record, future: dateKey > today, today: dateKey === today });
  }

  const visibleRecords = cells.filter((cell) => !cell.future);
  const activeRecords = visibleRecords.filter(hasActivity);
  const tokenRecords = visibleRecords.filter((cell) => cell.tokens > 0);
  const unknownTokenRecords = visibleRecords.filter((cell) => cell.hasUnknownTokenActivity);
  const tokenScale = logarithmicTokenScale(tokenRecords.map((cell) => cell.tokens));
  const totalTokens = tokenRecords.reduce((total, cell) => total + cell.tokens, 0);
  const sourceName = source === "codex"
    ? "Codex"
    : source === "claude"
      ? "Claude Code"
      : source === "deepseek"
        ? "DeepSeek"
        : source === "antigravity"
          ? "Antigravity"
          : source === "workbuddy"
            ? "WorkBuddy"
            : "全部来源";

  return `
    <section class="tokenStatsActivityCard" data-token-activity-root aria-labelledby="token-stats-activity-title">
      <div class="tokenStatsActivityHeader">
        <div>
          <h2 id="token-stats-activity-title">使用日历</h2>
          <p>过去 53 周 · ${activeRecords.length} 个活跃日 · 可确认 ${formatTokens(totalTokens)}${unknownTokenRecords.length ? ` · ${unknownTokenRecords.length} 日含 Token 未知活动` : ""}${loading ? " · 正在读取年度数据…" : error ? ` · ${escapeHtml(error)}` : ""}</p>
        </div>
        <div class="tokenStatsActivityLegend" aria-label="每日可确认 Token 对数色阶与 Token 未知活动图例">
          <span class="tokenStatsActivityLegendLabel">每日 Token（对数）</span>
          ${tokenScaleLegendMarkup(tokenScale)}
          <span class="tokenStatsActivityLegendDivider" aria-hidden="true"></span>
          <span class="tokenStatsActivityLegendItem"><i class="is-activity-only"></i><b>Token 未知</b></span>
        </div>
      </div>
      <div class="tokenStatsActivityScroll" data-token-activity-scroll tabindex="0" aria-label="过去 53 周每日 Token 使用量与恢复的历史活动，可横向滚动">
        <div class="tokenStatsActivityInner" style="--token-activity-week-count:${HEATMAP_WEEKS}">
          <div class="tokenStatsActivityMonths" aria-hidden="true">
            ${monthLabels(calendarStart, calendarEnd)}
          </div>
          <div class="tokenStatsActivityBody">
            <div class="tokenStatsActivityWeekdays" aria-hidden="true">
              <span>一</span><span></span><span>三</span><span></span><span>五</span><span></span><span>日</span>
            </div>
            <div class="tokenStatsActivityGrid">
              ${cells.map((cell) => activityCellMarkup(cell, tokenScale)).join("")}
            </div>
          </div>
        </div>
      </div>
      <div class="tokenStatsActivityReadout" aria-live="polite">
        <strong data-token-activity-date>每日使用记录</strong>
        <span data-token-activity-value>悬停、聚焦或点击日期方块，查看可确认 Token 与历史活动</span>
      </div>
      <p class="tokenStatsActivityScope">${escapeHtml(sourceName)}的年度记录 · 色阶以当前来源峰值为上限，在最近 3 个数量级内按对数均分 · Token 未知活动不计入合计 · 不受日期、Provider 和模型筛选影响</p>
    </section>
  `;
}

export function bindActivityHeatmap(root) {
  const section = root?.querySelector("[data-token-activity-root]");
  if (!section) return;

  const dateOutput = section.querySelector("[data-token-activity-date]");
  const valueOutput = section.querySelector("[data-token-activity-value]");
  const scroll = section.querySelector("[data-token-activity-scroll]");
  let selected = null;

  const show = (button) => {
    if (!button) {
      dateOutput.textContent = "每日使用记录";
      valueOutput.textContent = "悬停、聚焦或点击日期方块，查看可确认 Token 与历史活动";
      return;
    }
    dateOutput.textContent = button.dataset.dateLabel || button.dataset.date;
    const tokens = nonnegativeNumber(button.dataset.tokens);
    const unknownTokenActivity = button.dataset.unknownTokens === "true";
    const dayHasActivity = button.dataset.hasActivity === "true";
    const facts = !dayHasActivity
      ? ["无使用记录"]
      : unknownTokenActivity && tokens <= 0
        ? ["已恢复历史活动，Token 未知"]
        : [unknownTokenActivity ? `可确认 ${formatTokens(tokens)}` : `使用了 ${formatTokens(tokens)}`];
    const authoritativeSessions = nonnegativeNumber(button.dataset.authoritativeSessions);
    const restoredSessions = nonnegativeNumber(button.dataset.restoredSessions);
    const unknownTokenSessions = nonnegativeNumber(button.dataset.unknownTokenSessions);
    const sessions = nonnegativeNumber(button.dataset.sessions);
    if (dayHasActivity && authoritativeSessions > 0) facts.push(`${formatInteger(authoritativeSessions)} 个可核对日志会话`);
    if (restoredSessions > 0) facts.push(`${formatInteger(restoredSessions)} 个恢复会话`);
    if (!authoritativeSessions && !restoredSessions && sessions > 0) facts.push(`${formatInteger(sessions)} 个会话`);
    if (unknownTokenActivity && unknownTokenSessions > 0) facts.push(`其中 ${formatInteger(unknownTokenSessions)} 个会话无精确 Token`);
    else if (unknownTokenActivity && tokens > 0) facts.push("同日仍有部分活动的 Token 未知");
    valueOutput.textContent = facts.join(" · ");
  };

  section.querySelectorAll("[data-token-activity-day]").forEach((button) => {
    button.addEventListener("pointerenter", () => show(button));
    button.addEventListener("pointerleave", () => show(selected));
    button.addEventListener("focus", () => show(button));
    button.addEventListener("blur", () => show(selected));
    button.addEventListener("click", () => {
      selected?.classList.remove("is-selected");
      selected = selected === button ? null : button;
      selected?.classList.add("is-selected");
      show(selected);
    });
  });

  requestAnimationFrame(() => {
    if (scroll) scroll.scrollLeft = scroll.scrollWidth;
  });
}

function normalizeActivity(summary, source, fallbackTrend) {
  const root = summary?.data && typeof summary.data === "object" ? summary.data : summary || {};
  const sourceRows = {
    codex: rowsFrom(root.codex || root.sources?.codex),
    claude: rowsFrom(root.claude || root.sources?.claude),
    deepseek: rowsFrom(root.deepseek || root.sources?.deepseek),
    antigravity: rowsFrom(root.antigravity || root.sources?.antigravity),
    workbuddy: rowsFrom(root.workbuddy || root.sources?.workbuddy),
  };
  const selectedRows = source === "codex"
    ? sourceRows.codex
    : source === "claude"
      ? sourceRows.claude
      : source === "deepseek"
        ? sourceRows.deepseek
        : source === "antigravity"
          ? sourceRows.antigravity
          : source === "workbuddy"
            ? sourceRows.workbuddy
            : sourceRows.codex.concat(sourceRows.claude, sourceRows.deepseek, sourceRows.antigravity, sourceRows.workbuddy);
  const activity = new Map();

  selectedRows.forEach((row) => mergeRecord(activity, {
    date: String(row?.date || row?.day || "").slice(0, 10),
    tokens: nonnegativeNumber(row?.tokens ?? row?.totalTokens ?? row?.total_tokens),
    sessions: nonnegativeNumber(row?.sessions ?? row?.requests ?? row?.requestCount),
    authoritativeSessions: nonnegativeNumber(row?.authoritativeSessions ?? row?.authoritative_sessions),
    restoredSessions: nonnegativeNumber(row?.restoredSessions ?? row?.restored_sessions ?? row?.legacySessions ?? row?.legacy_sessions),
    unknownTokenSessions: nonnegativeNumber(row?.unknownTokenSessions ?? row?.unknown_token_sessions ?? row?.activityOnlySessions ?? row?.activity_only_sessions),
    hasUnknownTokenActivity: rowHasUnknownTokenActivity(row),
  }));

  if (!selectedRows.length) {
    fallbackTrend.forEach((row) => mergeRecord(activity, {
      date: String(row?.label || row?.date || "").slice(0, 10),
      tokens: nonnegativeNumber(row?.totalTokens ?? row?.tokens),
      sessions: nonnegativeNumber(row?.requests ?? row?.sessions),
      authoritativeSessions: nonnegativeNumber(row?.authoritativeSessions ?? row?.authoritative_sessions),
      restoredSessions: nonnegativeNumber(row?.restoredSessions ?? row?.restored_sessions),
      unknownTokenSessions: nonnegativeNumber(row?.unknownTokenSessions ?? row?.unknown_token_sessions ?? row?.activityOnlySessions ?? row?.activity_only_sessions),
      hasUnknownTokenActivity: rowHasUnknownTokenActivity(row),
    }));
  }
  return activity;
}

function rowsFrom(source) {
  if (!source || typeof source !== "object") return [];
  return [source.days, source.daily, source.dailyTotals].find(Array.isArray) || [];
}

function mergeRecord(activity, record) {
  if (!validDate(record.date)) return;
  const current = activity.get(record.date) || emptyRecord(record.date);
  current.tokens += record.tokens;
  current.sessions += record.sessions;
  current.authoritativeSessions += record.authoritativeSessions;
  current.restoredSessions += record.restoredSessions;
  current.unknownTokenSessions += record.unknownTokenSessions;
  current.hasUnknownTokenActivity ||= record.hasUnknownTokenActivity;
  activity.set(record.date, current);
}

function emptyRecord(date) {
  return {
    date,
    tokens: 0,
    sessions: 0,
    authoritativeSessions: 0,
    restoredSessions: 0,
    unknownTokenSessions: 0,
    hasUnknownTokenActivity: false,
  };
}

function activityCellMarkup(cell, tokenScale) {
  if (cell.future) {
    return '<span class="tokenStatsActivityDay is-future" aria-hidden="true"></span>';
  }
  const level = heatLevel(cell.tokens, tokenScale);
  const dateLabel = formatDate(cell.date);
  const activityOnly = cell.hasUnknownTokenActivity && cell.tokens <= 0;
  const partialCoverage = cell.hasUnknownTokenActivity && cell.tokens > 0;
  const classNames = [cell.today ? "is-today" : "", activityOnly ? "is-activity-only" : "", partialCoverage ? "has-partial-token-coverage" : ""].filter(Boolean).join(" ");
  const restoredCount = cell.unknownTokenSessions || cell.restoredSessions || cell.sessions;
  const dayHasActivity = hasActivity(cell);
  const label = !dayHasActivity
    ? `${dateLabel}无使用记录`
    : activityOnly
      ? `${dateLabel}有${restoredCount ? ` ${formatInteger(restoredCount)} 个会话` : "历史活动"}，Token 未知`
      : partialCoverage
        ? `${dateLabel}可确认 ${formatTokens(cell.tokens)}，同日部分活动 Token 未知`
        : `${dateLabel}使用了 ${formatTokens(cell.tokens)}`;
  return `
    <button class="tokenStatsActivityDay${classNames ? ` ${classNames}` : ""}" type="button"
      data-level="${level}" data-token-activity-day data-date="${cell.date}"
      data-date-label="${escapeAttr(dateLabel)}" data-tokens="${cell.tokens}" data-sessions="${cell.sessions}"
      data-authoritative-sessions="${cell.authoritativeSessions}" data-restored-sessions="${cell.restoredSessions}"
      data-unknown-token-sessions="${cell.unknownTokenSessions}" data-unknown-tokens="${cell.hasUnknownTokenActivity}"
      data-has-activity="${dayHasActivity}"
      title="${escapeAttr(label)}" aria-label="${escapeAttr(label)}"></button>
  `;
}

function hasActivity(record) {
  return record.tokens > 0 || record.sessions > 0 || record.restoredSessions > 0 || record.hasUnknownTokenActivity;
}

function rowHasUnknownTokenActivity(row) {
  if (!row || typeof row !== "object") return false;
  if (row.tokenKnown === true || row.token_known === true) return false;
  const explicitUnknown = row.hasUnknownTokenActivity === true || row.has_unknown_token_activity === true
    || row.activityOnly === true || row.activity_only === true
    || row.tokenKnown === false || row.token_known === false;
  if (explicitUnknown) return true;
  const unknownSessions = nonnegativeNumber(
    row.unknownTokenSessions ?? row.unknown_token_sessions ?? row.activityOnlySessions ?? row.activity_only_sessions,
  );
  const restoredSessions = nonnegativeNumber(row.restoredSessions ?? row.restored_sessions ?? row.legacySessions ?? row.legacy_sessions);
  const tokens = nonnegativeNumber(row.tokens ?? row.totalTokens ?? row.total_tokens);
  const sessions = nonnegativeNumber(row.sessions ?? row.requests ?? row.requestCount);
  return unknownSessions > 0 || (tokens <= 0 && (restoredSessions > 0 || sessions > 0));
}

function logarithmicTokenScale(values) {
  const maxTokens = Math.max(0, ...values.map(nonnegativeNumber));
  if (maxTokens <= 0) return { maxTokens: 0, thresholds: [] };
  const thresholds = Array.from({ length: HEATMAP_LEVELS - 1 }, (_, index) => {
    const level = index + 1;
    const remainingOrders = LOG_SCALE_ORDERS * (HEATMAP_LEVELS - level) / HEATMAP_LEVELS;
    return maxTokens / (10 ** remainingOrders);
  });
  return { maxTokens, thresholds };
}

function heatLevel(tokens, scale) {
  if (tokens <= 0) return 0;
  const thresholdIndex = scale.thresholds.findIndex((threshold) => tokens <= threshold);
  return thresholdIndex < 0 ? HEATMAP_LEVELS : thresholdIndex + 1;
}

function tokenScaleLegendMarkup(scale) {
  if (scale.maxTokens <= 0) return '<span class="tokenStatsActivityLegendEmpty">暂无可确认 Token</span>';
  const bounds = scale.thresholds;
  return Array.from({ length: HEATMAP_LEVELS }, (_, index) => {
    const level = index + 1;
    const lower = index > 0 ? bounds[index - 1] : 0;
    const upper = index < bounds.length ? bounds[index] : scale.maxTokens;
    const label = index === 0
      ? `≤${formatLegendTokens(upper)}`
      : index === HEATMAP_LEVELS - 1
        ? `>${formatLegendTokens(lower)}`
        : `${formatLegendTokens(lower)}–${formatLegendTokens(upper)}`;
    return `<span class="tokenStatsActivityLegendItem" title="色阶 ${level}：${escapeAttr(label)}"><i data-level="${level}"></i><b>${escapeHtml(label)}</b></span>`;
  }).join("");
}

function formatLegendTokens(value) {
  return new Intl.NumberFormat("zh-CN", {
    notation: "compact",
    maximumSignificantDigits: 2,
  }).format(nonnegativeNumber(value));
}

function monthLabels(start, end) {
  const labels = [];
  let cursor = new Date(start.getFullYear(), start.getMonth(), 1);
  if (cursor < start) cursor = new Date(start.getFullYear(), start.getMonth() + 1, 1);
  labels.push({ date: start, label: `${start.getMonth() + 1}月` });
  while (cursor <= end) {
    const week = Math.floor((cursor - start) / 86_400_000 / DAYS_PER_WEEK) + 1;
    if (week > 1) labels.push({ date: cursor, label: cursor.getMonth() === 0 ? `${cursor.getFullYear()}年1月` : `${cursor.getMonth() + 1}月` });
    cursor = new Date(cursor.getFullYear(), cursor.getMonth() + 1, 1);
  }
  return labels.map(({ date, label }) => {
    const week = Math.floor((date - start) / 86_400_000 / DAYS_PER_WEEK) + 1;
    return `<span style="grid-column:${Math.max(1, week)}">${label}</span>`;
  }).join("");
}

function parseDate(value) {
  const [year, month, day] = String(value).split("-").map(Number);
  return new Date(year, month - 1, day);
}

function addDays(date, days) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate() + days);
}

function endOfWeek(date) {
  const mondayOffset = (date.getDay() + 6) % 7;
  return addDays(date, 6 - mondayOffset);
}

function validDate(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  return localDateKey(parseDate(value)) === value;
}

function localDateKey(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function formatDate(value) {
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "long",
    day: "numeric",
    weekday: "short",
  }).format(parseDate(value));
}

function formatTokens(value) {
  return `${formatInteger(value)} Token`;
}

function formatInteger(value) {
  return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 }).format(nonnegativeNumber(value));
}

function nonnegativeNumber(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 0;
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
