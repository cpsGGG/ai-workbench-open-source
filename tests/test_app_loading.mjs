import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

// Execute the real shell and its dynamic imports without accessing a browser,
// the local API, or the user's localStorage. No DOM/package dependency is needed.
const source = await readFile(new URL("../src/app.js", import.meta.url), "utf8");
const storageKey = "ai-workbench:active-module:v2";
const moduleExports = {
  "token-stats": "createTokenStatsWorkspace",
  papers: "createPaperWorkspace",
  videos: "createVideoWorkspace",
  tasks: "createTaskWorkspace",
  health: "createHealthWorkspace",
  social: "createSocialWorkspace",
  accounting: "createAccountingWorkspace",
  tweets: "createTweetWorkspace",
  chats: "createChatHistoryWorkspace",
};

function deferred() {
  let resolve;
  const promise = new Promise((finish) => { resolve = finish; });
  return { promise, resolve };
}

async function settle() {
  // Dynamic import and VM module evaluation add several microtask turns.
  for (let turn = 0; turn < 3; turn += 1) await new Promise(setImmediate);
}

class Element {
  constructor(dataset = {}) {
    this.dataset = dataset;
    this.listeners = new Map();
    this.attributes = new Map();
    this.classes = new Set();
    this.classList = {
      toggle: (name, enabled) => enabled ? this.classes.add(name) : this.classes.delete(name),
    };
    this.hiddenObservers = [];
    this._hidden = false;
    this._html = "";
  }

  addEventListener(name, callback) {
    const callbacks = this.listeners.get(name) || [];
    this.listeners.set(name, [...callbacks, callback]);
  }

  click() {
    for (const callback of this.listeners.get("click") || []) callback({ target: this });
  }

  setAttribute(name, value) { this.attributes.set(name, value); }
  removeAttribute(name) { this.attributes.delete(name); }

  set hidden(value) {
    const changed = this._hidden !== value;
    this._hidden = value;
    if (changed) for (const callback of this.hiddenObservers) queueMicrotask(callback);
  }
  get hidden() { return this._hidden; }

  set innerHTML(value) {
    this._html = value;
    this.actionButtons = new Map([...value.matchAll(/<button[^>]*data-action="([^"]+)"/g)]
      .map((match) => [match[1], new Element()]));
    this.retry = this.actionButtons.get("retry-module") || null;
  }
  get innerHTML() { return this._html; }

  querySelector(selector) {
    const action = selector.match(/^\[data-action="(.+)"\]$/)?.[1];
    return this.actionButtons?.get(action) || null;
  }
}

function createHarness({ saved = null, store, plans = {}, storageThrows = false } = {}) {
  const storage = store || new Map(saved ? [[storageKey, saved]] : []);
  const buttons = new Map([...Object.keys(moduleExports), "books"].map((id) => [id, new Element({ module: id })]));
  const panels = new Map(Object.keys(moduleExports).map((id) => [id, new Element({ panel: id })]));
  const reload = new Element();
  const app = new Element();
  app.querySelectorAll = (selector) => {
    if (selector === "[data-module]") return [...buttons.values()];
    if (selector === "[data-panel]") return [...panels.values()];
    throw new Error(`Unexpected selector: ${selector}`);
  };
  app.querySelector = (selector) => {
    if (selector === '[data-action="reload-page"]') return reload;
    const match = selector.match(/^\[data-panel="(.+)"\]$/);
    if (match) return panels.get(match[1]);
    throw new Error(`Unexpected selector: ${selector}`);
  };
  const document = new Element();
  const events = [];
  document.querySelector = (selector) => selector === "#app" ? app : null;
  document.dispatchEvent = (event) => {
    events.push(event.detail.moduleId);
    for (const callback of document.listeners.get(event.type) || []) callback(event);
  };
  const imports = [];
  const creations = [];
  const errors = [];
  let reloads = 0;
  const context = vm.createContext({
    document,
    localStorage: {
      getItem(key) {
        if (storageThrows) throw new Error("Storage disabled");
        return storage.get(key) || null;
      },
      setItem(key, value) {
        if (storageThrows) throw new Error("Storage disabled");
        storage.set(key, value);
      },
    },
    window: { location: { reload() { reloads += 1; } } },
    CustomEvent: class {
      constructor(type, { detail }) { this.type = type; this.detail = detail; }
    },
    console: { error(...args) { errors.push(args); } },
  });
  const script = new vm.Script(source, {
    filename: "app.js",
    importModuleDynamically: async (specifier) => {
      const id = specifier.match(/\/modules\/([^/]+)\//)?.[1];
      assert.ok(moduleExports[id], `Unknown import: ${specifier}`);
      imports.push({ id, specifier });
      const attempt = imports.filter((entry) => entry.id === id).length;
      const plan = plans[id] || {};
      if (plan.importGate) await plan.importGate.promise;
      if ((plan.failImportOnce && attempt === 1) || attempt <= (plan.failImports || 0)) {
        throw new Error("Simulated offline import");
      }
      const createWorkspace = (root) => {
        creations.push({ id, hidden: root.hidden, visibility: [...panels.values()].map((panel) => [panel.dataset.panel, panel.hidden]) });
        root.innerHTML = `<div>${id} workspace</div>`;
        return plan.create?.(root, document);
      };
      const imported = new vm.SyntheticModule([moduleExports[id]], function () {
        this.setExport(moduleExports[id], createWorkspace);
      }, { context });
      await imported.link(() => {});
      await imported.evaluate();
      return imported;
    },
  });
  script.runInContext(context);
  return {
    app, buttons, panels, imports, creations, errors, events, storage,
    click(id) { buttons.get(id).click(); },
    get reloads() { return reloads; },
    reload,
    visible() { return [...panels.values()].filter((panel) => !panel.hidden).map((panel) => panel.dataset.panel); },
  };
}

test("first visit imports only statistics and sets panel visibility before creation", async () => {
  const app = createHarness();
  assert.deepEqual(app.visible(), ["token-stats"]);
  assert.match(app.panels.get("token-stats").innerHTML, /正在加载/);
  await settle();
  assert.deepEqual(app.imports.map((entry) => entry.id), ["token-stats"]);
  assert.equal(app.creations[0].hidden, false);
  assert.equal(app.creations[0].visibility.filter(([, hidden]) => !hidden).length, 1);
  assert.equal(app.panels.get("token-stats").attributes.has("aria-busy"), false);
  assert.equal(app.storage.size, 0, "startup does not alter stored selection or module data");
});

test("saved selection and subsequent refresh restore the same module without importing statistics", async () => {
  const app = createHarness({ saved: "videos" });
  await settle();
  assert.deepEqual(app.imports.map((entry) => entry.id), ["videos"]);
  assert.deepEqual(app.visible(), ["videos"]);
  app.click("tasks");
  await settle();
  assert.equal(app.storage.get(storageKey), "tasks");
  const refreshed = createHarness({ store: app.storage });
  await settle();
  assert.deepEqual(refreshed.visible(), ["tasks"]);
  assert.deepEqual(refreshed.imports.map((entry) => entry.id), ["tasks"]);
});

test("invalid or disabled saved module and unavailable localStorage safely use statistics", async () => {
  for (const options of [{ saved: "books" }, { saved: "unknown" }, { storageThrows: true }]) {
    const app = createHarness(options);
    await settle();
    assert.deepEqual(app.imports.map((entry) => entry.id), ["token-stats"]);
    app.click("books");
    assert.deepEqual(app.visible(), ["token-stats"]);
    app.click("tasks");
    await settle();
    assert.deepEqual(app.visible(), ["tasks"]);
  }
});

test("each requested workspace imports and initializes once while preserving draft state", async () => {
  const app = createHarness();
  await settle();
  for (const id of Object.keys(moduleExports)) {
    app.click(id);
    await settle();
    assert.deepEqual(app.visible(), [id]);
    app.panels.get(id).draft = `Unsent ${id} draft`;
  }
  for (const id of Object.keys(moduleExports)) {
    const html = app.panels.get(id).innerHTML;
    app.click(id);
    await settle();
    assert.equal(app.panels.get(id).draft, `Unsent ${id} draft`);
    assert.equal(app.panels.get(id).innerHTML, html);
    assert.equal(app.imports.filter((entry) => entry.id === id).length, 1);
    assert.equal(app.creations.filter((entry) => entry.id === id).length, 1);
  }
  assert.ok(app.imports.find((entry) => entry.id === "videos").specifier.endsWith("?v=20260810-video-shared-v1"));
  assert.ok(app.imports.find((entry) => entry.id === "token-stats").specifier.endsWith("?v=20260930-cache-tokens-v1"));
});

test("rapid A → B → A shares imports and late B completion cannot reactivate B", async () => {
  const papers = deferred();
  const tasks = deferred();
  const app = createHarness({ plans: { papers: { importGate: papers }, tasks: { importGate: tasks } } });
  await settle();
  app.click("papers");
  app.click("tasks");
  app.click("papers");
  await settle();
  assert.equal(app.imports.filter((entry) => entry.id === "papers").length, 1);
  const beforeLateCompletion = app.events.length;
  tasks.resolve();
  await settle();
  assert.deepEqual(app.visible(), ["papers"]);
  assert.equal(app.events.length, beforeLateCompletion);
  assert.equal(app.creations.find((entry) => entry.id === "tasks").hidden, true);
  papers.resolve();
  await settle();
  assert.deepEqual(app.visible(), ["papers"]);
  assert.equal(app.events.at(-1), "papers");
  assert.equal(app.creations.filter((entry) => entry.id === "papers").length, 1);
  assert.equal(app.storage.get(storageKey), "papers");
});

test("social activation reaches newly created listeners and hidden late social completion does not focus", async () => {
  let focusCount = 0;
  const social = deferred();
  const app = createHarness({
    plans: {
      social: {
        importGate: social,
        create(root, document) {
          document.addEventListener("ai-workbench:module-active", (event) => {
            if (event.detail.moduleId === "social" && !root.hidden) focusCount += 1;
          });
        },
      },
    },
  });
  await settle();
  app.click("social");
  app.click("tasks");
  await settle();
  social.resolve();
  await settle();
  assert.equal(focusCount, 0);
  assert.deepEqual(app.visible(), ["tasks"]);
  app.click("social");
  await settle();
  assert.equal(focusCount, 1);
  assert.equal(app.creations.filter((entry) => entry.id === "social").length, 1);

  let initialFocusCount = 0;
  const initiallySocial = createHarness({
    saved: "social",
    plans: { social: { create(root, document) {
      document.addEventListener("ai-workbench:module-active", (event) => {
        if (event.detail.moduleId === "social" && !root.hidden) initialFocusCount += 1;
      });
    } } },
  });
  await settle();
  assert.deepEqual(initiallySocial.visible(), ["social"]);
  assert.equal(initialFocusCount, 1, "activation is replayed after the first social initialization");
});

test("statistics initialized after being hidden waits for activation before its first request", async () => {
  const stats = deferred();
  let requestCount = 0;
  let started = false;
  const app = createHarness({ plans: { "token-stats": {
    importGate: stats,
    create(root) {
      const maybeStart = () => {
        if (started || root.hidden) return;
        started = true;
        requestCount += 1;
      };
      root.hiddenObservers.push(maybeStart);
      queueMicrotask(maybeStart);
    },
  } } });
  app.click("tasks");
  await settle();
  stats.resolve();
  await settle();
  assert.equal(app.creations.find((entry) => entry.id === "token-stats").hidden, true);
  assert.equal(requestCount, 0);
  app.click("token-stats");
  await settle();
  assert.equal(requestCount, 1);
  app.click("tasks");
  app.click("token-stats");
  await settle();
  assert.equal(requestCount, 1);
});

test("async creation remains shared and replays activation only after it completes", async () => {
  const initialization = deferred();
  const app = createHarness({ plans: { papers: { create() { return initialization.promise; } } } });
  await settle();
  app.click("papers");
  await settle();
  assert.equal(app.panels.get("papers").attributes.get("aria-busy"), "true");
  app.click("tasks");
  app.click("papers");
  await settle();
  const beforeReady = app.events.length;
  initialization.resolve();
  await settle();
  assert.equal(app.events.length, beforeReady + 1);
  assert.equal(app.events.at(-1), "papers");
  assert.equal(app.creations.filter((entry) => entry.id === "papers").length, 1);
  assert.equal(app.panels.get("papers").attributes.has("aria-busy"), false);
});

test("failed import provides readable retry feedback, retries once and leaves other panels intact", async () => {
  const app = createHarness({ plans: { papers: { failImportOnce: true } } });
  await settle();
  const statsHtml = app.panels.get("token-stats").innerHTML;
  app.click("papers");
  await settle();
  const panel = app.panels.get("papers");
  assert.match(panel.innerHTML, /加载失败/);
  assert.ok(panel.retry);
  assert.equal(panel.attributes.has("aria-busy"), false);
  assert.equal(app.errors.length, 1);
  assert.equal(app.creations.some((entry) => entry.id === "papers"), false);
  app.click("token-stats");
  app.click("papers");
  await settle();
  assert.equal(app.imports.filter((entry) => entry.id === "papers").length, 1);
  panel.retry.click();
  await settle();
  assert.equal(app.imports.filter((entry) => entry.id === "papers").length, 2);
  assert.deepEqual(app.imports.filter((entry) => entry.id === "papers").map((entry) => entry.specifier), [
    "./modules/papers/papers.js",
    "./modules/papers/papers.js?retry=1",
  ]);
  assert.equal(app.creations.filter((entry) => entry.id === "papers").length, 1);
  assert.equal(panel.retry, null);
  assert.equal(app.panels.get("token-stats").innerHTML, statsHtml);
  assert.deepEqual(app.visible(), ["papers"]);
  app.reload.click();
  assert.equal(app.reloads, 1);
});

test("each failed import retry uses a fresh URL and keeps the entry's existing version", async () => {
  const app = createHarness({ plans: { health: { failImports: 2 } } });
  await settle();
  app.click("health");
  await settle();
  const panel = app.panels.get("health");
  panel.retry.click();
  await settle();
  assert.match(panel.innerHTML, /加载失败/);
  panel.retry.click();
  await settle();
  assert.deepEqual(app.imports.filter((entry) => entry.id === "health").map((entry) => entry.specifier), [
    "./modules/health/health.js?v=20260811-health-v1",
    "./modules/health/health.js?v=20260811-health-v1&retry=1",
    "./modules/health/health.js?v=20260811-health-v1&retry=2",
  ]);
  assert.equal(app.creations.filter((entry) => entry.id === "health").length, 1);
  assert.equal(panel.retry, null);
  assert.deepEqual(app.visible(), ["health"]);
});

test("factory failure offers full refresh and cannot create duplicate listeners or instances", async () => {
  for (const asynchronous of [false, true]) {
    let listenerCount = 0;
    const app = createHarness({ plans: { papers: { create(root, document) {
      document.addEventListener("ai-workbench:module-active", () => { listenerCount += 1; });
      const error = new Error("Simulated factory failure after attaching listeners");
      if (asynchronous) return Promise.reject(error);
      throw error;
    } } } });
    await settle();
    app.click("papers");
    await settle();
    const panel = app.panels.get("papers");
    assert.match(panel.innerHTML, /初始化失败/);
    assert.equal(panel.retry, null);
    assert.equal(panel.attributes.has("aria-busy"), false);
    assert.equal(app.creations.filter((entry) => entry.id === "papers").length, 1);
    app.click("token-stats");
    app.click("papers");
    await settle();
    assert.equal(listenerCount, 2, "only the first factory's listener runs on later module switches");
    panel.querySelector('[data-action="reload-page"]').click();
    await settle();
    assert.equal(app.reloads, 1);
    assert.equal(app.imports.filter((entry) => entry.id === "papers").length, 1);
    assert.equal(app.creations.filter((entry) => entry.id === "papers").length, 1);
  }
});
