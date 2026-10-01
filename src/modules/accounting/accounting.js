const ENDPOINT = "/api/accounting-storage";
const AUTH_ENDPOINT = "/api/accounting-auth";
const AUTH_CANDIDATES_ENDPOINT = "/api/accounting-auth-candidates";
const CREDENTIAL_ENDPOINT = "/api/accounting-credential";
const BACKUP_KEY = "ai-workbench:accounting:purchases:v2";
const CATEGORIES = ["正规代充", "日抛 Plus", "日抛 Team", "Claude", "其他"];
const MAX_AUTH_BYTES = 2 * 1024 * 1024;

export function createAccountingWorkspace(root) {
  let entries = loadBackup();
  let revision = 0;
  let loading = true;
  let saving = false;
  let storageReady = false;
  let notice = null;
  let editingId = "";
  let showBatch = false;
  let batchText = "";
  let batchPreview = null;
  let range = "month";
  let filterCategory = "全部";
  let filterFrom = "";
  let filterTo = "";
  let cpaCandidates = null;
  let cpaCandidatesLoading = false;
  let selectedCpaCandidateId = "";

  render();
  syncFromServer();

  async function syncFromServer() {
    try {
      let state = await readState();
      if (!state.initialized && entries.length) {
        state = await persistWithRetry(entries, normalizeRevision(state.revision));
        notice = { type: "success", text: "已从此浏览器的脱敏备份恢复账单。" };
      }
      entries = normalizeEntries(state.entries);
      revision = normalizeRevision(state.revision);
      storageReady = true;
      saveBackup(entries);
    } catch (error) {
      storageReady = false;
      notice = { type: "error", text: errorMessage(error, "本机账本暂时无法读取，当前只显示浏览器备份且禁止编辑。") };
    } finally {
      loading = false;
      render();
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
        candidate = mergeEntries(candidate, error.state.entries);
        candidateRevision = normalizeRevision(error.state.revision);
      }
    }
    throw storageError("revision_conflict");
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
      notice = { type: "error", text: errorMessage(error, "保存失败，原账本没有被覆盖。") };
      return false;
    } finally {
      saving = false;
      render();
    }
  }

  async function saveForm(event) {
    event.preventDefault();
    if (!storageReady || saving) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    const previous = entries.find((item) => item.id === editingId);
    const quantity = Math.max(1, Number.parseInt(data.get("quantity"), 10) || 1);
    const unitPriceCents = parseMoneyToCents(data.get("unitPrice"));
    const enteredTotal = String(data.get("totalPrice") || "").trim();
    const totalPriceCents = enteredTotal ? parseMoneyToCents(enteredTotal) : unitPriceCents * quantity;
    const purchasedOn = normalizeDateKey(data.get("purchasedOn"), localTodayKey());
    const category = normalizeCategory(data.get("category"));
    if (!Number.isInteger(unitPriceCents) || !Number.isInteger(totalPriceCents) || totalPriceCents < 0) {
      notice = { type: "error", text: "请输入正确的单价或总价，最多保留两位小数。" };
      render();
      return;
    }
    const now = new Date().toISOString();
    const id = previous?.id || `purchase:${crypto.randomUUID()}`;
    const credential = String(data.get("credential") || "");
    const authContent = String(data.get("authContent") || "").trim();
    const authFile = data.get("authFile");
    const cpaCandidateId = String(data.get("cpaCandidateId") || "");
    let authFiles = previous?.authFiles || [];
    const credentialToSave = credential;
    const authContentToSave = authContent;
    const authFileToSave = authFile instanceof File && authFile.size ? authFile : null;
    if ([Boolean(cpaCandidateId), Boolean(authContentToSave), Boolean(authFileToSave)].filter(Boolean).length > 1) {
      notice = { type: "error", text: "CPA 文件、手动选择和粘贴内容只用一种，避免 auth.json 混在一起。" };
      render();
      return;
    }
    saving = true;
    notice = { type: "saving", text: "正在安全保存凭据和账单…" };
    render();
    try {
      if (credentialToSave) await writeCredential(id, credentialToSave);
      if (authContentToSave) {
        validateJsonText(authContentToSave);
        const uploaded = await uploadAuth(new Blob([authContentToSave], { type: "application/json" }), "auth.json");
        authFiles = [uploaded];
      }
      if (authFileToSave) {
        if (authFileToSave.size > MAX_AUTH_BYTES) throw storageError("accounting_auth_too_large");
        const uploaded = await uploadAuth(authFileToSave, "auth.json");
        authFiles = [uploaded];
      }
      if (cpaCandidateId) {
        const candidate = cpaCandidates?.find((item) => item.id === cpaCandidateId);
        if (!candidate) throw storageError("invalid_accounting_auth_candidate");
        const imported = await importCpaAuth(cpaCandidateId, candidate.name);
        // A confirmed CPA choice represents the auth for this purchase. When
        // editing, replace the old attachment instead of silently mixing both.
        authFiles = [imported];
      }
      const entry = normalizeEntry({
        id,
        purchasedOn,
        category,
        quantity,
        unitPriceCents,
        totalPriceCents,
        merchant: String(data.get("merchant") || "").trim(),
        note: String(data.get("note") || "").trim(),
        hasCredential: Boolean(credentialToSave || previous?.hasCredential),
        authFiles,
        importFingerprint: previous?.importFingerprint || "",
        createdAt: previous?.createdAt || now,
        updatedAt: now,
        deletedAt: "",
      });
      const state = await persistWithRetry(mergeEntries([entry], entries), revision);
      entries = normalizeEntries(state.entries);
      revision = normalizeRevision(state.revision);
      saveBackup(entries);
      editingId = "";
      selectedCpaCandidateId = "";
      notice = { type: "success", text: previous ? "账单已更新。" : "购买账单已记下。" };
    } catch (error) {
      notice = { type: "error", text: errorMessage(error, "保存失败，原账本没有被覆盖。") };
    } finally {
      saving = false;
      render();
    }
  }

  function editEntry(id) {
    if (!activeEntries(entries).some((entry) => entry.id === id)) return;
    editingId = id;
    selectedCpaCandidateId = "";
    showBatch = false;
    notice = { type: "success", text: "正在编辑。原有卡密不会显示；留空即可保留。" };
    render();
    root.querySelector("#accounting-purchased-on")?.focus();
  }

  async function deleteEntry(id) {
    const entry = activeEntries(entries).find((item) => item.id === id);
    if (!entry || !window.confirm("删除这条购买账单？")) return;
    const now = new Date().toISOString();
    const deleted = await commit(mergeEntries([{ ...entry, updatedAt: now, deletedAt: now }], entries), "账单已删除。");
    if (deleted && editingId === id) editingId = "";
  }

  async function revealCredential(id, copyOnly) {
    try {
      const credential = await readCredential(id);
      if (!credential) {
        notice = { type: "error", text: "这条账单没有可用凭据。" };
      } else if (copyOnly) {
        await navigator.clipboard.writeText(credential);
        notice = { type: "success", text: "账号 / 卡密已复制。" };
      } else {
        window.prompt("账号 / 卡密（关闭后自动隐藏）", credential);
      }
    } catch (error) {
      notice = { type: "error", text: errorMessage(error, "凭据读取失败。") };
    }
    render();
  }

  async function previewBatch() {
    batchText = root.querySelector("#accounting-batch-text")?.value || batchText;
    try {
      const parsed = parseBatch(batchText);
      const fingerprints = new Set(entries.map((entry) => entry.importFingerprint).filter(Boolean));
      const rows = [];
      let duplicates = 0;
      for (const row of parsed) {
        row.importFingerprint = await fingerprintRow(row);
        if (fingerprints.has(row.importFingerprint)) duplicates += 1;
        else {
          fingerprints.add(row.importFingerprint);
          rows.push(row);
        }
      }
      batchPreview = { rows, duplicates, originalCount: parsed.length };
      notice = null;
    } catch (error) {
      batchPreview = null;
      notice = { type: "error", text: errorMessage(error, "批量内容无法解析。") };
    }
    render();
  }

  async function importBatch() {
    if (!batchPreview?.rows.length || saving || !storageReady) return;
    saving = true;
    notice = { type: "saving", text: `正在导入 ${batchPreview.rows.length} 条…` };
    render();
    try {
      const now = new Date().toISOString();
      const imported = [];
      for (const row of batchPreview.rows) {
        const id = `purchase:${crypto.randomUUID()}`;
        let authFiles = [];
        if (row.credential) await writeCredential(id, row.credential);
        if (row.authJson) {
          validateJsonText(row.authJson);
          authFiles = [await uploadAuth(new Blob([row.authJson], { type: "application/json" }), "auth.json")];
        }
        imported.push(normalizeEntry({
          ...row,
          id,
          hasCredential: Boolean(row.credential),
          authFiles,
          createdAt: now,
          updatedAt: now,
          deletedAt: "",
        }));
      }
      const state = await persistWithRetry(mergeEntries(imported, entries), revision);
      entries = normalizeEntries(state.entries);
      revision = normalizeRevision(state.revision);
      saveBackup(entries);
      const skipped = batchPreview.duplicates;
      batchText = "";
      batchPreview = null;
      showBatch = false;
      notice = { type: "success", text: `已导入 ${imported.length} 条${skipped ? `，跳过 ${skipped} 条重复账单` : ""}。` };
    } catch (error) {
      notice = { type: "error", text: errorMessage(error, "导入失败，原账本没有被覆盖。") };
    } finally {
      saving = false;
      render();
    }
  }

  function render() {
    const visible = activeEntries(entries);
    const bounds = rangeBounds(range, filterFrom, filterTo);
    const filtered = visible.filter((entry) => (
      (filterCategory === "全部" || entry.category === filterCategory)
      && (!bounds.from || entry.purchasedOn >= bounds.from)
      && (!bounds.to || entry.purchasedOn <= bounds.to)
    ));
    const current = entries.find((entry) => entry.id === editingId);
    const disabled = loading || saving || !storageReady;
    root.className = "accountingWorkspace modulePanel";
    root.innerHTML = `
      <header class="topbar accountingHeader">
        <div><p class="eyebrow">AI Purchase Ledger</p><h1>AI 账号采购账本</h1></div>
        <div class="accountingHeaderActions">
          <span class="accountingStorageState ${storageReady ? "is-ready" : ""}">${loading ? "正在连接本机账本" : storageReady ? "已保存到本机" : "浏览器备份 · 只读"}</span>
          <button class="secondaryButton" data-accounting-batch-toggle type="button" ${disabled ? "disabled" : ""}>${showBatch ? "返回单条录入" : "批量导入"}</button>
        </div>
      </header>

      <div class="accountingSummaryGrid">
        ${renderMetric("今天", formatCny(sumCents(inRange(visible, rangeBounds("today")))), `${countInRange(visible, rangeBounds("today"))} 条购买`)}
        ${renderMetric("本周", formatCny(sumCents(inRange(visible, rangeBounds("week")))), "周一至周日")}
        ${renderMetric("本月", formatCny(sumCents(inRange(visible, rangeBounds("month")))), `${localTodayKey().slice(0, 7)}`)}
        ${renderMetric("当前筛选", formatCny(sumCents(filtered)), `${filtered.length} 条 · ${filterCategory}`)}
      </div>

      ${notice ? `<p class="accountingNotice ${notice.type === "error" ? "danger" : ""}">${escapeHtml(notice.text)}</p>` : ""}

      ${showBatch ? renderBatch(disabled, batchText, batchPreview) : renderForm(current, disabled, cpaCandidates, cpaCandidatesLoading, selectedCpaCandidateId)}

      <section class="accountingLedger">
        <div class="accountingLedgerHeader">
          <div><h2>购买账单</h2><span>${filtered.length} 条</span></div>
          <div class="accountingFilters">
            <select data-accounting-range aria-label="时间范围">
              ${[["today", "今天"], ["week", "本周"], ["month", "本月"], ["all", "全部"], ["custom", "自定义"]].map(([value, label]) => `<option value="${value}" ${range === value ? "selected" : ""}>${label}</option>`).join("")}
            </select>
            <select data-accounting-category aria-label="采购分类"><option>全部</option>${renderCategoryOptions(filterCategory)}</select>
            ${range === "custom" ? `<input data-accounting-from type="date" value="${escapeAttr(filterFrom)}" aria-label="开始日期" /><span>至</span><input data-accounting-to type="date" value="${escapeAttr(filterTo)}" aria-label="结束日期" />` : ""}
          </div>
        </div>
        ${renderCategoryTotals(filtered)}
        ${filtered.length ? `<div class="accountingCards">${filtered.map(renderEntry).join("")}</div>` : `<div class="accountingEmpty"><div><strong>这个范围还没有账单</strong><span>从上面录入今天购买的 AI 账号。</span></div></div>`}
      </section>
    `;
    bindEvents();
  }

  function bindEvents() {
    root.querySelector("#accounting-entry-form")?.addEventListener("submit", saveForm);
    root.querySelector("[data-accounting-cancel]")?.addEventListener("click", () => { editingId = ""; selectedCpaCandidateId = ""; notice = null; render(); });
    root.querySelector("[data-accounting-batch-toggle]")?.addEventListener("click", () => { showBatch = !showBatch; editingId = ""; selectedCpaCandidateId = ""; batchPreview = null; notice = null; render(); });
    root.querySelector("[data-accounting-batch-preview]")?.addEventListener("click", previewBatch);
    root.querySelector("[data-accounting-batch-import]")?.addEventListener("click", importBatch);
    root.querySelector("[data-accounting-batch-file]")?.addEventListener("change", async (event) => {
      const file = event.target.files?.[0];
      if (!file) return;
      batchText = await file.text();
      batchPreview = null;
      render();
    });
    root.querySelectorAll("[data-accounting-edit]").forEach((button) => button.addEventListener("click", () => editEntry(button.dataset.accountingEdit)));
    root.querySelectorAll("[data-accounting-delete]").forEach((button) => button.addEventListener("click", () => deleteEntry(button.dataset.accountingDelete)));
    root.querySelectorAll("[data-accounting-view-secret]").forEach((button) => button.addEventListener("click", () => revealCredential(button.dataset.accountingViewSecret, false)));
    root.querySelectorAll("[data-accounting-copy-secret]").forEach((button) => button.addEventListener("click", () => revealCredential(button.dataset.accountingCopySecret, true)));
    bindCpaCandidateEvents();
    root.querySelector("[data-accounting-range]")?.addEventListener("change", (event) => { range = event.target.value; render(); });
    root.querySelector("[data-accounting-category]")?.addEventListener("change", (event) => { filterCategory = event.target.value; render(); });
    root.querySelector("[data-accounting-from]")?.addEventListener("change", (event) => { filterFrom = event.target.value; render(); });
    root.querySelector("[data-accounting-to]")?.addEventListener("change", (event) => { filterTo = event.target.value; render(); });
    const quantity = root.querySelector("[name=quantity]");
    const unitPrice = root.querySelector("[name=unitPrice]");
    const totalPrice = root.querySelector("[name=totalPrice]");
    const updateEstimate = () => {
      const hint = root.querySelector("[data-accounting-price-hint]");
      if (!hint) return;
      const cents = parseMoneyToCents(unitPrice?.value);
      const count = Math.max(1, Number.parseInt(quantity?.value, 10) || 1);
      hint.textContent = totalPrice?.value ? "将按填写的总价入账" : `自动总价 ${formatCny(Math.max(0, cents || 0) * count)}`;
    };
    quantity?.addEventListener("input", updateEstimate);
    unitPrice?.addEventListener("input", updateEstimate);
    totalPrice?.addEventListener("input", updateEstimate);
  }

  function bindCpaCandidateEvents() {
    root.querySelector("[data-accounting-find-cpa]")?.addEventListener("click", findCpaCandidates);
    root.querySelectorAll("[name=cpaCandidateId]").forEach((radio) => radio.addEventListener("change", (event) => {
      selectedCpaCandidateId = event.target.value;
      const fileInput = root.querySelector("[name=authFile]");
      const contentInput = root.querySelector("[name=authContent]");
      if (fileInput) fileInput.value = "";
      if (contentInput) contentInput.value = "";
      root.querySelectorAll(".accountingCpaCandidate").forEach((row) => row.classList.toggle("is-selected", row.contains(event.target)));
    }));
    root.querySelector("[name=authFile]")?.addEventListener("change", (event) => {
      if (!event.target.files?.length) return;
      const contentInput = root.querySelector("[name=authContent]");
      if (contentInput) contentInput.value = "";
      clearCpaSelection();
    });
    root.querySelector("[name=authContent]")?.addEventListener("input", (event) => {
      if (!event.target.value.trim()) return;
      const fileInput = root.querySelector("[name=authFile]");
      if (fileInput) fileInput.value = "";
      clearCpaSelection();
    });
  }

  function clearCpaSelection() {
    selectedCpaCandidateId = "";
    root.querySelectorAll("[name=cpaCandidateId]").forEach((radio) => { radio.checked = false; });
    root.querySelectorAll(".accountingCpaCandidate").forEach((row) => row.classList.remove("is-selected"));
  }

  async function findCpaCandidates() {
    if (cpaCandidatesLoading) return;
    cpaCandidatesLoading = true;
    updateCpaCandidatePanel();
    try {
      cpaCandidates = await readCpaAuthCandidates();
      if (!cpaCandidates.some((item) => item.id === selectedCpaCandidateId)) selectedCpaCandidateId = "";
      if (notice?.type === "error") notice = null;
    } catch (error) {
      cpaCandidates = [];
      notice = { type: "error", text: errorMessage(error, "无法读取 CPA 的 auth.json 候选。") };
    } finally {
      cpaCandidatesLoading = false;
      updateCpaCandidatePanel();
      const noticeNode = root.querySelector(".accountingNotice");
      if (noticeNode && notice?.type === "error") noticeNode.textContent = notice.text;
    }
  }

  function updateCpaCandidatePanel() {
    const panel = root.querySelector("[data-accounting-cpa-panel]");
    if (!panel) return;
    panel.innerHTML = renderCpaCandidatePanel(cpaCandidates, cpaCandidatesLoading, selectedCpaCandidateId, saving || !storageReady);
    if (notice?.type === "error") {
      panel.insertAdjacentHTML("beforeend", `<p class="accountingCpaEmpty">${escapeHtml(notice.text)}</p>`);
    }
    bindCpaCandidateEvents();
  }
}

function renderForm(entry, disabled, cpaCandidates, cpaCandidatesLoading, selectedCpaCandidateId) {
  return `
    <section class="accountingComposer">
      <div class="accountingSectionHeader"><div><h2>${entry ? "编辑账单" : "快速记一笔"}</h2><span>人民币</span></div><span>只保留 5 个必要分类</span></div>
      <form id="accounting-entry-form" class="accountingPurchaseForm">
        <label><span>购买日期</span><input id="accounting-purchased-on" name="purchasedOn" type="date" required value="${escapeAttr(entry?.purchasedOn || localTodayKey())}" ${disabled ? "disabled" : ""} /></label>
        <label><span>分类</span><select name="category" ${disabled ? "disabled" : ""}>${renderCategoryOptions(entry?.category || CATEGORIES[0])}</select></label>
        <label><span>数量</span><input name="quantity" type="number" min="1" max="1000000" step="1" required value="${entry?.quantity || 1}" ${disabled ? "disabled" : ""} /></label>
        <label><span>单价（元）</span><input name="unitPrice" type="number" min="0" step="0.01" required value="${entry ? centsToInput(entry.unitPriceCents) : ""}" placeholder="10.00" ${disabled ? "disabled" : ""} /></label>
        <label><span>总价（可选）</span><input name="totalPrice" type="number" min="0" step="0.01" value="${entry ? centsToInput(entry.totalPriceCents) : ""}" placeholder="默认 数量 × 单价" ${disabled ? "disabled" : ""} /><small data-accounting-price-hint>${entry ? "将按填写的总价入账" : "不填则自动计算"}</small></label>
        <label><span>渠道 / 商家</span><input name="merchant" maxlength="1000" value="${escapeAttr(entry?.merchant || "")}" placeholder="在哪买的" ${disabled ? "disabled" : ""} /></label>
        <label class="accountingFormWide"><span>账号 / 卡密 ${entry?.hasCredential ? "（已有，留空保留）" : ""}</span><textarea name="credential" maxlength="100000" rows="2" placeholder="账号、密码或卡密；保存后默认遮挡" ${disabled ? "disabled" : ""}></textarea></label>
        <label><span>auth.json 文件</span><input name="authFile" type="file" accept=".json,application/json" ${disabled ? "disabled" : ""} /></label>
        <div class="accountingFormWide accountingCpaPicker" data-accounting-cpa-panel>${renderCpaCandidatePanel(cpaCandidates, cpaCandidatesLoading, selectedCpaCandidateId, disabled)}</div>
        <label class="accountingFormWide"><span>或粘贴 auth.json 内容</span><textarea name="authContent" maxlength="2000000" rows="2" placeholder='{"access_token":"..."}' ${disabled ? "disabled" : ""}></textarea></label>
        <label class="accountingFormWide"><span>备注</span><input name="note" maxlength="20000" value="${escapeAttr(entry?.note || "")}" placeholder="可不填" ${disabled ? "disabled" : ""} /></label>
        <div class="accountingFormActions">
          ${entry ? `<button class="secondaryButton" data-accounting-cancel type="button">取消</button>` : ""}
          <button class="primaryButton" type="submit" ${disabled ? "disabled" : ""}>${disabled ? "请稍候" : entry ? "保存修改" : "记下这笔"}</button>
        </div>
      </form>
      <p class="accountingSecurityHint">账号 / 卡密和 auth.json 单独保存在本机并加密，不会写入浏览器缓存或账单列表。</p>
    </section>`;
}

function renderCpaCandidatePanel(candidates, loading, selectedId, disabled) {
  const rows = Array.isArray(candidates) ? candidates : null;
  return `
    <div class="accountingCpaPickerHeader">
      <div><strong>CPA 里的 auth.json</strong><small>只找符合 Codex 登录特征的文件，不读取其他 JSON</small></div>
      <button class="secondaryButton" data-accounting-find-cpa type="button" ${disabled || loading ? "disabled" : ""}>${loading ? "正在查找…" : "从 CPA 找最近生成的 auth.json"}</button>
    </div>
    ${rows === null ? "" : rows.length ? `<div class="accountingCpaCandidates">${rows.map((item) => `
      <label class="accountingCpaCandidate ${item.id === selectedId ? "is-selected" : ""}">
        <input type="radio" name="cpaCandidateId" value="${escapeAttr(item.id)}" ${item.id === selectedId ? "checked" : ""} ${disabled ? "disabled" : ""} />
        <span><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(formatCandidateTime(item.modifiedAt))} · ${escapeHtml(formatBytes(item.size))} · ${escapeHtml(item.source)}</small></span>
      </label>`).join("")}</div>` : `<p class="accountingCpaEmpty">最近 6 小时没有尚未记账的 Codex auth.json，可继续用上面的“选择文件”。</p>`}`;
}

function renderBatch(disabled, text, preview) {
  return `
    <section class="accountingComposer">
      <div class="accountingSectionHeader"><div><h2>批量导入</h2><span>CSV / TSV</span></div><label class="accountingFileButton">选择文件<input data-accounting-batch-file type="file" accept=".csv,.tsv,text/csv,text/tab-separated-values" ${disabled ? "disabled" : ""} /></label></div>
      <p class="accountingBatchHelp">首行固定：日期,分类,数量,单价,总价,商家,账号卡密,备注,auth_json。总价可留空；分类只能用正规代充、日抛 Plus、日抛 Team、Claude、其他。</p>
      <textarea id="accounting-batch-text" class="accountingBatchText" rows="8" placeholder="日期,分类,数量,单价,总价,商家,账号卡密,备注,auth_json&#10;2026-08-13,日抛 Plus,2,8.5,,商家A,账号密码,测试,"""{""""token"""":""""...""""}""""" ${disabled ? "disabled" : ""}>${escapeHtml(text)}</textarea>
      ${preview ? `<div class="accountingBatchPreview"><strong>预览：${preview.rows.length} 条可导入 · ${formatCny(sumCents(preview.rows))}</strong><span>${preview.duplicates ? `${preview.duplicates} 条重复记录会跳过` : "没有发现重复记录"}</span></div>` : ""}
      <div class="accountingFormActions"><button class="secondaryButton" data-accounting-batch-preview type="button" ${disabled ? "disabled" : ""}>校验预览</button><button class="primaryButton" data-accounting-batch-import type="button" ${disabled || !preview?.rows.length ? "disabled" : ""}>确认导入 ${preview?.rows.length || 0} 条</button></div>
    </section>`;
}

function renderEntry(entry) {
  const credential = entry.hasCredential ? `<div class="accountingSecret"><span>账号 / 卡密：••••••••</span><button data-accounting-view-secret="${escapeAttr(entry.id)}" type="button">查看</button><button data-accounting-copy-secret="${escapeAttr(entry.id)}" type="button">复制</button></div>` : "";
  const authFiles = entry.authFiles.length ? `<div class="accountingAuthFiles">${entry.authFiles.map((file) => `<a href="${escapeAttr(file.url)}" download="${escapeAttr(file.name)}">下载 ${escapeHtml(file.name)}</a>`).join("")}</div>` : "";
  return `<article class="accountingCard">
    <div class="accountingCardTop"><div><time>${escapeHtml(entry.purchasedOn)}</time><span class="accountingCategory">${escapeHtml(entry.category)}</span></div><strong>${formatCny(entry.totalPriceCents)}</strong></div>
    <div class="accountingCardMeta"><span>${entry.quantity} 个 × ${formatCny(entry.unitPriceCents)}</span>${entry.merchant ? `<span>${escapeHtml(entry.merchant)}</span>` : ""}</div>
    ${credential}${authFiles}${entry.note ? `<p>${escapeHtml(entry.note)}</p>` : ""}
    <div class="accountingCardActions"><button data-accounting-edit="${escapeAttr(entry.id)}" type="button">编辑</button><button data-accounting-delete="${escapeAttr(entry.id)}" type="button">删除</button></div>
  </article>`;
}

function renderMetric(label, value, hint) {
  return `<div class="accountingMetric"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong><em>${escapeHtml(hint)}</em></div>`;
}

function renderCategoryOptions(selected) {
  return CATEGORIES.map((category) => `<option value="${escapeAttr(category)}" ${category === selected ? "selected" : ""}>${escapeHtml(category)}</option>`).join("");
}

function renderCategoryTotals(entries) {
  const items = CATEGORIES.map((category) => ({ category, cents: sumCents(entries.filter((entry) => entry.category === category)) })).filter((item) => item.cents > 0);
  return items.length ? `<div class="accountingCategoryTotals">${items.map((item) => `<span>${escapeHtml(item.category)} <strong>${formatCny(item.cents)}</strong></span>`).join("")}</div>` : "";
}

async function readState() {
  const response = await fetch(ENDPOINT, { cache: "no-store" });
  const payload = await response.json();
  if (!response.ok || !payload.ok || !Array.isArray(payload.entries)) throw storageError(payload?.error || "accounting_storage_read_failed");
  return payload;
}

async function writeState(entries, baseRevision) {
  const response = await fetch(ENDPOINT, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ entries, baseRevision }) });
  const payload = await response.json();
  if (response.status === 409 && payload.state) { const error = storageError("revision_conflict"); error.state = payload.state; throw error; }
  if (!response.ok || !payload.ok || !Array.isArray(payload.entries)) throw storageError(payload?.error || "accounting_storage_write_failed");
  return payload;
}

async function writeCredential(id, credential) {
  const response = await fetch(CREDENTIAL_ENDPOINT, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id, credential }) });
  const payload = await response.json();
  if (!response.ok || !payload.ok) throw storageError(payload?.error || "accounting_credential_write_failed");
}

async function readCredential(id) {
  const response = await fetch(`${CREDENTIAL_ENDPOINT}/${encodeURIComponent(id)}`, { cache: "no-store" });
  const payload = await response.json();
  if (!response.ok || !payload.ok) throw storageError(payload?.error || "accounting_credential_read_failed");
  return String(payload.credential || "");
}

async function uploadAuth(file, name) {
  if (file.size > MAX_AUTH_BYTES) throw storageError("accounting_auth_too_large");
  const response = await fetch(AUTH_ENDPOINT, { method: "POST", headers: { "Content-Type": "application/json" }, body: file });
  const payload = await response.json();
  if (!response.ok || !payload.ok || !payload.url) throw storageError(payload?.error || "accounting_auth_write_failed");
  return { id: `auth:${crypto.randomUUID()}`, name: String(name || "auth.json").slice(0, 512), size: payload.size || file.size, url: payload.url };
}

async function readCpaAuthCandidates() {
  const response = await fetch(AUTH_CANDIDATES_ENDPOINT, { cache: "no-store" });
  const payload = await response.json();
  if (!response.ok || !payload.ok || !Array.isArray(payload.candidates)) throw storageError(payload?.error || "accounting_auth_candidates_read_failed");
  return payload.candidates.filter((item) => item && /^[0-9a-f]{64}$/.test(item.id) && Number.isInteger(item.size) && item.size > 0 && item.size <= MAX_AUTH_BYTES);
}

async function importCpaAuth(candidateId, name) {
  const response = await fetch(`${AUTH_CANDIDATES_ENDPOINT}/import`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ candidateId }),
  });
  const payload = await response.json();
  if (!response.ok || !payload.ok || !payload.url) throw storageError(payload?.error || "accounting_auth_write_failed");
  return { id: `auth:${crypto.randomUUID()}`, name: String(name || "auth.json").slice(0, 512), size: payload.size, url: payload.url };
}

function mergeEntries(primary, fallback) {
  const map = new Map();
  [...normalizeEntries(fallback), ...normalizeEntries(primary)].forEach((entry) => {
    const current = map.get(entry.id);
    if (!current) {
      map.set(entry.id, entry);
      return;
    }
    // There is no restore action in the UI. Once either side has a tombstone,
    // keep the deletion so an older browser cannot bring the record back.
    if (current.deletedAt || entry.deletedAt) {
      const deleted = [current, entry]
        .filter((item) => item.deletedAt)
        .sort((left, right) => right.updatedAt.localeCompare(left.updatedAt))[0];
      map.set(entry.id, deleted);
      return;
    }
    if (entry.updatedAt >= current.updatedAt) map.set(entry.id, entry);
  });
  return [...map.values()].sort(compareNewest);
}

function normalizeEntries(value) { return Array.isArray(value) ? value.map(normalizeEntry).filter(Boolean) : []; }
function activeEntries(value) { return normalizeEntries(value).filter((entry) => !entry.deletedAt).sort(compareNewest); }

function normalizeEntry(value) {
  if (!value || typeof value !== "object") return null;
  const now = new Date().toISOString();
  const createdAt = normalizeIso(value.createdAt, now);
  const quantity = Math.max(1, Math.min(1000000, Number.parseInt(value.quantity, 10) || 1));
  const unitPriceCents = normalizeCents(value.unitPriceCents);
  const totalPriceCents = normalizeCents(value.totalPriceCents ?? unitPriceCents * quantity);
  return {
    id: safeId(value.id, "purchase"), purchasedOn: normalizeDateKey(value.purchasedOn, localDateKeyFromIso(createdAt)),
    category: normalizeCategory(value.category), quantity, unitPriceCents, totalPriceCents,
    merchant: String(value.merchant || "").slice(0, 1000), note: String(value.note || "").slice(0, 20000),
    hasCredential: Boolean(value.hasCredential), authFiles: normalizeAuthFiles(value.authFiles),
    importFingerprint: String(value.importFingerprint || "").replace(/[^a-f0-9]/gi, "").slice(0, 64),
    createdAt, updatedAt: normalizeIso(value.updatedAt, createdAt), deletedAt: value.deletedAt ? normalizeIso(value.deletedAt, createdAt) : "",
  };
}

function normalizeAuthFiles(value) {
  return Array.isArray(value) ? value.map((item) => {
    if (!item || !String(item.url || "").match(/^\/api\/accounting-auth\/[0-9a-f]{64}$/)) return null;
    return { id: safeId(item.id, "auth"), name: String(item.name || "auth.json").slice(0, 512), size: Math.max(1, Number.parseInt(item.size, 10) || 1), url: item.url };
  }).filter(Boolean).slice(0, 20) : [];
}

function mergeAuthFiles(primary, fallback) { const map = new Map([...primary, ...fallback].map((item) => [item.url, item])); return [...map.values()].slice(0, 20); }

function loadBackup() {
  try { const parsed = JSON.parse(localStorage.getItem(BACKUP_KEY) || "[]"); return normalizeEntries(Array.isArray(parsed) ? parsed : parsed?.entries); } catch { return []; }
}

function saveBackup(entries) {
  try { localStorage.setItem(BACKUP_KEY, JSON.stringify({ version: 2, entries: normalizeEntries(entries) })); } catch {}
}

function parseBatch(text) {
  const source = String(text || "").trim();
  if (!source) throw storageError("empty_batch");
  const delimiter = source.split(/\r?\n/, 1)[0].includes("\t") ? "\t" : ",";
  const table = parseDelimited(source, delimiter);
  if (table.length < 2) throw storageError("empty_batch");
  const expected = ["日期", "分类", "数量", "单价", "总价", "商家", "账号卡密", "备注", "auth_json"];
  const header = table[0].map((item) => item.trim());
  if (expected.some((name, index) => header[index] !== name)) throw storageError("invalid_batch_header");
  return table.slice(1).filter((row) => row.some((cell) => cell.trim())).map((row, index) => {
    const purchasedOn = normalizeDateKey(row[0], "");
    const category = normalizeCategory(row[1], false);
    const quantityText = String(row[2] || "").trim();
    const quantity = /^\d+$/.test(quantityText) ? Number(quantityText) : Number.NaN;
    const unitPriceCents = parseMoneyToCents(row[3]);
    const totalPriceCents = String(row[4] || "").trim() ? parseMoneyToCents(row[4]) : unitPriceCents * quantity;
    if (!purchasedOn || !category || !Number.isInteger(quantity) || quantity < 1 || !Number.isInteger(unitPriceCents) || !Number.isInteger(totalPriceCents)) throw storageErrorWithRow("invalid_batch_row", index + 2);
    if (row[8]) validateJsonText(row[8]);
    return { purchasedOn, category, quantity, unitPriceCents, totalPriceCents, merchant: String(row[5] || "").slice(0, 1000), credential: String(row[6] || "").slice(0, 100000), note: String(row[7] || "").slice(0, 20000), authJson: String(row[8] || "") };
  });
}

function parseDelimited(text, delimiter) {
  const rows = []; let row = []; let field = ""; let quoted = false;
  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    if (quoted) {
      if (char === '"' && text[index + 1] === '"') { field += '"'; index += 1; }
      else if (char === '"') quoted = false;
      else field += char;
    } else if (char === '"') quoted = true;
    else if (char === delimiter) { row.push(field); field = ""; }
    else if (char === "\n") { row.push(field.replace(/\r$/, "")); rows.push(row); row = []; field = ""; }
    else field += char;
  }
  if (quoted) throw storageError("invalid_batch_quotes");
  row.push(field.replace(/\r$/, "")); rows.push(row);
  return rows;
}

async function fingerprintRow(row) {
  const stable = [row.purchasedOn, row.category, row.quantity, row.unitPriceCents, row.totalPriceCents, row.merchant.trim(), row.note.trim()].join("\u001f");
  const bytes = new TextEncoder().encode(stable);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((value) => value.toString(16).padStart(2, "0")).join("");
}

function validateJsonText(text) { try { const parsed = JSON.parse(text); if (!parsed || typeof parsed !== "object") throw new Error(); } catch { throw storageError("invalid_accounting_auth_json"); } }
function normalizeCategory(value, fallback = true) { const text = String(value || "").trim(); return CATEGORIES.includes(text) ? text : fallback ? "其他" : ""; }
function normalizeCents(value) { const number = Number(value); return Number.isSafeInteger(number) && number >= 0 ? number : 0; }
function parseMoneyToCents(value) { const text = String(value ?? "").trim(); if (!/^\d+(?:\.\d{1,2})?$/.test(text)) return Number.NaN; const [whole, decimal = ""] = text.split("."); const cents = Number(whole) * 100 + Number(decimal.padEnd(2, "0")); return Number.isSafeInteger(cents) ? cents : Number.NaN; }
function centsToInput(value) { return (normalizeCents(value) / 100).toFixed(2); }
function sumCents(entries) { return entries.reduce((total, entry) => total + normalizeCents(entry.totalPriceCents), 0); }
function countInRange(entries, bounds) { return inRange(entries, bounds).length; }
function inRange(entries, bounds) { return entries.filter((entry) => (!bounds.from || entry.purchasedOn >= bounds.from) && (!bounds.to || entry.purchasedOn <= bounds.to)); }

function rangeBounds(kind, customFrom = "", customTo = "") {
  const now = new Date(); const today = localDateKeyFromDate(now);
  if (kind === "today") return { from: today, to: today };
  if (kind === "week") { const day = now.getDay() || 7; const monday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - day + 1); const sunday = new Date(monday.getFullYear(), monday.getMonth(), monday.getDate() + 6); return { from: localDateKeyFromDate(monday), to: localDateKeyFromDate(sunday) }; }
  if (kind === "month") { const first = new Date(now.getFullYear(), now.getMonth(), 1); const last = new Date(now.getFullYear(), now.getMonth() + 1, 0); return { from: localDateKeyFromDate(first), to: localDateKeyFromDate(last) }; }
  if (kind === "custom") return { from: normalizeDateKey(customFrom, ""), to: normalizeDateKey(customTo, "") };
  return { from: "", to: "" };
}

function formatCny(cents) { return new Intl.NumberFormat("zh-CN", { style: "currency", currency: "CNY", minimumFractionDigits: 2 }).format(normalizeCents(cents) / 100); }
function compareNewest(left, right) { return right.purchasedOn.localeCompare(left.purchasedOn) || right.createdAt.localeCompare(left.createdAt); }
function localTodayKey() { return localDateKeyFromDate(new Date()); }
function localDateKeyFromIso(value) { const date = new Date(value); return Number.isNaN(date.getTime()) ? localTodayKey() : localDateKeyFromDate(date); }
function localDateKeyFromDate(date) { return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`; }
function normalizeDateKey(value, fallback) { return isDateKey(value) ? String(value) : fallback; }
function isDateKey(value) { if (!/^\d{4}-\d{2}-\d{2}$/.test(String(value || ""))) return false; const [year, month, day] = String(value).split("-").map(Number); const date = new Date(year, month - 1, day); return date.getFullYear() === year && date.getMonth() === month - 1 && date.getDate() === day; }
function normalizeRevision(value) { const revision = Number(value); return Number.isInteger(revision) && revision >= 0 ? revision : 0; }
function normalizeIso(value, fallback) { const stamp = typeof value === "string" ? Date.parse(value) : Number.NaN; return Number.isNaN(stamp) ? fallback : new Date(stamp).toISOString(); }
function safeId(value, prefix) { const source = String(value || `${prefix}:${crypto.randomUUID()}`); return source.replace(/[^A-Za-z0-9._:-]/g, "-").slice(0, 128) || `${prefix}:${crypto.randomUUID()}`; }
function storageError(code) { const error = new Error(code); error.code = code; return error; }
function storageErrorWithRow(code, row) { const error = storageError(code); error.row = row; return error; }
function errorMessage(error, fallback) {
  const messages = { revision_conflict: "另一个页面刚更新了账本，请重试。", accounting_storage_read_failed: "本机账本读取失败，为保护数据已停止编辑。", accounting_storage_write_failed: "本机账本写入失败。", accounting_storage_too_large: "账本数据过大。", accounting_credential_write_failed: "账号 / 卡密安全保存失败。", accounting_credential_read_failed: "账号 / 卡密读取失败。", accounting_auth_write_failed: "auth.json 保存失败。", accounting_auth_read_failed: "auth.json 读取失败。", accounting_auth_candidates_read_failed: "无法读取 CPA 的 auth.json 候选。", accounting_auth_candidate_changed: "这个 CPA auth.json 已变化、已记过账或已超过最近时间，请重新查找。", invalid_accounting_auth_candidate: "请选择一个有效的 CPA auth.json。", accounting_auth_already_bound: "这个 auth.json 已经绑定到另一条账单。", accounting_auth_too_large: "auth.json 不能超过 2 MB。", invalid_accounting_auth_json: "auth.json 内容不是有效 JSON。", invalid_batch_header: "表头不正确，请使用页面给出的固定 9 列。", invalid_batch_quotes: "CSV 引号格式不正确。", empty_batch: "请粘贴至少一条账单。", invalid_batch_row: `第 ${error?.row || "?"} 行格式不正确。` };
  return messages[error?.code || error?.message] || fallback;
}
function formatCandidateTime(value) { const stamp = Date.parse(value); return Number.isNaN(stamp) ? "时间未知" : new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(stamp)); }
function formatBytes(value) { const bytes = Number(value); return bytes < 1024 ? `${bytes} B` : `${(bytes / 1024).toFixed(bytes < 10240 ? 1 : 0)} KB`; }
function escapeHtml(value) { return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;"); }
function escapeAttr(value) { return escapeHtml(value).replaceAll("`", "&#096;"); }
