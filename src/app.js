// Import and create a workspace only when it is first opened. Keeping its panel
// and load promise lets later visits preserve drafts and reuse the same instance.
const workspaceLoaders = {
  papers: workspaceLoader("./modules/papers/papers.js", "createPaperWorkspace"),
  accounting: workspaceLoader("./modules/accounting/accounting.js", "createAccountingWorkspace"),
  tasks: workspaceLoader("./modules/tasks/tasks.js", "createTaskWorkspace"),
  videos: workspaceLoader("./modules/videos/videos.js?v=20260810-video-shared-v1", "createVideoWorkspace"),
  tweets: workspaceLoader("./modules/tweets/tweets.js?v=20260810-x-bookmarks-v4", "createTweetWorkspace"),
  chats: workspaceLoader("./modules/chats/chats.js?v=20260715-chat-history-v1", "createChatHistoryWorkspace"),
  "token-stats": workspaceLoader("./modules/token-stats/token-stats.js?v=20260930-cache-tokens-v1", "createTokenStatsWorkspace"),
  health: workspaceLoader("./modules/health/health.js?v=20260811-health-v1", "createHealthWorkspace"),
  social: workspaceLoader("./modules/social/social.js?v=20260813-social-v2", "createSocialWorkspace"),
};
const workspaceLoads = new Map();

function workspaceLoader(path, factoryName) {
  return (attempt) => {
    // Browsers retain failed imports in the module map. Retry the entry with a
    // fresh URL while retaining its existing version and normal first-load URL.
    const url = attempt ? `${path}${path.includes("?") ? "&" : "?"}retry=${attempt}` : path;
    return import(url).then((module) => module[factoryName]);
  };
}

// v2 makes the redesigned statistics page the one-time default. After that,
// the selected module is still restored on every refresh.
const ACTIVE_MODULE_STORAGE_KEY = "ai-workbench:active-module:v2";

const createNavIcon = (paths) => `
  <svg viewBox="0 0 24 24" fill="none" aria-hidden="true" focusable="false">
    ${paths}
  </svg>
`;

const navIcons = {
  stats: createNavIcon(`
    <path d="M4 19V9m6 10V5m6 14v-7m4 7H2" />
  `),
  papers: createNavIcon(`
    <path d="M6 3h9l4 4v14H6z" />
    <path d="M15 3v5h4M9 12h7M9 16h7" />
  `),
  videos: createNavIcon(`
    <rect x="3" y="5" width="18" height="14" rx="3" />
    <path d="m10 9 5 3-5 3z" />
  `),
  tasks: createNavIcon(`
    <rect x="3" y="3" width="18" height="18" rx="4" />
    <path d="m8 12 2.5 2.5L16.5 8.5" />
  `),
  health: createNavIcon(`
    <path d="M20.8 5.7a5.2 5.2 0 0 0-7.4 0L12 7.1l-1.4-1.4a5.2 5.2 0 1 0-7.4 7.4L12 21l8.8-7.9a5.2 5.2 0 0 0 0-7.4Z" />
    <path d="M6.5 12h3l1.4-2.6 2.2 5.2 1.4-2.6h3" />
  `),
  social: createNavIcon(`
    <circle cx="9" cy="8" r="3" />
    <circle cx="17" cy="9" r="2.4" />
    <path d="M3.5 20c.3-4 2.2-6 5.5-6s5.2 2 5.5 6M14.5 15c3.7-.8 5.7.8 6 4" />
  `),
  accounting: createNavIcon(`
    <path d="M4 7h14a3 3 0 0 1 3 3v8a3 3 0 0 1-3 3H6a3 3 0 0 1-3-3V6a3 3 0 0 1 3-3h11" />
    <path d="M15 12h6v4h-6a2 2 0 1 1 0-4Z" />
  `),
  tweets: createNavIcon(`
    <path d="m6 4 12 16M18 4 6 20" />
  `),
  chats: createNavIcon(`
    <path d="M20 15a4 4 0 0 1-4 4H9l-5 3v-7a4 4 0 0 1-1-2.6V8a4 4 0 0 1 4-4h9a4 4 0 0 1 4 4z" />
    <path d="M8 11h.01M12 11h.01M16 11h.01" />
  `),
  books: createNavIcon(`
    <path d="M3 5.5A4.5 4.5 0 0 1 7.5 4H12v16H7.5A4.5 4.5 0 0 0 3 21.5z" />
    <path d="M21 5.5A4.5 4.5 0 0 0 16.5 4H12v16h4.5a4.5 4.5 0 0 1 4.5 1.5z" />
  `),
  refresh: createNavIcon(`
    <path d="M20 7v5h-5M4 17v-5h5" />
    <path d="M6.1 8.1A8 8 0 0 1 20 12M17.9 15.9A8 8 0 0 1 4 12" />
  `),
  settings: createNavIcon(`
    <circle cx="12" cy="12" r="3" />
    <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1A1.7 1.7 0 0 0 9 4.6a1.7 1.7 0 0 0 1-1.6v-.2h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1Z" />
  `),
};

const navItems = [
  { id: "token-stats", label: "统计", icon: navIcons.stats, enabled: true },
  { id: "papers", label: "论文", icon: navIcons.papers, enabled: true },
  { id: "videos", label: "视频", icon: navIcons.videos, enabled: true },
  { id: "tasks", label: "待办", icon: navIcons.tasks, enabled: true },
  { id: "health", label: "健康", icon: navIcons.health, enabled: true },
  { id: "social", label: "社交", icon: navIcons.social, enabled: true },
  { id: "accounting", label: "记账", icon: navIcons.accounting, enabled: true },
  { id: "tweets", label: "推特", icon: navIcons.tweets, enabled: true },
  { id: "chats", label: "聊天", icon: navIcons.chats, enabled: true },
  { id: "books", label: "读书", icon: navIcons.books, enabled: false },
];

let activeModule = loadActiveModule();

const app = document.querySelector("#app");

renderShell();

syncActiveModule();

function renderShell() {
  app.innerHTML = `
    <div class="shell">
      <aside class="sidebar">
        <div class="brand">
          <div class="brandMark">AI</div>
          <div>
            <strong>AI 工作台</strong>
            <span>Personal Desk</span>
          </div>
        </div>

        <nav class="navList" aria-label="工作台模块">
          ${navItems
            .map(
              (item) => `
                <button
                  class="navItem ${activeModule === item.id ? "active" : ""}"
                  data-module="${item.id}"
                  type="button"
                  ${item.enabled ? "" : "disabled"}
                  title="${item.enabled ? item.label : "待接入"}"
                >
                  <span class="navIcon" aria-hidden="true">${item.icon}</span>
                  <span>${item.label}</span>
                </button>
              `,
            )
            .join("")}
        </nav>

        <div class="sidebarActions">
          <button
            class="sidebarActionButton reloadPageButton"
            type="button"
            data-action="reload-page"
            title="刷新网页"
            aria-label="刷新网页"
          >
            <span class="navIcon" aria-hidden="true">${navIcons.refresh}</span>
            <span>刷新网页</span>
          </button>

          <button class="sidebarActionButton settingsButton" type="button" disabled title="待接入">
            <span class="navIcon" aria-hidden="true">${navIcons.settings}</span>
            <span>设置</span>
          </button>
        </div>
      </aside>

      <main class="workspace">
        <section id="paper-workspace" class="paperWorkspace modulePanel" data-panel="papers"></section>
        <section id="video-workspace" class="paperWorkspace modulePanel" data-panel="videos"></section>
        <section id="task-workspace" class="paperWorkspace modulePanel" data-panel="tasks"></section>
        <section id="health-workspace" class="paperWorkspace modulePanel" data-panel="health"></section>
        <section id="social-workspace" class="paperWorkspace modulePanel" data-panel="social"></section>
        <section id="accounting-workspace" class="paperWorkspace modulePanel" data-panel="accounting"></section>
        <section id="token-stats-workspace" class="paperWorkspace modulePanel" data-panel="token-stats"></section>
        <section id="tweet-workspace" class="paperWorkspace modulePanel" data-panel="tweets"></section>
        <section id="chat-history-workspace" class="paperWorkspace modulePanel" data-panel="chats"></section>
      </main>
    </div>
  `;

  app.querySelector('[data-action="reload-page"]')?.addEventListener("click", () => {
    window.location.reload();
  });

  app.querySelectorAll("[data-module]").forEach((button) => {
    button.addEventListener("click", () => {
      const item = navItems.find((navItem) => navItem.id === button.dataset.module);
      if (!item?.enabled) return;
      activeModule = item.id;
      saveActiveModule(activeModule);
      syncActiveModule();
    });
  });
}

function loadActiveModule() {
  try {
    const savedModule = localStorage.getItem(ACTIVE_MODULE_STORAGE_KEY);
    const savedItem = navItems.find((item) => item.id === savedModule);
    if (savedItem?.enabled) return savedItem.id;
  } catch {
  }

  return "token-stats";
}

function saveActiveModule(moduleId) {
  try {
    localStorage.setItem(ACTIVE_MODULE_STORAGE_KEY, moduleId);
  } catch {
  }
}

function syncActiveModule() {
  app.querySelectorAll("[data-module]").forEach((button) => {
    button.classList.toggle("active", button.dataset.module === activeModule);
  });

  app.querySelectorAll("[data-panel]").forEach((panel) => {
    panel.hidden = panel.dataset.panel !== activeModule;
  });

  void loadWorkspace(activeModule);
  notifyActiveModule();
}

function notifyActiveModule() {
  document.dispatchEvent(new CustomEvent("ai-workbench:module-active", { detail: { moduleId: activeModule } }));
}

function loadWorkspace(moduleId, attempt = 0) {
  const existing = workspaceLoads.get(moduleId);
  if (existing) return existing;

  const panel = app.querySelector(`[data-panel="${moduleId}"]`);
  const loader = workspaceLoaders[moduleId];
  if (!panel || !loader) return Promise.resolve();

  const label = navItems.find((item) => item.id === moduleId).label;
  panel.setAttribute("aria-busy", "true");
  panel.innerHTML = `<div class="emptyState" role="status">正在加载${label}…</div>`;
  let creationStarted = false;

  const promise = Promise.resolve()
    .then(() => loader(attempt))
    .then((createWorkspace) => {
      panel.innerHTML = "";
      creationStarted = true;
      return createWorkspace(panel);
    })
    .then(() => {
      panel.removeAttribute("aria-busy");
      // New listeners (including the social editor's focus listener) must hear
      // activation, but a late import must never reactivate a previous panel.
      if (activeModule === moduleId) notifyActiveModule();
    })
    .catch((error) => {
      panel.removeAttribute("aria-busy");
      const recoveryAction = creationStarted ? "reload-page" : "retry-module";
      const message = creationStarted
        ? `${label}初始化失败，请刷新页面后重试。`
        : `${label}加载失败，请重试。若持续失败，请刷新页面或检查本机服务。`;
      panel.innerHTML = `
        <div class="emptyState" role="alert">
          <p>${message}</p>
          <button class="secondaryButton" type="button" data-action="${recoveryAction}">${creationStarted ? "刷新页面" : "重新加载"}</button>
        </div>
      `;
      panel.querySelector(`[data-action="${recoveryAction}"]`).addEventListener("click", () => {
        // A factory can have attached listeners before throwing. Only a full
        // refresh safely recovers those partially initialized instances.
        if (creationStarted) {
          window.location.reload();
          return;
        }
        workspaceLoads.delete(moduleId);
        void loadWorkspace(moduleId, attempt + 1);
      });
      console.error(`Failed to load workspace: ${moduleId}`, error);
    });

  workspaceLoads.set(moduleId, promise);
  return promise;
}
