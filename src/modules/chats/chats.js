const CHAT_HISTORY_ENDPOINTS = Object.freeze({
  summary: "/api/chat-history/summary",
  session: "/api/chat-history/session",
  openTerminal: "/api/chat-history/open-terminal",
});

export function createChatHistoryWorkspace(root) {
  if (!root) return;

  root.classList.add("chatHistoryWorkspace");

  const state = {
    data: null,
    selectedFolder: "ALL",
    expandedFolders: new Set(["ALL"]),
    source: "all",
    search: "",
    selectedKey: "",
    detailSession: null,
    detailMessages: [],
    activeTab: "summary",
    summaryBusy: false,
    summaryError: "",
    detailBusy: false,
    detailError: "",
    actionBusy: "",
  };

  let summaryController = null;
  let summaryRequestVersion = 0;
  let detailController = null;
  let detailRequestVersion = 0;
  let toastTimer = 0;

  root.innerHTML = `
    <header class="chatHistoryTopbar">
      <div class="chatHistoryHeading">
        <p class="chatHistoryEyebrow">History Desk</p>
        <h1>聊天记录</h1>
        <p class="chatHistoryScanStatus" data-chat-history-role="scan-status" aria-live="polite">
          正在扫描本机聊天记录...
        </p>
      </div>
      <button
        class="chatHistoryRefreshButton"
        type="button"
        data-chat-history-action="refresh"
        title="重新扫描 Codex 与 Claude Code 历史"
      >
        刷新
      </button>
    </header>

    <section class="chatHistoryMetrics" aria-label="聊天记录总览">
      ${metricMarkup("总会话", "total-sessions")}
      ${metricMarkup("目录", "total-folders")}
      ${metricMarkup("Codex token", "codex-tokens")}
      ${metricMarkup("Claude token", "claude-tokens")}
      ${metricMarkup("总 token", "total-tokens")}
    </section>

    <div class="chatHistoryLayout">
      <aside class="chatHistoryNavigator" aria-label="聊天记录筛选与目录">
        <div class="chatHistoryFilters">
          <label class="chatHistorySearch">
            <span class="chatHistorySearchIcon" aria-hidden="true">Q</span>
            <input
              type="search"
              data-chat-history-role="search"
              aria-label="搜索标题、路径或用户问题"
              placeholder="搜索标题、路径或用户问题"
            />
          </label>

          <div class="chatHistorySourceControl" role="group" aria-label="来源筛选">
            <button type="button" data-chat-history-action="source" data-source="all">全部</button>
            <button type="button" data-chat-history-action="source" data-source="codex">Codex</button>
            <button type="button" data-chat-history-action="source" data-source="claude">Claude</button>
          </div>
        </div>

        <div class="chatHistoryPaneHeader">
          <div>
            <span class="chatHistorySectionLabel">目录</span>
            <strong data-chat-history-role="folder-title">全部路径</strong>
          </div>
          <button
            class="chatHistoryPathButton"
            type="button"
            data-chat-history-action="open-folder"
            data-idle-label="打开路径"
            title="在终端中打开当前选择的具体路径"
          >
            打开路径
          </button>
        </div>

        <div class="chatHistoryFolderTree" data-chat-history-role="folder-tree"></div>
      </aside>

      <section class="chatHistorySessionsPane" aria-label="会话列表">
        <div class="chatHistoryPaneHeader">
          <div>
            <span class="chatHistorySectionLabel">会话</span>
            <strong data-chat-history-role="session-heading">全部记录</strong>
          </div>
          <span class="chatHistoryCount" data-chat-history-role="session-count">-</span>
        </div>
        <div class="chatHistorySessionList" data-chat-history-role="session-list"></div>
      </section>

      <aside class="chatHistoryDetailPane" data-chat-history-role="detail-pane" aria-label="会话详情"></aside>
    </div>

    <div class="chatHistoryToast" data-chat-history-role="toast" role="status" aria-live="polite" hidden></div>
  `;

  const elements = {
    scanStatus: queryRole("scan-status"),
    refreshButton: root.querySelector('[data-chat-history-action="refresh"]'),
    searchInput: queryRole("search"),
    folderTitle: queryRole("folder-title"),
    folderTree: queryRole("folder-tree"),
    sessionHeading: queryRole("session-heading"),
    sessionCount: queryRole("session-count"),
    sessionList: queryRole("session-list"),
    detailPane: queryRole("detail-pane"),
    toast: queryRole("toast"),
  };

  root.addEventListener("click", handleClick);
  elements.searchInput.addEventListener("input", (event) => {
    state.search = String(event.currentTarget.value || "");
    reconcileSelectedSession();
    renderFilteredView();
  });

  renderAll();
  void loadSummary(false);

  function queryRole(role) {
    return root.querySelector('[data-chat-history-role="' + role + '"]');
  }

  function handleClick(event) {
    const control = event.target.closest("[data-chat-history-action]");
    if (!control || !root.contains(control)) return;

    const action = control.dataset.chatHistoryAction;
    if (action === "refresh") {
      void loadSummary(true);
      return;
    }

    if (action === "source") {
      const nextSource = control.dataset.source;
      if (!["all", "codex", "claude"].includes(nextSource)) return;
      state.source = nextSource;
      reconcileSelectedSession();
      renderFilteredView();
      return;
    }

    if (action === "toggle-folder") {
      toggleFolder(control.dataset.path, control.dataset.hasChildren === "1");
      return;
    }

    if (action === "select-folder") {
      selectFolder(control.dataset.path, control.dataset.hasChildren === "1");
      return;
    }

    if (action === "select-session") {
      void selectSession(control.dataset.key);
      return;
    }

    if (action === "tab") {
      const nextTab = control.dataset.tab;
      if (!["summary", "transcript"].includes(nextTab)) return;
      state.activeTab = nextTab;
      renderDetail();
      return;
    }

    if (action === "open-session") {
      void openTerminal(control.dataset.mode, false);
      return;
    }

    if (action === "open-folder") {
      void openTerminal("terminal", true);
      return;
    }

    if (action === "retry-summary") {
      void loadSummary(false);
      return;
    }

    if (action === "retry-detail" && state.selectedKey) {
      void selectSession(state.selectedKey, true);
    }
  }

  async function loadSummary(refresh) {
    summaryController?.abort();
    summaryController = new AbortController();
    const requestVersion = ++summaryRequestVersion;

    if (refresh) clearSelectedSession();
    state.summaryBusy = true;
    state.summaryError = "";
    renderAll();

    try {
      const url = CHAT_HISTORY_ENDPOINTS.summary + (refresh ? "?refresh=1" : "");
      const payload = await fetchJson(url, { signal: summaryController.signal });
      if (requestVersion !== summaryRequestVersion) return;

      const sessions = Array.isArray(payload?.sessions) ? payload.sessions : [];
      state.data = { ...payload, sessions };

      if (
        state.selectedFolder !== "ALL" &&
        !sessions.some((session) => pathPrefixMatch(session.cwd, state.selectedFolder))
      ) {
        state.selectedFolder = "ALL";
      }

      state.expandedFolders = defaultExpandedFolders(sessions);
      reconcileSelectedSession();
    } catch (error) {
      if (error?.name === "AbortError" || requestVersion !== summaryRequestVersion) return;
      state.summaryError = error?.message || "扫描失败";
      showToast(state.summaryError);
    } finally {
      if (requestVersion === summaryRequestVersion) {
        state.summaryBusy = false;
        renderAll();
      }
    }
  }

  async function selectSession(key, force = false) {
    const normalizedKey = String(key || "");
    if (!normalizedKey) return;
    if (!force && state.selectedKey === normalizedKey && state.detailSession && !state.detailError) return;

    detailController?.abort();
    detailController = new AbortController();
    const requestVersion = ++detailRequestVersion;

    state.selectedKey = normalizedKey;
    state.detailSession = null;
    state.detailMessages = [];
    state.detailBusy = true;
    state.detailError = "";
    state.activeTab = "summary";
    renderSessions();
    renderDetail();

    try {
      const payload = await fetchJson(
        CHAT_HISTORY_ENDPOINTS.session + "/" + encodeURIComponent(normalizedKey),
        { signal: detailController.signal },
      );
      if (requestVersion !== detailRequestVersion || state.selectedKey !== normalizedKey) return;
      if (!payload?.session) throw new Error("会话详情数据不完整");

      state.detailSession = payload.session;
      state.detailMessages = Array.isArray(payload.messages) ? payload.messages : [];
    } catch (error) {
      if (error?.name === "AbortError" || requestVersion !== detailRequestVersion) return;
      state.detailError = error?.message || "读取会话详情失败";
    } finally {
      if (requestVersion === detailRequestVersion && state.selectedKey === normalizedKey) {
        state.detailBusy = false;
        renderSessions();
        renderDetail();
      }
    }
  }

  async function openTerminal(mode, useFolder) {
    if (state.actionBusy) return;

    const body = { mode };
    if (useFolder) {
      if (state.selectedFolder === "ALL") {
        showToast("请先选择一个具体目录。");
        return;
      }
      body.cwd = state.selectedFolder;
    } else {
      const key = state.detailSession?.key || state.selectedKey;
      if (!key) {
        showToast("请先选择一条聊天记录。");
        return;
      }
      body.key = key;
    }

    state.actionBusy = useFolder ? "folder" : mode;
    syncActionButtons();

    try {
      await fetchJson(CHAT_HISTORY_ENDPOINTS.openTerminal, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      showToast(useFolder ? "已打开当前路径。" : "打开指令已发送。");
    } catch (error) {
      showToast(error?.message || "打开失败");
    } finally {
      state.actionBusy = "";
      syncActionButtons();
    }
  }

  function toggleFolder(path, hasChildren) {
    if (!path || path === "ALL" || !hasChildren) return;
    if (state.expandedFolders.has(path)) {
      collapseFolder(path);
    } else {
      state.expandedFolders.add(path);
    }
    renderFolders();
  }

  function selectFolder(path, hasChildren) {
    state.selectedFolder = path || "ALL";
    if (hasChildren && path) state.expandedFolders.add(path);
    reconcileSelectedSession();
    renderFilteredView();
  }

  function clearSelectedSession() {
    detailController?.abort();
    detailController = null;
    detailRequestVersion += 1;
    state.selectedKey = "";
    state.detailSession = null;
    state.detailMessages = [];
    state.detailBusy = false;
    state.detailError = "";
    state.activeTab = "summary";
  }

  function reconcileSelectedSession() {
    if (!state.selectedKey) return;
    const remainsVisible = filteredSessions().some((session) => session.key === state.selectedKey);
    if (!remainsVisible) clearSelectedSession();
  }

  function collapseFolder(path) {
    const normalized = String(path).toLowerCase();
    const prefix = normalized + "\\";
    for (const expanded of Array.from(state.expandedFolders)) {
      const candidate = expanded.toLowerCase();
      if (candidate === normalized || candidate.startsWith(prefix)) {
        state.expandedFolders.delete(expanded);
      }
    }
  }

  function renderAll() {
    renderHeader();
    renderMetrics();
    renderSourceControls();
    renderFolders();
    renderSessions();
    renderDetail();
  }

  function renderFilteredView() {
    renderSourceControls();
    renderFolders();
    renderSessions();
    renderDetail();
  }

  function renderHeader() {
    elements.refreshButton.disabled = state.summaryBusy;
    elements.refreshButton.textContent = state.summaryBusy ? "扫描中..." : "刷新";

    if (state.summaryBusy) {
      elements.scanStatus.textContent = state.data ? "正在重新扫描本机聊天记录..." : "正在扫描本机聊天记录...";
      elements.scanStatus.classList.remove("chatHistoryStatusError");
      return;
    }

    if (state.summaryError) {
      elements.scanStatus.textContent = "扫描失败 · " + state.summaryError;
      elements.scanStatus.classList.add("chatHistoryStatusError");
      return;
    }

    elements.scanStatus.classList.remove("chatHistoryStatusError");
    if (!state.data) {
      elements.scanStatus.textContent = "等待扫描";
      return;
    }

    const count = safeNumber(state.data.totals?.sessions ?? state.data.sessions.length);
    elements.scanStatus.textContent =
      "已扫描 Codex / Claude Code · " + fmtNumber(count) + " 条 · " + fmtDate(state.data.generatedAt);
  }

  function renderMetrics() {
    const totals = state.data?.totals || {};
    const codex = totals.sources?.codex || {};
    const claude = totals.sources?.claude || {};

    setMetric("total-sessions", state.data ? fmtNumber(totals.sessions ?? state.data.sessions.length) : "-");
    setMetric("total-folders", state.data ? fmtNumber(totals.folders ?? 0) : "-");
    setMetric(
      "codex-tokens",
      state.data ? fmtToken(codex.tokenTotal || 0) : "-",
      state.data ? fmtUsageBreakdown(codex.tokenUsage) : "",
    );
    setMetric(
      "claude-tokens",
      state.data ? fmtToken(claude.tokenTotal || 0) : "-",
      state.data ? fmtUsageBreakdown(claude.tokenUsage) : "",
    );
    setMetric("total-tokens", state.data ? fmtToken(totals.tokenTotal || 0) : "-");
  }

  function setMetric(role, value, title = "") {
    const node = root.querySelector('[data-chat-history-metric="' + role + '"]');
    if (!node) return;
    node.textContent = value;
    node.closest(".chatHistoryMetric").title = title;
  }

  function renderSourceControls() {
    root.querySelectorAll('[data-chat-history-action="source"]').forEach((button) => {
      const selected = button.dataset.source === state.source;
      button.classList.toggle("chatHistoryControlSelected", selected);
      button.setAttribute("aria-pressed", selected ? "true" : "false");
    });
  }

  function renderFolders() {
    elements.folderTitle.textContent =
      state.selectedFolder === "ALL" ? "全部路径" : shortPath(state.selectedFolder, 34);
    const folderButton = root.querySelector('[data-chat-history-action="open-folder"]');
    folderButton.disabled = state.selectedFolder === "ALL" || Boolean(state.actionBusy);

    if (!state.data && state.summaryBusy) {
      elements.folderTree.innerHTML = stateMarkup("正在读取目录...");
      return;
    }

    if (!state.data && state.summaryError) {
      elements.folderTree.innerHTML = stateMarkup("目录读取失败");
      return;
    }

    const tree = buildFolderTree(treeSessions());
    const visibleNodes = [];
    collectVisibleNodes(tree, visibleNodes);

    if (!tree.sessionCount) {
      elements.folderTree.innerHTML = stateMarkup("没有匹配的目录");
      return;
    }

    elements.folderTree.innerHTML = visibleNodes.map(folderMarkup).join("");
  }

  function renderSessions() {
    const sessions = filteredSessions();
    elements.sessionHeading.textContent =
      state.selectedFolder === "ALL" ? "全部记录" : shortPath(state.selectedFolder, 42);
    elements.sessionCount.textContent = state.data ? fmtNumber(sessions.length) + " 条" : "-";

    if (!state.data && state.summaryBusy) {
      elements.sessionList.innerHTML = stateMarkup("正在读取聊天记录...");
      return;
    }

    if (!state.data && state.summaryError) {
      elements.sessionList.innerHTML =
        stateMarkup("扫描失败", "retry-summary", "重新扫描");
      return;
    }

    if (!sessions.length) {
      elements.sessionList.innerHTML = stateMarkup("没有匹配的聊天记录");
      return;
    }

    elements.sessionList.innerHTML = sessions.map(sessionMarkup).join("");
  }

  function renderDetail() {
    if (!state.selectedKey) {
      elements.detailPane.innerHTML = stateMarkup("选择一条聊天记录后查看原文与 token 明细");
      return;
    }

    if (state.detailBusy) {
      elements.detailPane.innerHTML = stateMarkup("正在读取会话详情...");
      return;
    }

    if (state.detailError) {
      elements.detailPane.innerHTML =
        stateMarkup(state.detailError, "retry-detail", "重试");
      return;
    }

    const session = state.detailSession;
    if (!session) {
      elements.detailPane.innerHTML = stateMarkup("这条会话没有可显示的详情");
      return;
    }

    const source = sourceInfo(session.source);
    const userMessages = Array.isArray(session.userMessages) ? session.userMessages : [];
    const messages = state.detailMessages;
    const summaryContent = userMessages.length
      ? userMessages
          .map((message, index) => messageMarkup("user", message.text, message.timestamp, "问题 " + (index + 1)))
          .join("")
      : stateMarkup("这条记录里没有提取到用户问题");
    const transcriptContent = messages.length
      ? messages.map((message) => messageMarkup(message.role, message.text, message.timestamp)).join("")
      : stateMarkup("这条记录没有可显示的对话片段");

    elements.detailPane.innerHTML = `
      <div class="chatHistoryDetailHeader">
        <span class="chatHistorySourceBadge ${source.className}">${source.label}</span>
        <h2>${escapeHtml(session.title || "未命名会话")}</h2>
        <p title="${escapeAttr(session.cwd || "")}">${escapeHtml(session.cwd || "未知路径")}</p>
      </div>

      <div class="chatHistoryActions" aria-label="打开会话">
        ${actionButton("terminal", "打开终端")}
        ${actionButton("codex", "打开 Codex")}
        ${actionButton("codex-resume", "Codex resume")}
        ${actionButton("claude", "打开 Claude")}
      </div>

      <dl class="chatHistoryDetailStats">
        <div>
          <dt>Token</dt>
          <dd>${fmtToken(session.tokenTotal || tokenTotal(session.tokenUsage))}</dd>
        </div>
        <div>
          <dt>问答</dt>
          <dd>${fmtNumber(session.userTurnCount || 0)} 问 / ${fmtNumber(session.assistantTurnCount || 0)} 答</dd>
        </div>
        <div>
          <dt>更新时间</dt>
          <dd>${escapeHtml(fmtDate(session.updatedAt))}</dd>
        </div>
        <div>
          <dt>历史文件</dt>
          <dd title="${escapeAttr(session.file || "")}">${escapeHtml(shortPath(session.file || "-", 48))}</dd>
        </div>
      </dl>

      <p class="chatHistoryTokenBreakdown">${escapeHtml(fmtSessionUsage(session.tokenUsage))}</p>

      <div class="chatHistoryTabs" role="tablist" aria-label="详情视图">
        <button
          type="button"
          role="tab"
          data-chat-history-action="tab"
          data-tab="summary"
          aria-selected="${state.activeTab === "summary"}"
          class="${state.activeTab === "summary" ? "chatHistoryControlSelected" : ""}"
        >
          问题摘要
        </button>
        <button
          type="button"
          role="tab"
          data-chat-history-action="tab"
          data-tab="transcript"
          aria-selected="${state.activeTab === "transcript"}"
          class="${state.activeTab === "transcript" ? "chatHistoryControlSelected" : ""}"
        >
          对话片段
        </button>
      </div>

      <div class="chatHistoryTabContent">
        ${state.activeTab === "summary" ? summaryContent : transcriptContent}
      </div>
    `;

    syncActionButtons();
  }

  function syncActionButtons() {
    root.querySelectorAll('[data-chat-history-action="open-session"]').forEach((button) => {
      const busy = state.actionBusy === button.dataset.mode;
      button.disabled = Boolean(state.actionBusy);
      button.textContent = busy ? "打开中..." : button.dataset.idleLabel;
    });

    const folderButton = root.querySelector('[data-chat-history-action="open-folder"]');
    if (folderButton) {
      const busy = state.actionBusy === "folder";
      folderButton.disabled = state.selectedFolder === "ALL" || Boolean(state.actionBusy);
      folderButton.textContent = busy ? "打开中..." : folderButton.dataset.idleLabel;
    }
  }

  function showToast(message) {
    elements.toast.textContent = String(message || "");
    elements.toast.hidden = false;
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => {
      elements.toast.hidden = true;
    }, 2600);
  }

  function basicSessionMatches(session) {
    if (state.source !== "all" && session.source !== state.source) return false;
    const query = state.search.trim().toLowerCase();
    if (!query) return true;

    const haystack = [
      session.title,
      session.cwd,
      session.file,
      session.source,
      ...(Array.isArray(session.userMessages) ? session.userMessages.map((message) => message.text) : []),
    ]
      .map((value) => String(value || ""))
      .join("\n")
      .toLowerCase();
    return haystack.includes(query);
  }

  function filteredSessions() {
    return (state.data?.sessions || []).filter(
      (session) => basicSessionMatches(session) && pathPrefixMatch(session.cwd, state.selectedFolder),
    );
  }

  function treeSessions() {
    return (state.data?.sessions || []).filter(basicSessionMatches);
  }

  function buildFolderTree(sessions) {
    const tree = emptyTreeNode("全部路径", "ALL", 0);
    for (const session of sessions) {
      addSessionToNode(tree, session);
      let node = tree;
      let currentPath = "";
      for (const part of pathParts(session.cwd)) {
        currentPath = joinPathPart(currentPath, part);
        if (!node.children.has(part)) {
          node.children.set(part, emptyTreeNode(part, currentPath, node.depth + 1));
        }
        node = node.children.get(part);
        addSessionToNode(node, session);
      }
    }
    return tree;
  }

  function addSessionToNode(node, session) {
    node.sessionCount += 1;
    node.tokenTotal += safeNumber(session.tokenTotal);
  }

  function emptyTreeNode(label, fullPath, depth) {
    return {
      label,
      fullPath,
      depth,
      sessionCount: 0,
      tokenTotal: 0,
      children: new Map(),
    };
  }

  function collectVisibleNodes(node, rows) {
    rows.push(node);
    const expanded =
      node.fullPath === "ALL" || state.expandedFolders.has(node.fullPath) || Boolean(state.search.trim());
    if (!expanded) return;
    for (const child of sortedChildren(node)) collectVisibleNodes(child, rows);
  }

  function sortedChildren(node) {
    return Array.from(node.children.values()).sort((left, right) => {
      if (right.sessionCount !== left.sessionCount) return right.sessionCount - left.sessionCount;
      return left.label.localeCompare(right.label, "zh-CN");
    });
  }

  function defaultExpandedFolders(sessions) {
    const expanded = new Set(["ALL"]);
    for (const session of sessions) {
      let path = "";
      pathParts(session.cwd)
        .slice(0, 3)
        .forEach((part) => {
          path = joinPathPart(path, part);
          expanded.add(path);
        });
    }
    return expanded;
  }

  function folderMarkup(node) {
    const hasChildren = node.children.size > 0;
    const expanded =
      node.fullPath === "ALL" || state.expandedFolders.has(node.fullPath) || Boolean(state.search.trim());
    const selected = state.selectedFolder === node.fullPath;
    const depth = Math.max(0, node.depth - 1);
    const toggle = hasChildren && node.fullPath !== "ALL"
      ? `
          <button
            class="chatHistoryTreeToggle"
            type="button"
            data-chat-history-action="toggle-folder"
            data-path="${escapeAttr(node.fullPath)}"
            data-has-children="1"
            title="${expanded ? "收起目录" : "展开目录"}"
            aria-label="${expanded ? "收起目录" : "展开目录"}"
          >${expanded ? "⌄" : "›"}</button>
        `
      : '<span class="chatHistoryTreeSpacer" aria-hidden="true"></span>';

    return `
      <div
        class="chatHistoryTreeRow ${selected ? "chatHistoryTreeSelected" : ""}"
        style="--chat-history-depth: ${depth}"
      >
        ${toggle}
        <button
          class="chatHistoryFolderButton"
          type="button"
          data-chat-history-action="select-folder"
          data-path="${escapeAttr(node.fullPath)}"
          data-has-children="${hasChildren ? "1" : "0"}"
          title="${escapeAttr(node.fullPath === "ALL" ? "全部路径" : node.fullPath)}"
        >
          <span class="chatHistoryFolderName">${escapeHtml(node.label)}</span>
          <span class="chatHistoryFolderCount">${fmtNumber(node.sessionCount)}</span>
          <span class="chatHistoryFolderToken">${fmtToken(node.tokenTotal)}</span>
        </button>
      </div>
    `;
  }

  function sessionMarkup(session) {
    const source = sourceInfo(session.source);
    const preview =
      session.userMessages?.[0]?.text ||
      session.assistantSnippets?.[0]?.text ||
      "暂无原文预览";
    const selected = state.selectedKey === session.key;

    return `
      <button
        class="chatHistorySession ${selected ? "chatHistorySessionSelected" : ""}"
        type="button"
        data-chat-history-action="select-session"
        data-key="${escapeAttr(session.key)}"
      >
        <span class="chatHistorySessionTitleRow">
          <strong>${escapeHtml(session.title || "未命名会话")}</strong>
          <span class="chatHistorySourceBadge ${source.className}">${source.label}</span>
        </span>
        <span class="chatHistorySessionPreview">${escapeHtml(preview)}</span>
        <span class="chatHistorySessionPath" title="${escapeAttr(session.cwd || "")}">
          ${escapeHtml(shortPath(session.cwd || "未知路径", 66))}
        </span>
        <span class="chatHistorySessionMeta">
          <span>${fmtToken(session.tokenTotal || 0)} token</span>
          <time datetime="${escapeAttr(session.updatedAt || "")}">${escapeHtml(fmtDate(session.updatedAt))}</time>
        </span>
      </button>
    `;
  }

  function messageMarkup(role, text, timestamp, forcedLabel = "") {
    const normalizedRole = role === "assistant" ? "assistant" : "user";
    const label = forcedLabel || (normalizedRole === "assistant" ? "助手" : "你");
    const timestampText = timestamp ? " · " + fmtDate(timestamp) : "";
    return `
      <article class="chatHistoryMessage ${normalizedRole === "assistant" ? "chatHistoryRoleAssistant" : "chatHistoryRoleUser"}">
        <header>${escapeHtml(label + timestampText)}</header>
        <p>${escapeHtml(text || "")}</p>
      </article>
    `;
  }

  function actionButton(mode, label) {
    return `
      <button
        type="button"
        data-chat-history-action="open-session"
        data-mode="${escapeAttr(mode)}"
        data-idle-label="${escapeAttr(label)}"
        title="${escapeAttr(label)}"
      >${escapeHtml(label)}</button>
    `;
  }
}

function metricMarkup(label, role) {
  return `
    <div class="chatHistoryMetric">
      <span>${escapeHtml(label)}</span>
      <strong data-chat-history-metric="${escapeAttr(role)}">-</strong>
    </div>
  `;
}

function stateMarkup(message, action = "", actionLabel = "") {
  return `
    <div class="chatHistoryState">
      <p>${escapeHtml(message)}</p>
      ${
        action
          ? `<button type="button" data-chat-history-action="${escapeAttr(action)}">${escapeHtml(actionLabel)}</button>`
          : ""
      }
    </div>
  `;
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload?.error || "请求失败（HTTP " + response.status + "）");
  return payload;
}

function sourceInfo(source) {
  if (source === "codex") {
    return { label: "Codex", className: "chatHistorySourceCodex" };
  }
  if (source === "claude") {
    return { label: "Claude", className: "chatHistorySourceClaude" };
  }
  return { label: "未知", className: "chatHistorySourceUnknown" };
}

function pathParts(value) {
  const raw = String(value || "Unknown").replaceAll("/", "\\").replace(/\\+$/, "");
  if (!raw || raw === "Unknown") return ["Unknown"];

  const drive = raw.match(/^[A-Za-z]:/);
  if (drive) {
    const rest = raw.slice(drive[0].length).replace(/^\\+/, "");
    return [drive[0].toUpperCase(), ...rest.split("\\").filter(Boolean)];
  }
  return raw.split("\\").filter(Boolean);
}

function joinPathPart(parent, part) {
  if (!parent || parent === "ALL") return part;
  if (/^[A-Za-z]:$/.test(parent)) return parent + "\\" + part;
  return parent + "\\" + part;
}

function pathPrefixMatch(value, prefix) {
  if (prefix === "ALL") return true;
  const normalizedValue = String(value || "").replaceAll("/", "\\").replace(/\\+$/, "").toLowerCase();
  const normalizedPrefix = String(prefix || "").replaceAll("/", "\\").replace(/\\+$/, "").toLowerCase();
  return normalizedValue === normalizedPrefix || normalizedValue.startsWith(normalizedPrefix + "\\");
}

function safeNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : 0;
}

function fmtNumber(value) {
  return new Intl.NumberFormat("zh-CN").format(safeNumber(value));
}

function fmtToken(value) {
  const number = safeNumber(value);
  const absolute = Math.abs(number);
  if (absolute >= 1_000_000_000) return (number / 1_000_000_000).toFixed(2) + "B";
  if (absolute >= 1_000_000) return (number / 1_000_000).toFixed(2) + "M";
  if (absolute >= 1_000) return (number / 1_000).toFixed(2) + "K";
  return fmtNumber(number);
}

function fmtUsageBreakdown(usage) {
  if (!usage) return "输入 0 · 缓存 0 · 输出 0";
  return (
    "输入 " +
    fmtToken(usage.input || 0) +
    " · 缓存 " +
    fmtToken(usage.cached || 0) +
    " · 输出 " +
    fmtToken(usage.output || 0)
  );
}

function tokenTotal(usage) {
  if (!usage) return 0;
  if (safeNumber(usage.total_tokens) > 0) return safeNumber(usage.total_tokens);
  return (
    safeNumber(usage.input_tokens) +
    safeNumber(usage.cached_input_tokens) +
    safeNumber(usage.cache_creation_input_tokens) +
    safeNumber(usage.cache_read_input_tokens) +
    safeNumber(usage.output_tokens) +
    safeNumber(usage.reasoning_output_tokens)
  );
}

function fmtSessionUsage(usage) {
  if (!usage) return "总计 0 · 输入 0 · 缓存 0 · 输出 0";
  const cached =
    safeNumber(usage.cached_input_tokens) +
    safeNumber(usage.cache_creation_input_tokens) +
    safeNumber(usage.cache_read_input_tokens);
  const parts = [
    "总计 " + fmtToken(tokenTotal(usage)),
    "输入 " + fmtToken(usage.input_tokens || 0),
    "缓存 " + fmtToken(cached),
    "输出 " + fmtToken(usage.output_tokens || 0),
  ];
  if (safeNumber(usage.reasoning_output_tokens)) {
    parts.push("推理 " + fmtToken(usage.reasoning_output_tokens));
  }
  return parts.join(" · ");
}

function fmtDate(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function shortPath(value, max = 56) {
  const text = String(value || "未知");
  if (text.length <= max) return text;
  const leftLength = Math.max(12, Math.floor(max * 0.42));
  const rightLength = Math.max(14, max - leftLength - 3);
  return text.slice(0, leftLength) + "..." + text.slice(-rightLength);
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
