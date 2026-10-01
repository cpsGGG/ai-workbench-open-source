import { createPaperTrendsFeature } from "./paper-trends.js";

const STORAGE_KEY = "ai-workbench:papers:v1";

const statusLabels = {
  all: "全部",
  unread: "未读",
  reading: "阅读中",
  summarized: "已总结",
  finished: "完成",
};

const accents = ["#3b82f6", "#14b8a6", "#f97316", "#a855f7", "#ef4444", "#22c55e"];

const seedPapers = [
  {
    id: "manual-attention-is-all-you-need",
    title: "Attention Is All You Need",
    authors: "Ashish Vaswani et al.",
    venue: "NeurIPS",
    year: "2017",
    status: "finished",
    tags: ["Transformer", "NLP", "示例"],
    abstract: "如果本地服务没有扫描到 Obsidian PDF，会显示这条示例数据。",
    keyIdeas: ["Self-Attention", "Multi-Head Attention", "Positional Encoding"],
    pdfUrl: "https://arxiv.org/abs/1706.03762",
    thumbnailUrl: "",
    obsidianUrl: "",
    obsidianVault: "",
    obsidianPath: "",
    source: "manual",
    addedAt: "2026-07-04T00:00:00.000Z",
    accent: "#3b82f6",
  },
];

export function createPaperWorkspace(root) {
  let papers = loadManualPapers();
  let vaults = [];
  let selectedId = papers[0]?.id ?? "";
  let query = "";
  let status = "all";
  let autoOpen = true;
  let modalOpen = false;
  let loading = true;
  let apiMessage = "正在读取本地 Obsidian vault...";
  let activePaperView = "library";
  const paperTrends = createPaperTrendsFeature(render);

  loadObsidianPapers();

  async function loadObsidianPapers() {
    try {
      const response = await fetch("/api/papers", { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      vaults = data.vaults ?? [];
      const scanned = (data.papers ?? []).map((paper) => ({ ...paper, source: "obsidian" }));
      const manual = loadManualPapers().filter((paper) => paper.source !== "obsidian");
      papers = scanned.length ? [...scanned, ...manual] : manual;
      selectedId = papers[0]?.id ?? "";
      apiMessage = scanned.length
        ? `已从 Obsidian 读取 ${scanned.length} 篇 PDF`
        : "没有在 Obsidian vault 中扫描到 PDF";
    } catch (error) {
      papers = loadManualPapers();
      apiMessage = "本地扫描服务未连接，当前显示手动数据";
    } finally {
      loading = false;
      render();
    }
  }

  function filteredPapers() {
    const normalized = query.trim().toLowerCase();
    return papers.filter((paper) => {
      const content = [
        paper.title,
        paper.authors,
        paper.venue,
        paper.year,
        paper.obsidianPath,
        ...(paper.tags ?? []),
      ]
        .join(" ")
        .toLowerCase();
      return (status === "all" || paper.status === status) && (!normalized || content.includes(normalized));
    });
  }

  function selectedPaper() {
    const visible = filteredPapers();
    return papers.find((paper) => paper.id === selectedId) ?? visible[0] ?? papers[0];
  }

  function selectPaper(paper) {
    selectedId = paper.id;
    render();
    if (autoOpen) openObsidian(paper);
  }

  function openObsidian(paper) {
    if (paper.obsidianUrl) {
      window.location.href = paper.obsidianUrl;
      return;
    }

    if (!paper.obsidianVault || !paper.obsidianPath) return;
    const query = new URLSearchParams({ vault: paper.obsidianVault, file: paper.obsidianPath });
    window.location.href = `obsidian://open?${query.toString()}`;
  }

  function createPaper(form) {
    const data = new FormData(form);
    const title = String(data.get("title") ?? "").trim();
    if (!title) return;

    const paper = {
      id: `manual-${Date.now()}-${title.toLowerCase().replace(/\s+/g, "-")}`,
      title,
      authors: String(data.get("authors") ?? ""),
      venue: String(data.get("venue") ?? ""),
      year: String(data.get("year") ?? "2026"),
      status: String(data.get("status") ?? "unread"),
      tags: splitList(String(data.get("tags") ?? "")),
      abstract: String(data.get("abstract") ?? ""),
      keyIdeas: splitList(String(data.get("keyIdeas") ?? "")),
      pdfUrl: String(data.get("pdfUrl") ?? ""),
      thumbnailUrl: "",
      obsidianUrl: "",
      obsidianVault: String(data.get("obsidianVault") ?? ""),
      obsidianPath: String(data.get("obsidianPath") ?? ""),
      source: "manual",
      addedAt: new Date().toISOString(),
      accent: accents[papers.length % accents.length],
    };

    const manual = [paper, ...loadManualPapers().filter((item) => item.source !== "obsidian")];
    saveManualPapers(manual);
    papers = [paper, ...papers];
    selectedId = paper.id;
    modalOpen = false;
    render();
  }

  function render() {
    const selected = selectedPaper();
    const visible = filteredPapers();
    const scannedCount = papers.filter((paper) => paper.source === "obsidian").length;
    const libraryContent = `
      <div class="paperStatusBar">
        <span>${escapeHtml(apiMessage)}</span>
        <span>${loading ? "扫描中" : `Obsidian ${scannedCount} 篇 · 手动 ${papers.length - scannedCount} 篇`}</span>
      </div>

      <div class="paperLayout">
        <div class="paperListPane">
          <div class="searchBox">
            <span>Q</span>
            <input id="paper-search" placeholder="搜索标题、目录、标签" value="${escapeHtml(query)}" />
          </div>

          <div class="segmented">
            ${Object.entries(statusLabels)
              .map(([key, label]) => `<button class="${status === key ? "selected" : ""}" data-status="${key}" type="button">${label}</button>`)
              .join("")}
          </div>

          <div class="paperGrid">
            ${
              visible.length
                ? visible.map((paper) => renderCard(paper, selected?.id === paper.id)).join("")
                : `<div class="emptyState">暂无匹配论文</div>`
            }
          </div>
        </div>

        <aside class="detailPane">
          ${selected ? renderDetail(selected) : `<div class="emptyState">暂无论文</div>`}
        </aside>
      </div>
    `;

    root.innerHTML = `
      <header class="topbar">
        <div class="paperHeaderBlock">
          <p class="eyebrow">Paper Desk</p>
          <h1>论文工作台</h1>
          <nav class="paperModeTabs" aria-label="论文子功能">
            <button class="${activePaperView === "library" ? "selected" : ""}" id="paper-library-view" type="button" aria-current="${activePaperView === "library" ? "page" : "false"}">论文库</button>
            <button class="${activePaperView === "trends" ? "selected" : ""}" id="paper-trends-view" type="button" aria-current="${activePaperView === "trends" ? "page" : "false"}"><span aria-hidden="true">⌁</span>抓取</button>
          </nav>
        </div>
        ${activePaperView === "library" ? `<div class="toolbar">
          <label class="toggle">
            <input id="auto-open" ${autoOpen ? "checked" : ""} type="checkbox" />
            <span>选中即打开 Obsidian</span>
          </label>
          <button class="iconButton" id="refresh-papers" title="重新扫描 Obsidian" type="button">↻</button>
          <label class="iconButton" title="导入 JSON">
            <span>IN</span>
            <input id="import-papers" accept="application/json" type="file" />
          </label>
          <button class="iconButton" id="export-papers" title="导出 JSON" type="button">OUT</button>
          <button class="primaryButton" id="new-paper" type="button"><span>+</span><span>新论文</span></button>
        </div>` : ""}
      </header>

      ${activePaperView === "trends" ? paperTrends.render() : libraryContent}

      ${activePaperView === "library" && modalOpen ? renderModal() : ""}
    `;

    bindEvents();
  }

  function bindEvents() {
    root.querySelector("#paper-library-view")?.addEventListener("click", () => {
      if (activePaperView === "library") return;
      paperTrends.cancel();
      activePaperView = "library";
      render();
    });

    root.querySelector("#paper-trends-view")?.addEventListener("click", () => {
      if (activePaperView !== "trends") {
        activePaperView = "trends";
        render();
      }
      paperTrends.load({ force: true });
    });

    paperTrends.bind(root);

    root.querySelector("#auto-open")?.addEventListener("change", (event) => {
      autoOpen = event.target.checked;
    });

    root.querySelector("#paper-search")?.addEventListener("input", (event) => {
      query = event.target.value;
      render();
    });

    root.querySelector("#refresh-papers")?.addEventListener("click", () => {
      loading = true;
      apiMessage = "正在重新扫描 Obsidian vault...";
      render();
      loadObsidianPapers();
    });

    root.querySelectorAll("[data-status]").forEach((button) => {
      button.addEventListener("click", () => {
        status = button.dataset.status;
        render();
      });
    });

    root.querySelectorAll("[data-paper-id]").forEach((button) => {
      button.addEventListener("click", () => {
        const paper = papers.find((item) => item.id === button.dataset.paperId);
        if (paper) selectPaper(paper);
      });
    });

    root.querySelector("#new-paper")?.addEventListener("click", () => {
      modalOpen = true;
      render();
    });

    root.querySelector("#close-modal")?.addEventListener("click", () => {
      modalOpen = false;
      render();
    });

    root.querySelector("#cancel-modal")?.addEventListener("click", () => {
      modalOpen = false;
      render();
    });

    root.querySelector("#paper-form")?.addEventListener("submit", (event) => {
      event.preventDefault();
      createPaper(event.currentTarget);
    });

    root.querySelector("#open-obsidian")?.addEventListener("click", () => {
      const selected = selectedPaper();
      if (selected) openObsidian(selected);
    });

    root.querySelector("#export-papers")?.addEventListener("click", () => downloadPapers(papers));

    root.querySelector("#import-papers")?.addEventListener("change", importPapers);
  }

  function importPapers(event) {
    const file = event.target.files?.[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = () => {
      try {
        const incoming = JSON.parse(String(reader.result));
        if (!Array.isArray(incoming)) throw new Error("not an array");
        const manual = incoming.map((paper) => ({ ...paper, source: paper.source ?? "manual" }));
        saveManualPapers(manual);
        papers = [...papers.filter((paper) => paper.source === "obsidian"), ...manual];
        selectedId = papers[0]?.id ?? "";
        render();
      } catch {
        window.alert("JSON 文件格式不正确");
      }
    };
    reader.readAsText(file);
  }

  render();
}

function renderCard(paper, selected) {
  const subtitle = paper.relativeDir || paper.venue || paper.obsidianVault || paper.year || "论文";
  return `
    <button class="paperCard visual ${selected ? "selected" : ""}" data-paper-id="${escapeAttr(paper.id)}" style="--accent: ${escapeAttr(paper.accent)}" type="button">
      <div class="pdfThumb">
        ${
          paper.thumbnailUrl
            ? `<img src="${escapeAttr(paper.thumbnailUrl)}" alt="${escapeAttr(paper.title)} 首页" loading="lazy" />`
            : `<div class="pdfPlaceholder">PDF</div>`
        }
      </div>
      <div class="cardHeader">
        <span>${escapeHtml(paper.year || paper.obsidianVault || "PDF")}</span>
        <strong>${paper.source === "obsidian" ? "Obsidian" : statusLabels[paper.status] ?? paper.status}</strong>
      </div>
      <h2>${escapeHtml(paper.title)}</h2>
      <p>${escapeHtml(subtitle)}</p>
      <div class="tagRow">
        ${(paper.tags ?? []).slice(0, 3).map((tag) => `<span>${escapeHtml(tag)}</span>`).join("")}
      </div>
    </button>
  `;
}

function renderDetail(paper) {
  return `
    <div class="detailHero" style="--accent: ${escapeAttr(paper.accent)}">
      <span>${escapeHtml(paper.venue || paper.obsidianVault || "PDF")}</span>
      <h2>${escapeHtml(paper.title)}</h2>
      <p>${escapeHtml(paper.obsidianPath || paper.authors || "")}</p>
    </div>

    <div class="pdfPreviewLarge">
      ${
        paper.thumbnailUrl
          ? `<img src="${escapeAttr(paper.thumbnailUrl)}" alt="${escapeAttr(paper.title)} 首页预览" />`
          : `<div class="pdfPlaceholder">PDF 首页预览</div>`
      }
    </div>

    <div class="detailActions">
      <button class="primaryButton" id="open-obsidian" type="button"><span>O</span><span>打开 Obsidian</span></button>
      ${
        paper.pdfUrl
          ? `<a class="secondaryButton" href="${escapeAttr(paper.pdfUrl)}" rel="noreferrer" target="_blank"><span>↗</span><span>浏览器打开</span></a>`
          : ""
      }
    </div>

    <section class="detailSection">
      <h3>路径</h3>
      <p>${escapeHtml(paper.obsidianVault || "手动")} / ${escapeHtml(paper.obsidianPath || paper.pdfUrl || "")}</p>
    </section>

    <section class="detailSection">
      <h3>关键点</h3>
      <ul>
        ${
          (paper.keyIdeas ?? []).length
            ? paper.keyIdeas.map((idea) => `<li><span class="checkMark">✓</span><span>${escapeHtml(idea)}</span></li>`).join("")
            : `<li><span class="checkMark">✓</span><span>等待 AI 读论文后生成</span></li>`
        }
      </ul>
    </section>
  `;
}

function renderModal() {
  return `
    <div class="modalBackdrop" role="presentation">
      <form class="paperModal" id="paper-form">
        <div class="modalHeader">
          <h2>新论文</h2>
          <button id="close-modal" type="button">关闭</button>
        </div>

        <div class="formGrid">
          <label>标题<input name="title" required /></label>
          <label>作者<input name="authors" /></label>
          <label>会议/期刊<input name="venue" /></label>
          <label>年份<input name="year" value="2026" /></label>
          <label>
            状态
            <select name="status">
              <option value="unread">未读</option>
              <option value="reading">阅读中</option>
              <option value="summarized">已总结</option>
              <option value="finished">完成</option>
            </select>
          </label>
          <label>Obsidian 库<input name="obsidianVault" /></label>
          <label class="wide">Obsidian 文件<input name="obsidianPath" placeholder="论文/xxx.pdf 或 论文/xxx.md" /></label>
          <label class="wide">论文链接<input name="pdfUrl" /></label>
          <label class="wide">标签<input name="tags" placeholder="RAG, Agent, 综述" /></label>
          <label class="wide">摘要<textarea name="abstract"></textarea></label>
          <label class="wide">关键点<textarea name="keyIdeas"></textarea></label>
        </div>

        <div class="modalFooter">
          <button class="secondaryButton" id="cancel-modal" type="button">取消</button>
          <button class="primaryButton" type="submit">保存</button>
        </div>
      </form>
    </div>
  `;
}

function loadManualPapers() {
  const raw = localStorage.getItem(STORAGE_KEY);
  if (!raw) return seedPapers;

  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : seedPapers;
  } catch {
    return seedPapers;
  }
}

function saveManualPapers(papers) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(papers));
}

function downloadPapers(papers) {
  const blob = new Blob([JSON.stringify(papers, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "papers.json";
  link.click();
  URL.revokeObjectURL(url);
}

function splitList(value) {
  return value
    .split(/[,，\n]/)
    .map((item) => item.trim())
    .filter(Boolean);
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
  return escapeHtml(value).replaceAll("`", "&#096;");
}
