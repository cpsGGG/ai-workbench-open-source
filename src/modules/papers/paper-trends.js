const TOPICS = [
  ["large-models", "大模型"],
  ["world-models", "世界模型"],
  ["robotics", "机器人"],
  ["computer-vision", "计算机视觉"],
  ["machine-learning", "机器学习"],
  ["deep-learning", "深度学习"],
];

export function createPaperTrendsFeature(onChange) {
  const state = {
    items: [], sources: [], loading: false, error: "", warning: "", fetchedAt: "",
    stale: false, degradedDiversity: false, incomplete: false,
    topic: "", submittedQuery: "", searchInput: "", searchLabel: "为你推荐",
    likes: new Map(), likesRevision: 0, likesReady: false, likesError: "", likeSaving: new Set(),
  };
  let requestSequence = 0;
  let activeController = null;

  async function ensureLikes() {
    if (state.likesReady || state.likesError) return;
    try {
      const response = await fetch("/api/paper-trend-likes", { cache: "no-store" });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || data.ok === false || !Array.isArray(data.likes)) throw new Error(data.error || "点赞数据加载失败");
      applyLikesState(data);
    } catch (error) {
      state.likesError = String(error?.message || "点赞数据加载失败");
      state.likesReady = false;
    }
  }

  function applyLikesState(data) {
    state.likes = new Map((data.likes || []).map((item) => [item.id, item]));
    state.likesRevision = Number.isInteger(data.revision) ? data.revision : 0;
    state.likesReady = true;
    state.likesError = "";
  }

  async function load({ force = true } = {}) {
    if (state.loading) return;
    await ensureLikes();
    const sequence = ++requestSequence;
    activeController?.abort();
    const controller = new AbortController();
    activeController = controller;
    const timeout = window.setTimeout(() => controller.abort("timeout"), 20_000);
    state.loading = true;
    state.error = "";
    state.warning = "";
    onChange();
    try {
      const params = new URLSearchParams();
      if (force) params.set("refresh", "1");
      if (state.topic) params.set("topic", state.topic);
      if (state.submittedQuery) params.set("query", state.submittedQuery);
      const response = await fetch(`/api/paper-trends?${params}`, { cache: "no-store", signal: controller.signal });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || data.ok === false) throw new Error(humanError(data.error, response.status));
      if (!Array.isArray(data.items) || data.items.length > 20 || data.limit !== 20) throw new Error("抓取结果格式不正确");
      if (sequence !== requestSequence) return;
      state.items = data.items;
      state.sources = Array.isArray(data.sources) ? data.sources : [];
      state.fetchedAt = String(data.fetchedAt || "");
      state.stale = Boolean(data.stale);
      state.degradedDiversity = Boolean(data.degradedDiversity);
      state.incomplete = Boolean(data.incomplete);
      state.warning = String(data.warning || "");
      state.searchLabel = String(data.search?.label || state.submittedQuery || topicLabel(state.topic) || "为你推荐");
      state.topic = String(data.search?.topic || "");
      state.submittedQuery = String(data.search?.query || "");
    } catch (error) {
      if (sequence !== requestSequence) return;
      const timedOut = controller.signal.aborted && controller.signal.reason === "timeout";
      state.error = timedOut ? "抓取超时，请检查网络后重试。" : String(error?.message || "抓取失败，请稍后重试。");
    } finally {
      window.clearTimeout(timeout);
      if (sequence === requestSequence) {
        state.loading = false;
        activeController = null;
        onChange();
      }
    }
  }

  function chooseTopic(topic) {
    if (state.loading || state.topic === topic && !state.submittedQuery) return;
    state.topic = topic;
    state.submittedQuery = "";
    state.searchInput = "";
    state.items = [];
    load({ force: true });
  }

  function submitSearch(form) {
    const query = String(new FormData(form).get("query") || "").replace(/\s+/g, " ").trim();
    if (!query) return;
    if (query.length > 48 || new TextEncoder().encode(query).length > 144) {
      state.error = "搜索词最多 48 个字符（UTF-8 不超过 144 字节）。";
      onChange();
      return;
    }
    state.topic = "";
    state.submittedQuery = query;
    state.searchInput = query;
    state.items = [];
    load({ force: true });
  }

  async function toggleLike(item) {
    if (!state.likesReady || state.likeSaving.has(item.id)) return;
    const desired = !state.likes.has(item.id);
    const before = new Map(state.likes);
    desired ? state.likes.set(item.id, item) : state.likes.delete(item.id);
    state.likeSaving.add(item.id);
    onChange();
    let baseRevision = state.likesRevision;
    let conflictState = null;
    try {
      for (let attempt = 0; attempt < 2; attempt += 1) {
        const response = await fetch("/api/paper-trend-likes", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ item: likeRecord(item), liked: desired, baseRevision }),
        });
        const data = await response.json().catch(() => ({}));
        if (response.status === 409 && data.state) {
          conflictState = data.state;
          applyLikesState(data.state);
          if (attempt === 0) {
            desired ? state.likes.set(item.id, item) : state.likes.delete(item.id);
            baseRevision = state.likesRevision;
            continue;
          }
        }
        if (!response.ok || data.ok === false) throw new Error(data.error || "点赞保存失败");
        applyLikesState(data);
        return;
      }
    } catch (error) {
      if (conflictState) applyLikesState(conflictState);
      else state.likes = before;
      state.warning = `点赞未保存：${String(error?.message || "请稍后重试")}`;
    } finally {
      state.likeSaving.delete(item.id);
      onChange();
    }
  }

  function cancel() {
    requestSequence += 1;
    activeController?.abort();
    activeController = null;
    state.loading = false;
  }

  function render() {
    const workingSources = state.sources.filter((source) => source?.ok).length;
    const failedSources = state.sources.filter((source) => source && !source.ok);
    const sourceSummary = state.sources.length ? `${workingSources}/${state.sources.length} 个来源可用` : "将并行读取多个公开来源";
    const fetchedAt = formatDateTime(state.fetchedAt);
    return `
      <section class="paperTrendPanel" aria-busy="${state.loading}" aria-labelledby="paper-trend-title">
        <div class="paperTrendIntro">
          <div><p class="eyebrow">Fresh &amp; Notable</p><h2 id="paper-trend-title">AI 研究抓取</h2>
            <p>选择一个领域或输入任意关键词，抓取最多 20 条明确相关的新论文、技术报告和官方文章；默认推荐会参考你的点赞偏好。</p></div>
          <button class="primaryButton" id="retry-paper-trends" type="button" ${state.loading ? "disabled" : ""}>
            <span>${state.loading ? "…" : "↻"}</span><span>${state.loading ? "正在抓取" : state.items.length ? "重新抓取" : "开始抓取"}</span>
          </button>
        </div>
        <div class="paperTrendFilters" aria-label="研究领域">
          <button class="paperTrendTopic ${!state.topic && !state.submittedQuery ? "selected" : ""}" data-trend-topic="" type="button" aria-pressed="${!state.topic && !state.submittedQuery}">为你推荐</button>
          ${TOPICS.map(([key, label]) => `<button class="paperTrendTopic ${state.topic === key ? "selected" : ""}" data-trend-topic="${key}" type="button" aria-pressed="${state.topic === key}">${label}</button>`).join("")}
        </div>
        <form class="paperTrendSearch" id="paper-trend-search">
          <label for="paper-trend-query">自定义搜索</label>
          <div><input id="paper-trend-query" name="query" maxlength="48" value="${escapeAttr(state.searchInput)}" placeholder="例如：多模态智能体、world model" ${state.loading ? "disabled" : ""} />
          <button class="secondaryButton" type="submit" ${state.loading ? "disabled" : ""}>搜索 20 条</button></div>
        </form>
        <div class="paperTrendStatus" role="status" aria-live="polite">
          <span>${state.loading ? `正在抓取“${escapeHtml(state.submittedQuery || topicLabel(state.topic) || "为你推荐")}”…` : `${escapeHtml(state.searchLabel)} · ${state.items.length ? `${state.items.length} 条` : sourceSummary}`}</span>
          <span>${fetchedAt ? `${state.stale ? "上次同条件成功" : "更新于"} ${escapeHtml(fetchedAt)}` : "不需要 API Key"}</span>
        </div>
        ${state.likesError ? `<div class="paperTrendNotice error" role="alert">点赞数据未能从本机服务加载，已禁用点赞，避免覆盖已有数据。</div>` : ""}
        ${state.error ? `<div class="paperTrendNotice error" role="alert"><span>${escapeHtml(state.error)}${state.items.length ? " 当前保留上次结果。" : ""}</span><button id="paper-trend-inline-retry" type="button" ${state.loading ? "disabled" : ""}>重试</button></div>` : ""}
        ${state.warning ? `<div class="paperTrendNotice warning" role="status">${escapeHtml(state.warning)}</div>` : failedSources.length ? `<div class="paperTrendNotice warning" role="status">部分来源暂时不可用（${failedSources.map((source) => escapeHtml(source.name)).join("、")}）。</div>` : state.degradedDiversity ? `<div class="paperTrendNotice warning">来源分布比平时更集中。</div>` : ""}
        ${state.items.length ? `<div class="paperTrendGrid">${state.items.map((item, index) => renderTrendCard(item, index, state)).join("")}</div>` : state.loading ? `<div class="paperTrendGrid paperTrendSkeletonGrid" aria-hidden="true">${Array.from({ length: 20 }, (_, index) => renderSkeleton(index + 1)).join("")}</div>` : `<div class="paperTrendEmpty"><strong>选择领域，或搜索你关心的主题</strong><p>只展示明确匹配内容；不足 20 条时会如实显示数量，不用无关文章凑数。</p></div>`}
      </section>`;
  }

  function bind(root) {
    root.querySelector("#retry-paper-trends")?.addEventListener("click", () => load({ force: true }));
    root.querySelector("#paper-trend-inline-retry")?.addEventListener("click", () => load({ force: true }));
    root.querySelector("#paper-trend-search")?.addEventListener("submit", (event) => { event.preventDefault(); submitSearch(event.currentTarget); });
    root.querySelector("#paper-trend-query")?.addEventListener("input", (event) => { state.searchInput = event.target.value; });
    root.querySelectorAll("[data-trend-topic]").forEach((button) => button.addEventListener("click", () => chooseTopic(button.dataset.trendTopic || "")));
    root.querySelectorAll("[data-trend-like]").forEach((button) => button.addEventListener("click", () => {
      const item = state.items.find((value) => value.id === button.dataset.trendLike);
      if (item) toggleLike(item);
    }));
    root.querySelectorAll("[data-trend-image]").forEach((image) => {
      const fallback = () => image.closest(".paperTrendVisual")?.classList.add("imageFailed");
      image.addEventListener("error", fallback, { once: true });
    });
  }
  return { bind, cancel, load, render };
}

function renderTrendCard(item, index, state) {
  const href = safeHref(item.url);
  const imageSrc = safeTrendImage(item.imageUrl, item.id);
  const visualLabel = `${item.itemType || "AI 研究"} · 来源暂未提供图片`;
  const rank = Number.isInteger(item.rank) ? item.rank : index + 1;
  const liked = state.likes.has(item.id);
  const saving = state.likeSaving.has(item.id);
  const disabled = !state.likesReady || saving;
  return `<article class="paperTrendCard" data-trend-id="${escapeAttr(item.id || String(rank))}">
    <div class="paperTrendRank" aria-label="第 ${rank} 名">${rank}</div><div class="paperTrendCardBody">
      <div class="paperTrendMeta"><span class="paperTrendType">${escapeHtml(["论文", "技术报告", "官方文章"].includes(item.itemType) ? item.itemType : "研究文章")}</span><span>${escapeHtml(item.source || "公开来源")}</span><time datetime="${escapeAttr(item.publishedAt || "")}">${escapeHtml(formatDate(item.publishedAt) || "日期待确认")}</time></div>
      <figure class="paperTrendVisual ${imageSrc ? "" : "imageFailed"}">${imageSrc ? `<img data-trend-image src="${escapeAttr(imageSrc)}" alt="${escapeAttr(item.title)}" loading="lazy" decoding="async" referrerpolicy="same-origin" />` : ""}<figcaption>${escapeHtml(visualLabel)}</figcaption></figure>
      <h3>${escapeHtml(item.title)}</h3>${item.authors ? `<p class="paperTrendAuthors">${escapeHtml(item.authors)}</p>` : ""}
      <p class="paperTrendSummary">${escapeHtml(item.summary || "来源暂未提供摘要，可打开原文查看详情。")}</p>
      <p class="paperTrendMatch">${escapeHtml(item.matchReason || "综合新鲜度与公开热度")}</p>
      <div class="paperTrendFooter"><span class="paperTrendHot"><span aria-hidden="true">↗</span>${escapeHtml(item.hotReason || "近期新发布")}</span><div class="paperTrendActions">
        <button class="paperTrendLike ${liked ? "liked" : ""}" data-trend-like="${escapeAttr(item.id)}" type="button" aria-pressed="${liked}" aria-label="${liked ? "取消喜欢" : "喜欢"}：${escapeAttr(item.title)}" ${disabled ? "disabled" : ""}><span aria-hidden="true">${liked ? "♥" : "♡"}</span>${saving ? "保存中" : liked ? "已喜欢" : "喜欢"}</button>
        ${href ? `<a class="secondaryButton" href="${escapeAttr(href)}" target="_blank" rel="noopener noreferrer"><span>查看原文</span><span aria-hidden="true">↗</span></a>` : `<span class="paperTrendUnavailable">原文链接不可用</span>`}
      </div></div></div></article>`;
}

function likeRecord(item) { return { id: item.id, url: item.url, title: item.title, summary: item.summary || "", source: item.source || "", topics: Array.isArray(item.topics) ? item.topics : [] }; }
function renderSkeleton(rank) { return `<div class="paperTrendCard paperTrendSkeleton"><div class="paperTrendRank">${rank}</div><div class="paperTrendCardBody"><span></span><strong></strong><p></p><p></p></div></div>`; }
function topicLabel(key) { return TOPICS.find(([value]) => value === key)?.[1] || ""; }
function humanError(value, status) { const labels = { query_too_long: "搜索词过长。", query_contains_unsupported_characters: "搜索词包含不支持的字符。", unknown_topic: "未知研究领域。" }; return labels[value] || value || `抓取服务返回 HTTP ${status}`; }
function formatDate(value) { if (!/^\d{4}-\d{2}-\d{2}$/.test(String(value || ""))) return ""; const date = new Date(`${value}T00:00:00Z`); return Number.isNaN(date.getTime()) ? "" : new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" }).format(date); }
function formatDateTime(value) { const date = new Date(value); return !value || Number.isNaN(date.getTime()) ? "" : new Intl.DateTimeFormat("zh-CN", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(date); }
function safeHref(value) { try { const url = new URL(String(value || "")); return ["http:", "https:"].includes(url.protocol) ? url.href : ""; } catch { return ""; } }
function safeTrendImage(value, id) { const expected = `/api/paper-trend-image?id=${encodeURIComponent(String(id || ""))}`; return String(value || "") === expected && /^[0-9a-f]{16}$/.test(String(id || "")) ? expected : ""; }
function escapeHtml(value) { return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;"); }
function escapeAttr(value) { return escapeHtml(value).replaceAll("`", "&#096;"); }
