const ENDPOINT = "/api/social-storage";
const MEDIA_ENDPOINT = "/api/social-media";
const BACKUP_KEY = "ai-workbench:social:v1";
const MIGRATED_KEY = "ai-workbench:social:shared-store-migrated:v1";
const MAX_ATTACHMENTS = 12;
const MAX_FILE_BYTES = 250 * 1024 * 1024;

export function createSocialWorkspace(root) {
  let entries = loadBackup();
  let revision = 0;
  let storageReady = false;
  let loading = true;
  let saving = false;
  let notice = null;
  let editingId = "";
  let draftBody = "";
  let draftOccurredOn = localTodayKey();
  let draftAttachments = [];

  render();
  syncFromServer();

  document.addEventListener("ai-workbench:module-active", (event) => {
    if (event.detail?.moduleId === "social") requestAnimationFrame(focusEditor);
  });

  async function syncFromServer() {
    try {
      let state = await readState();
      revision = normalizeRevision(state.revision);
      let nextEntries = normalizeEntries(state.entries);
      if (!loadMigrated() && entries.length) {
        nextEntries = mergeEntries(entries, nextEntries);
        state = await persistWithRetry(nextEntries, revision);
        revision = normalizeRevision(state.revision);
        nextEntries = normalizeEntries(state.entries);
        notice = { type: "success", text: "已合并此浏览器中的旧社交笔记。" };
      }
      entries = nextEntries;
      storageReady = true;
      saveMigrated();
      saveBackup(entries);
    } catch (error) {
      storageReady = false;
      notice = { type: "error", text: errorMessage(error, "本机数据服务暂时不可用，当前显示浏览器备份。") };
    } finally {
      loading = false;
      render();
      focusEditor();
    }
  }

  async function persistWithRetry(desired, baseRevision) {
    let candidate = normalizeEntries(desired);
    let candidateRevision = baseRevision;
    for (let attempt = 0; attempt < 3; attempt += 1) {
      try {
        return await writeState(candidate, candidateRevision);
      } catch (error) {
        if (error?.code !== "revision_conflict" || !error.state) throw error;
        candidateRevision = normalizeRevision(error.state.revision);
        candidate = mergeEntries(candidate, error.state.entries);
      }
    }
    const error = new Error("revision_conflict");
    error.code = "revision_conflict";
    throw error;
  }

  async function commit(nextEntries, message) {
    if (saving || !storageReady) return false;
    saving = true;
    notice = { type: "saving", text: "正在保存…" };
    render();
    try {
      const state = await persistWithRetry(nextEntries, revision);
      entries = normalizeEntries(state.entries);
      revision = normalizeRevision(state.revision);
      saveBackup(entries);
      notice = { type: "success", text: message };
      return true;
    } catch (error) {
      notice = { type: "error", text: errorMessage(error, "保存失败，原有笔记没有被覆盖。") };
      return false;
    } finally {
      saving = false;
      render();
    }
  }

  async function saveDraft() {
    draftBody = root.querySelector("#social-note")?.value.trim() || "";
    draftOccurredOn = normalizeDateKey(root.querySelector("#social-occurred-on")?.value, localTodayKey());
    if (!draftBody && !draftAttachments.length) {
      notice = { type: "error", text: "写点内容，或加入一张图片 / 一段视频。" };
      render();
      focusEditor();
      return;
    }
    const now = new Date().toISOString();
    const previous = entries.find((entry) => entry.id === editingId);
    const entry = normalizeEntry({
      id: previous?.id || `social:${crypto.randomUUID()}`,
      body: draftBody,
      occurredOn: draftOccurredOn,
      attachments: draftAttachments,
      createdAt: previous?.createdAt || now,
      updatedAt: now,
      deletedAt: "",
    });
    const nextEntries = mergeEntries([entry], entries);
    const saved = await commit(nextEntries, previous ? "复盘已更新。" : "复盘已保存。");
    if (saved) resetDraft();
  }

  async function importFiles(fileList) {
    const files = [...fileList].filter((file) => file.type.startsWith("image/") || file.type.startsWith("video/"));
    if (!files.length) {
      notice = { type: "error", text: "这里只接收图片和视频。" };
      render();
      return;
    }
    const available = MAX_ATTACHMENTS - draftAttachments.length;
    if (available <= 0) {
      notice = { type: "error", text: `每条复盘最多 ${MAX_ATTACHMENTS} 个附件。` };
      render();
      return;
    }
    const selected = files.slice(0, available);
    const tooLarge = selected.find((file) => file.size > MAX_FILE_BYTES);
    if (tooLarge) {
      notice = { type: "error", text: `${tooLarge.name} 超过 250 MB，未导入。` };
      render();
      return;
    }
    saving = true;
    notice = { type: "saving", text: `正在导入 ${selected.length} 个附件…` };
    render();
    try {
      const uploaded = [];
      for (const file of selected) uploaded.push(await uploadMedia(file));
      draftAttachments = [...draftAttachments, ...uploaded];
      notice = { type: "success", text: `已加入 ${uploaded.length} 个附件，写完直接保存。` };
    } catch (error) {
      notice = { type: "error", text: errorMessage(error, "附件导入失败，请重试。") };
    } finally {
      saving = false;
      render();
      focusEditor();
    }
  }

  function editEntry(id) {
    const entry = activeEntries(entries).find((item) => item.id === id);
    if (!entry) return;
    editingId = entry.id;
    draftBody = entry.body;
    draftOccurredOn = entry.occurredOn;
    draftAttachments = entry.attachments;
    notice = null;
    render();
    focusEditor();
  }

  async function deleteEntry(id) {
    const entry = activeEntries(entries).find((item) => item.id === id);
    if (!entry || !window.confirm("删除这条社交复盘？")) return;
    const now = new Date().toISOString();
    const tombstone = { ...entry, updatedAt: now, deletedAt: now };
    const deleted = await commit(mergeEntries([tombstone], entries), "复盘已删除。");
    if (deleted && editingId === id) resetDraft();
  }

  function resetDraft() {
    editingId = "";
    draftBody = "";
    draftOccurredOn = localTodayKey();
    draftAttachments = [];
    render();
    focusEditor();
  }

  function render() {
    const visibleEntries = activeEntries(entries);
    const disabled = loading || saving || !storageReady;
    root.className = "socialWorkspace modulePanel";
    root.innerHTML = `
      <header class="socialHeader">
        <div>
          <p class="eyebrow">Social Notes</p>
          <h1>社交</h1>
        </div>
        <span class="socialStorageState ${storageReady ? "is-ready" : ""}">
          ${loading ? "正在连接本机数据" : storageReady ? "已保存到本机" : "浏览器备份 · 只读"}
        </span>
      </header>

      <section class="socialComposer ${disabled ? "is-disabled" : ""}" data-social-dropzone>
        <textarea id="social-note" maxlength="100000" placeholder="刚才发生了什么？我有什么感受，下次想怎么做…" aria-label="社交复盘内容" ${disabled ? "disabled" : ""}>${escapeHtml(draftBody)}</textarea>
        ${draftAttachments.length ? `<div class="socialDraftMedia">${draftAttachments.map(renderDraftAttachment).join("")}</div>` : ""}
        <div class="socialComposerFooter">
          <label class="socialDateField">
            <span class="socialVisuallyHidden">发生日期</span>
            <input id="social-occurred-on" type="date" value="${escapeAttr(draftOccurredOn)}" ${disabled ? "disabled" : ""} />
          </label>
          <label class="socialImportButton ${disabled || draftAttachments.length >= MAX_ATTACHMENTS ? "is-disabled" : ""}">
            <span>＋ 图片或视频</span>
            <input data-social-files type="file" accept="image/*,video/*" multiple ${disabled || draftAttachments.length >= MAX_ATTACHMENTS ? "disabled" : ""} />
          </label>
          <span class="socialImportHint">也可以粘贴或拖进来</span>
          <div class="socialComposerActions">
            ${editingId ? `<button class="socialGhostButton" data-social-cancel type="button" ${disabled ? "disabled" : ""}>取消</button>` : ""}
            <button class="primaryButton" data-social-save type="button" ${disabled ? "disabled" : ""}>${saving ? "保存中" : editingId ? "更新" : "保存"}</button>
          </div>
        </div>
        ${notice ? `<p class="socialNotice is-${notice.type}">${escapeHtml(notice.text)}</p>` : ""}
      </section>

      <section class="socialFeed" aria-label="社交复盘列表">
        <div class="socialFeedHeading"><h2>复盘</h2><span>${visibleEntries.length} 条</span></div>
        ${visibleEntries.length ? visibleEntries.map(renderEntry).join("") : `
          <div class="socialEmpty"><strong>还没有复盘</strong><span>从上面直接写第一条就好。</span></div>
        `}
      </section>
    `;
    bindEvents();
  }

  function bindEvents() {
    const textarea = root.querySelector("#social-note");
    textarea?.addEventListener("input", () => { draftBody = textarea.value; });
    root.querySelector("#social-occurred-on")?.addEventListener("change", (event) => {
      draftOccurredOn = normalizeDateKey(event.target.value, localTodayKey());
    });
    textarea?.addEventListener("keydown", (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
        event.preventDefault();
        saveDraft();
      }
    });
    textarea?.addEventListener("paste", (event) => {
      const files = [...(event.clipboardData?.files || [])];
      if (files.length) importFiles(files);
    });
    root.querySelector("[data-social-files]")?.addEventListener("change", (event) => importFiles(event.target.files));
    root.querySelector("[data-social-save]")?.addEventListener("click", saveDraft);
    root.querySelector("[data-social-cancel]")?.addEventListener("click", resetDraft);
    root.querySelectorAll("[data-social-remove]").forEach((button) => button.addEventListener("click", () => {
      draftAttachments = draftAttachments.filter((item) => item.id !== button.dataset.socialRemove);
      render();
      focusEditor();
    }));
    root.querySelectorAll("[data-social-edit]").forEach((button) => button.addEventListener("click", () => editEntry(button.dataset.socialEdit)));
    root.querySelectorAll("[data-social-delete]").forEach((button) => button.addEventListener("click", () => deleteEntry(button.dataset.socialDelete)));
    const dropzone = root.querySelector("[data-social-dropzone]");
    ["dragenter", "dragover"].forEach((name) => dropzone?.addEventListener(name, (event) => {
      event.preventDefault();
      dropzone.classList.add("is-dragging");
    }));
    ["dragleave", "drop"].forEach((name) => dropzone?.addEventListener(name, (event) => {
      event.preventDefault();
      dropzone.classList.remove("is-dragging");
      if (name === "drop" && event.dataTransfer?.files?.length) importFiles(event.dataTransfer.files);
    }));
  }

  function focusEditor() {
    if (!root.hidden && storageReady && !saving) root.querySelector("#social-note")?.focus({ preventScroll: true });
  }
}

function renderEntry(entry) {
  const edited = entry.updatedAt !== entry.createdAt;
  return `
    <article class="socialEntry">
      <div class="socialEntryMeta">
        <time class="socialOccurredOn" datetime="${escapeAttr(entry.occurredOn)}">${formatFriendlyDate(entry.occurredOn)}</time>
        <span>创建 ${formatClockTime(entry.createdAt)}${edited ? ` · 编辑 ${formatClockTime(entry.updatedAt)}` : ""}</span>
      </div>
      ${entry.body ? `<p class="socialEntryBody">${escapeHtml(entry.body)}</p>` : ""}
      ${entry.attachments.length ? `<div class="socialEntryMedia">${entry.attachments.map(renderAttachment).join("")}</div>` : ""}
      <div class="socialEntryActions">
        <button type="button" data-social-edit="${escapeAttr(entry.id)}">编辑</button>
        <button type="button" data-social-delete="${escapeAttr(entry.id)}">删除</button>
      </div>
    </article>
  `;
}

function renderAttachment(item) {
  if (item.type === "video") return `<video controls preload="metadata" src="${escapeAttr(item.url)}"></video>`;
  return `<a href="${escapeAttr(item.url)}" target="_blank" rel="noreferrer"><img loading="lazy" src="${escapeAttr(item.url)}" alt="${escapeAttr(item.name || "复盘图片")}" /></a>`;
}

function renderDraftAttachment(item) {
  return `<div class="socialDraftAttachment">${renderAttachment(item)}<button type="button" data-social-remove="${escapeAttr(item.id)}" aria-label="移除 ${escapeAttr(item.name)}">×</button></div>`;
}

async function readState() {
  const response = await fetch(ENDPOINT, { cache: "no-store" });
  const payload = await response.json();
  if (!response.ok || !payload.ok || !Array.isArray(payload.entries)) throw storageError(payload?.error || "social_storage_read_failed");
  return payload;
}

async function writeState(entries, baseRevision) {
  const response = await fetch(ENDPOINT, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ entries, baseRevision }),
  });
  const payload = await response.json();
  if (response.status === 409 && payload.state) {
    const error = storageError("revision_conflict");
    error.state = payload.state;
    throw error;
  }
  if (!response.ok || !payload.ok || !Array.isArray(payload.entries)) throw storageError(payload?.error || "social_storage_write_failed");
  return payload;
}

async function uploadMedia(file) {
  const response = await fetch(MEDIA_ENDPOINT, { method: "POST", headers: { "Content-Type": file.type }, body: file });
  const payload = await response.json();
  if (!response.ok || !payload.ok || !payload.url) throw storageError(payload?.error || "social_media_write_failed");
  return {
    id: `media:${crypto.randomUUID()}`,
    name: file.name || (file.type.startsWith("video/") ? "视频" : "图片"),
    type: file.type.startsWith("video/") ? "video" : "image",
    mimeType: payload.mimeType || file.type,
    size: payload.size || file.size,
    url: payload.url,
  };
}

function mergeEntries(primary, fallback) {
  const map = new Map();
  [...normalizeEntries(fallback), ...normalizeEntries(primary)].forEach((entry) => {
    const current = map.get(entry.id);
    if (!current || entry.updatedAt >= current.updatedAt) map.set(entry.id, entry);
  });
  return [...map.values()].sort(compareEntriesNewestFirst);
}

function normalizeEntries(value) {
  return Array.isArray(value) ? value.map(normalizeEntry).filter(Boolean) : [];
}

function normalizeEntry(value) {
  if (!value || typeof value !== "object") return null;
  const now = new Date().toISOString();
  const id = safeId(value.id, "social");
  const createdAt = normalizeIso(value.createdAt, now);
  const updatedAt = normalizeIso(value.updatedAt, createdAt);
  return {
    id,
    body: String(value.body ?? value.text ?? value.note ?? "").slice(0, 100000),
    occurredOn: normalizeDateKey(value.occurredOn, localDateKeyFromIso(createdAt)),
    attachments: Array.isArray(value.attachments) ? value.attachments.map(normalizeAttachment).filter(Boolean).slice(0, 50) : [],
    createdAt,
    updatedAt,
    deletedAt: value.deletedAt ? normalizeIso(value.deletedAt, updatedAt) : "",
  };
}

function normalizeAttachment(value) {
  if (!value || !["image", "video"].includes(value.type) || !String(value.url || "").startsWith("/api/social-media/")) return null;
  return {
    id: safeId(value.id, "media"),
    name: String(value.name || "附件").slice(0, 512),
    type: value.type,
    mimeType: String(value.mimeType || "").slice(0, 128),
    size: Math.max(0, Number.parseInt(value.size, 10) || 0),
    url: String(value.url).slice(0, 2048),
  };
}

function activeEntries(entries) {
  return normalizeEntries(entries).filter((entry) => !entry.deletedAt).sort(compareEntriesNewestFirst);
}

function loadBackup() {
  try {
    const parsed = JSON.parse(localStorage.getItem(BACKUP_KEY) || "[]");
    return normalizeEntries(Array.isArray(parsed) ? parsed : parsed?.entries);
  } catch { return []; }
}

function saveBackup(entries) {
  try { localStorage.setItem(BACKUP_KEY, JSON.stringify({ version: 1, entries: normalizeEntries(entries) })); } catch {}
}

function loadMigrated() {
  try { return localStorage.getItem(MIGRATED_KEY) === "1"; } catch { return false; }
}

function saveMigrated() {
  try { localStorage.setItem(MIGRATED_KEY, "1"); } catch {}
}

function normalizeRevision(value) {
  const revision = Number(value);
  return Number.isInteger(revision) && revision >= 0 ? revision : 0;
}

function normalizeIso(value, fallback) {
  const timestamp = typeof value === "string" ? Date.parse(value) : Number.NaN;
  return Number.isNaN(timestamp) ? fallback : new Date(timestamp).toISOString();
}

function safeId(value, prefix) {
  const source = String(value || `${prefix}:${crypto.randomUUID()}`);
  const clean = source.replace(/[^A-Za-z0-9._:-]/g, "-").slice(0, 128);
  return clean || `${prefix}:${crypto.randomUUID()}`;
}

function storageError(code) {
  const error = new Error(code);
  error.code = code;
  return error;
}

function errorMessage(error, fallback) {
  const messages = {
    revision_conflict: "其他页面正在更新社交笔记，请刷新后重试。",
    social_storage_read_failed: "社交数据文件暂时无法读取，为保护已有数据已停止编辑。",
    social_storage_write_failed: "社交数据文件写入失败。",
    social_media_write_failed: "附件写入本机失败。",
    unsupported_social_media_type: "暂不支持这种图片或视频格式。",
    social_media_too_large: "单个附件不能超过 250 MB。",
  };
  return messages[error?.code || error?.message] || fallback;
}

function formatFriendlyDate(value) {
  if (!isDateKey(value)) return value;
  const [year, month, day] = value.split("-").map(Number);
  return `${year}年${month}月${day}日`;
}

function formatClockTime(value) {
  try {
    const date = new Date(value);
    return new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false }).format(date);
  } catch { return ""; }
}

function compareEntriesNewestFirst(left, right) {
  return right.occurredOn.localeCompare(left.occurredOn) || right.createdAt.localeCompare(left.createdAt);
}

function localTodayKey() {
  return localDateKeyFromDate(new Date());
}

function localDateKeyFromIso(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? localTodayKey() : localDateKeyFromDate(date);
}

function localDateKeyFromDate(date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

function normalizeDateKey(value, fallback) {
  return isDateKey(value) ? value : fallback;
}

function isDateKey(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(String(value || ""))) return false;
  const [year, month, day] = value.split("-").map(Number);
  const date = new Date(year, month - 1, day);
  return date.getFullYear() === year && date.getMonth() === month - 1 && date.getDate() === day;
}

function escapeHtml(value) {
  return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");
}

function escapeAttr(value) {
  return escapeHtml(value).replaceAll("`", "&#096;");
}
