const STORAGE_KEY = "ai-workbench:videos:v1";
const SHARED_STORAGE_MIGRATED_KEY = "ai-workbench:videos:shared-store-migrated:v1";
const SHARED_STORAGE_ENDPOINT = "/api/video-storage";
const LEGACY_DEMO_IDS = new Set([
  "demo-bilibili-bad-apple",
  "demo-youtube-openai-devday",
  "demo-douyin-copy-link",
]);

const platformLabels = {
  all: "全部",
  bilibili: "B站",
  youtube: "YouTube",
  douyin: "抖音",
  other: "其他",
};

const statusLabels = {
  planned: "想看",
  watching: "观看中",
  saved: "已收藏",
  finished: "看完",
};

const platformAccents = {
  bilibili: "#00a1d6",
  youtube: "#ef4444",
  douyin: "#111827",
  other: "#64748b",
};

const emptyYouTubeImportBatch = {
  batchId: "youtube-import-empty",
  sourceType: "import-json",
  label: "抓取结果 JSON",
  accountHandle: "",
  foundCount: 0,
  availableRecordCount: 0,
  unavailableCount: 0,
  entries: [],
};

export function createVideoWorkspace(root) {
  let videos = loadVideos();
  let selectedId = videos[0]?.id ?? "";
  let sharedStorageRevision = 0;
  let sharedStorageReady = false;
  let sharedStorageMigrated = loadSharedStorageMigrated();
  let sharedCommitPending = false;
  let workspaceNotice = null;
  let query = "";
  let platform = "all";
  let modalOpen = false;
  let youtubeImportOpen = false;
  let youtubeImportUrl = "";
  let youtubeImportJson = "";
  let youtubeImportLoading = false;
  let youtubeImportBatch = createYouTubeImportBatch(emptyYouTubeImportBatch);
  let youtubeImportSelected = new Set();
  let youtubeImportNotice = {
    state: "",
    message: "可读取公开播放列表 URL，或粘贴本机辅助抓取的 JSON 后预览。",
  };
  let hydrationStarted = false;
  let modalVersion = 0;
  let isSaving = false;

  async function syncSharedVideoStorage() {
    try {
      let state = await fetchSharedVideoStorage();
      let remoteVideos = normalizeVideoCollection(state.videos);
      sharedStorageRevision = state.revision;

      const needsLegacyMigration = !sharedStorageMigrated;
      const needsMissingStoreRecovery = sharedStorageMigrated && !state.initialized && videos.length > 0;
      if (needsLegacyMigration || needsMissingStoreRecovery) {
        const legacyVideos = videos.filter((video) => !isLegacyDemoVideo(video));
        let merged = mergeVideoCollections(legacyVideos, remoteVideos);
        try {
          state = await writeSharedVideoStorage(merged, sharedStorageRevision);
        } catch (error) {
          if (error?.code !== "revision_conflict" || !error.state) throw error;
          remoteVideos = normalizeVideoCollection(error.state.videos);
          sharedStorageRevision = error.state.revision;
          merged = mergeVideoCollections(legacyVideos, remoteVideos);
          state = await writeSharedVideoStorage(merged, sharedStorageRevision);
        }
        if (needsLegacyMigration) {
          sharedStorageMigrated = true;
          try {
            saveSharedStorageMigrated();
          } catch {
            workspaceNotice = { state: "error", message: "共享迁移已完成，但当前浏览器无法保存迁移标记。" };
          }
        }
      }

      sharedStorageRevision = state.revision;
      sharedStorageReady = true;
      videos = normalizeVideoCollection(state.videos);
      selectedId = videos.some((video) => video.id === selectedId) ? selectedId : videos[0]?.id ?? "";
      if (!workspaceNotice?.message?.includes("迁移标记")) workspaceNotice = null;
      try {
        saveVideos(videos);
      } catch {
        workspaceNotice = { state: "error", message: "共享视频已读取，但当前浏览器缓存写入失败。" };
      }
      render();
    } catch {
      sharedStorageReady = false;
      workspaceNotice = {
        state: "error",
        message: sharedStorageMigrated
          ? "本机共享视频存储暂时不可用；为避免覆盖其他浏览器的数据，编辑已暂停。"
          : "本机共享视频存储暂时不可用，当前仍使用此浏览器的本地数据。",
      };
      render();
    }
  }

  async function commitVideoCollection(nextVideos) {
    if (sharedCommitPending) return { ok: false, error: "pending" };
    const normalized = normalizeVideoCollection(nextVideos);

    if (!sharedStorageReady) {
      if (sharedStorageMigrated) {
        workspaceNotice = { state: "error", message: "共享存储离线，本次修改未保存。请恢复本地服务并刷新页面后重试。" };
        return { ok: false, error: "shared_storage_offline" };
      }
      try {
        saveVideos(normalized);
      } catch {
        workspaceNotice = { state: "error", message: "当前浏览器本地存储写入失败，本次修改未保存。" };
        return { ok: false, error: "local_storage_failed" };
      }
      videos = normalized;
      workspaceNotice = null;
      return { ok: true, videos };
    }

    sharedCommitPending = true;
    try {
      const state = await writeSharedVideoStorage(normalized, sharedStorageRevision);
      sharedStorageRevision = state.revision;
      videos = normalizeVideoCollection(state.videos);
      workspaceNotice = null;
      try {
        saveVideos(videos);
      } catch {
        workspaceNotice = { state: "error", message: "修改已保存到共享存储，但当前浏览器缓存写入失败。" };
      }
      return { ok: true, videos };
    } catch (error) {
      if (error?.code === "revision_conflict" && error.state) {
        sharedStorageRevision = error.state.revision;
        videos = normalizeVideoCollection(error.state.videos);
        try {
          saveVideos(videos);
        } catch {
        }
        workspaceNotice = { state: "error", message: "其他浏览器刚刚更新了视频列表，已载入最新数据；请重试本次操作。" };
        return { ok: false, error: "revision_conflict" };
      }
      sharedStorageReady = false;
      workspaceNotice = { state: "error", message: "共享存储写入失败，本次修改未保存。" };
      return { ok: false, error: "shared_storage_failed" };
    } finally {
      sharedCommitPending = false;
    }
  }

  function filteredVideos() {
    const normalized = query.trim().toLowerCase();
    return videos.filter((video) => {
      const content = [video.title, video.author, video.url, video.platformLabel, video.description, ...(video.tags ?? [])]
        .join(" ")
        .toLowerCase();
      return (platform === "all" || video.platform === platform) && (!normalized || content.includes(normalized));
    });
  }

  function selectedVideo() {
    const visible = filteredVideos();
    return visible.find((video) => video.id === selectedId) ?? visible[0] ?? null;
  }

  function selectAndOpenVideo(video) {
    selectedId = video.id;
    render();
    openVideo(video);
  }

  function openVideo(video) {
    if (!video?.url) return;
    window.open(video.url, "_blank", "noreferrer");
  }

  async function hydrateMissingVideoMetadata() {
    if (hydrationStarted) return;
    hydrationStarted = true;

    const targets = videos
      .filter((video) => video.url && (!video.thumbnailUrl || !video.author || video.titleSource === "generated"))
      .slice(0, 12);

    if (!targets.length) return;

    let changed = false;
    for (const target of targets) {
      try {
        const response = await fetch(`/api/video-meta?url=${encodeURIComponent(target.url)}`, { cache: "no-store" });
        const metadata = await response.json();
        if (!metadata.ok) continue;

        const video = videos.find((item) => item.id === target.id);
        if (!video) continue;

        const thumbnailUrl = metadata.thumbnailUrl || metadata.image || "";
        const author = metadata.author || metadata.uploader || "";

        if (thumbnailUrl && !video.thumbnailUrl) {
          video.thumbnailUrl = thumbnailUrl;
          changed = true;
        }

        if (author && !video.author) {
          video.author = author;
          changed = true;
        }

        if (metadata.authorUrl && !video.authorUrl) {
          video.authorUrl = metadata.authorUrl;
          changed = true;
        }

        if (metadata.title && (!video.title || video.titleSource === "generated")) {
          video.title = metadata.title;
          video.titleSource = "metadata";
          changed = true;
        }

        if (metadata.source && !video.metadataSource) {
          video.metadataSource = metadata.source;
          changed = true;
        }

        if (metadata.fetchedAt && !video.metadataFetchedAt) {
          video.metadataFetchedAt = new Date(metadata.fetchedAt * 1000).toISOString();
          changed = true;
        }
      } catch {
        // Metadata is a convenience layer; the saved link should remain usable if fetching fails.
      }
    }

    if (changed) {
      await commitVideoCollection(videos);
      if (!modalOpen && !youtubeImportOpen) render();
    }
  }

  async function loadYouTubePlaylist(form) {
    if (youtubeImportLoading) return;
    youtubeImportUrl = String(new FormData(form).get("playlistUrl") ?? "").trim();
    clearYouTubeImportPreview("youtube-playlist-url", "公开播放列表 URL");
    if (!youtubeImportUrl) {
      youtubeImportNotice = { state: "error", message: "请先粘贴一个 YouTube 播放列表 URL。" };
      render();
      return;
    }

    youtubeImportLoading = true;
    youtubeImportNotice = { state: "loading", message: "正在读取公开播放列表，请稍候…" };
    render();

    try {
      const response = await fetch("/api/youtube-playlist/import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: youtubeImportUrl }),
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) {
        throw new Error(youtubePlaylistErrorMessage(payload.error));
      }

      youtubeImportBatch = createYouTubeImportBatch({
        batchId: `youtube-playlist-${payload.playlistId || Date.now()}`,
        label: "公开播放列表 URL",
        accountHandle: payload.channel || "",
        playlistTitle: payload.title || "",
        foundCount: payload.foundCount,
        availableRecordCount: payload.availableCount,
        unavailableCount: payload.unavailableCount,
        truncated: payload.truncated,
        truncatedCount: payload.truncatedCount,
        entries: payload.entries,
      });
      youtubeImportSelected = new Set(youtubeImportBatch.entries.map((entry) => entry.youtubeVideoId));
      youtubeImportNotice = {
        state: "success",
        message: `已读取「${youtubeImportBatch.playlistTitle || "YouTube 播放列表"}」，请确认后导入。`,
      };
    } catch (error) {
      youtubeImportNotice = {
        state: "error",
        message: error instanceof Error ? error.message : "播放列表读取失败，原有收藏未发生变化。",
      };
    } finally {
      youtubeImportLoading = false;
      render();
    }
  }

  function loadYouTubeImportJson(form) {
    const raw = String(new FormData(form).get("importJson") ?? "").trim();
    youtubeImportJson = raw;
    clearYouTubeImportPreview("local-assisted-json", "本机辅助抓取结果");
    if (!raw) {
      youtubeImportNotice = { state: "error", message: "请先粘贴抓取结果 JSON。" };
      render();
      return;
    }

    try {
      if (raw.length > 2_000_000) {
        throw new Error("JSON 过大，请控制在 2 MB 以内。");
      }
      const parsed = JSON.parse(raw);
      const payload = Array.isArray(parsed) ? { entries: parsed } : parsed;
      if (!payload || typeof payload !== "object" || !Array.isArray(payload.entries)) {
        throw new Error("JSON 需要是视频数组，或包含 entries 数组的对象。");
      }
      if (payload.entries.length > 500) {
        throw new Error("一次最多载入 500 个视频，请分批导入。");
      }
      youtubeImportBatch = createYouTubeImportBatch({
        ...payload,
        batchId: `youtube-json-${Date.now()}`,
        sourceType: "local-assisted-json",
        label: payload.label || "本机辅助抓取结果",
        accountHandle: payload.accountHandle || payload.account || "",
        availableRecordCount: payload.availableRecordCount ?? payload.availableCount,
      });
      if (!youtubeImportBatch.entries.length) {
        throw new Error("JSON 中没有有效的 YouTube videoId。");
      }
      youtubeImportSelected = new Set(youtubeImportBatch.entries.map((entry) => entry.youtubeVideoId));
      youtubeImportNotice = {
        state: "success",
        message: `已载入 ${youtubeImportBatch.entries.length} 个去重视频，请确认后导入。`,
      };
      render();
    } catch (error) {
      youtubeImportNotice = {
        state: "error",
        message: error instanceof Error ? error.message : "无法解析这段 JSON。",
      };
      render();
    }
  }

  function clearYouTubeImportPreview(sourceType, label) {
    youtubeImportBatch = createYouTubeImportBatch({
      ...emptyYouTubeImportBatch,
      sourceType,
      label,
    });
    youtubeImportSelected = new Set();
  }

  async function importSelectedYouTubeVideos() {
    if (youtubeImportLoading || sharedCommitPending) return;
    const existingIds = collectExistingYouTubeIds(videos);
    const candidates = youtubeImportBatch.entries.filter(
      (entry) => youtubeImportSelected.has(entry.youtubeVideoId) && !existingIds.has(entry.youtubeVideoId),
    );
    const membershipMerge = mergeYouTubePlaylistMemberships(videos, youtubeImportBatch.entries);
    if (!candidates.length && !membershipMerge.updatedCount) {
      youtubeImportNotice = { state: "error", message: "没有选中可新增的视频；已有视频会自动跳过。" };
      render();
      return;
    }

    const importedAt = new Date().toISOString();
    const records = candidates.map((entry) => createVideoFromYouTubeImport(entry, youtubeImportBatch, importedAt));
    const nextVideos = [...records, ...membershipMerge.videos];
    const result = await commitVideoCollection(nextVideos);
    if (!result.ok) {
      youtubeImportNotice = {
        state: "error",
        message: workspaceNotice?.message || "共享存储写入失败，收藏数据未改变。",
      };
      render();
      return;
    }
    if (youtubeImportBatch.sourceType === "local-assisted-json") youtubeImportJson = "";
    selectedId = records[0]?.id ?? selectedId;
    youtubeImportNotice = {
      state: "success",
      message: `已导入 ${records.length} 个视频${membershipMerge.updatedCount ? `，并补充 ${membershipMerge.updatedCount} 个已有视频的播放列表归属` : ""}；其余重复项已跳过。`,
    };
    render();
  }

  async function createVideo(form) {
    if (isSaving) return;
    isSaving = true;
    const saveModalVersion = modalVersion;
    setFormBusy(form, true);

    let data = new FormData(form);
    const rawUrl = String(data.get("url") ?? "").trim();
    if (!rawUrl) {
      isSaving = false;
      setFormBusy(form, false);
      return;
    }

    const normalizedUrl = normalizeUrl(rawUrl);
    const platformInfo = detectPlatform(normalizedUrl);
    const userProvidedTitle = Boolean(String(data.get("title") ?? "").trim());
    const needsMetadata =
      !String(data.get("title") ?? "").trim() ||
      !String(data.get("author") ?? "").trim() ||
      !String(data.get("thumbnailUrl") ?? "").trim();
    const metadata = needsMetadata ? await fetchMetadataForForm(form, { silent: true }) : null;

    if (!modalOpen || saveModalVersion !== modalVersion) {
      isSaving = false;
      return;
    }

    data = new FormData(form);
    const savedUrl = normalizeUrl(String(data.get("url") ?? normalizedUrl).trim());
    const savedPlatformInfo = detectPlatform(savedUrl);
    const title = String(data.get("title") ?? "").trim() || createTitleFromUrl(savedUrl, savedPlatformInfo.label);
    const status = String(data.get("status") ?? "planned");
    const thumbnailUrl = String(data.get("thumbnailUrl") ?? "").trim();
    const author = String(data.get("author") ?? "").trim();

    const video = {
      id: `video-${Date.now()}-${Math.random().toString(16).slice(2)}`,
      title,
      url: savedUrl,
      platform: savedPlatformInfo.id,
      platformLabel: savedPlatformInfo.label,
      status,
      tags: splitList(String(data.get("tags") ?? "")),
      description: String(data.get("description") ?? "").trim(),
      thumbnailUrl: thumbnailUrl ? normalizeUrl(thumbnailUrl) : createThumbnailUrl(savedUrl, savedPlatformInfo.id),
      author,
      authorUrl: String(data.get("authorUrl") ?? "").trim(),
      addedAt: new Date().toISOString(),
      accent: platformAccents[savedPlatformInfo.id] ?? platformAccents.other,
      titleSource: userProvidedTitle ? "user" : metadata?.title ? "metadata" : "generated",
      metadataSource: metadata?.source ?? "",
      metadataFetchedAt: metadata?.fetchedAt ? new Date(metadata.fetchedAt * 1000).toISOString() : "",
    };

    const result = await commitVideoCollection([video, ...videos]);
    if (!result.ok) {
      isSaving = false;
      setFormBusy(form, false);
      setMetadataStatus(form, workspaceNotice?.message || "共享存储写入失败，请稍后重试", "error");
      return;
    }
    selectedId = video.id;
    modalOpen = false;
    modalVersion += 1;
    isSaving = false;
    render();
  }

  async function fetchMetadataForForm(form, options = {}) {
    const urlInput = form.elements.url;
    const rawUrl = String(urlInput?.value ?? "").trim();
    if (!rawUrl) {
      setMetadataStatus(form, "先粘贴视频链接", "error");
      return null;
    }

    const normalizedUrl = normalizeUrl(rawUrl);
    setMetadataStatus(form, "正在抓取公开信息...", "loading");

    try {
      const response = await fetch(`/api/video-meta?url=${encodeURIComponent(normalizedUrl)}`, { cache: "no-store" });
      const metadata = await response.json();
      if (!metadata.ok) {
        if (!options.silent) setMetadataStatus(form, "没有抓到公开信息，可手动填写后保存", "error");
        return metadata;
      }

      applyMetadataToForm(form, metadata, options);
      setMetadataStatus(form, `已抓取：${metadata.source || metadata.platform || "metadata"}`, "success");
      return metadata;
    } catch {
      if (!options.silent) setMetadataStatus(form, "抓取失败，请检查本地服务或网络", "error");
      return null;
    }
  }

  async function deleteSelectedVideo() {
    const selected = selectedVideo();
    if (!selected || sharedCommitPending) return;
    const nextVideos = videos.filter((video) => video.id !== selected.id);
    const result = await commitVideoCollection(nextVideos);
    if (!result.ok) {
      render();
      return;
    }
    selectedId = videos[0]?.id ?? "";
    render();
  }

  function render() {
    const visible = filteredVideos();
    const selected = selectedVideo();

    root.innerHTML = `
      <header class="topbar">
        <div>
          <p class="eyebrow">Video Desk</p>
          <h1>视频工作台</h1>
        </div>
        <div class="toolbar">
          <button class="secondaryButton" id="import-youtube" type="button"><span>YT</span><span>批量导入</span></button>
          <button class="primaryButton" id="new-video" type="button"><span>+</span><span>新视频</span></button>
        </div>
      </header>

      <div class="paperStatusBar">
        <span>收集 B站、YouTube、抖音等想看的视频链接</span>
        <span>${videos.length} 个视频 · 当前 ${visible.length} 个匹配 · ${sharedStorageReady ? "本机共享已连接" : "本地缓存"}</span>
      </div>

      ${workspaceNotice ? `<div class="metadataStatus videoWorkspaceNotice" data-state="${escapeAttr(workspaceNotice.state || "")}">${escapeHtml(workspaceNotice.message)}</div>` : ""}

      <div class="paperLayout">
        <div class="paperListPane">
          <div class="searchBox">
            <span>Q</span>
            <input id="video-search" placeholder="搜索标题、平台、标签或链接" value="${escapeAttr(query)}" />
          </div>

          <div class="segmented">
            ${Object.entries(platformLabels)
              .map(
                ([key, label]) =>
                  `<button class="${platform === key ? "selected" : ""}" data-platform="${key}" type="button">${label}</button>`,
              )
              .join("")}
          </div>

          <div class="paperGrid">
            ${
              visible.length
                ? visible.map((video) => renderCard(video, selected?.id === video.id)).join("")
                : `<div class="emptyState">暂无匹配视频</div>`
            }
          </div>
        </div>

        <aside class="detailPane">
          ${selected ? renderDetail(selected) : renderEmptyDetail()}
        </aside>
      </div>

      ${modalOpen ? renderModal() : ""}
      ${
        youtubeImportOpen
          ? renderYouTubeImportModal({
              batch: youtubeImportBatch,
              selectedIds: youtubeImportSelected,
              existingVideos: videos,
              playlistUrl: youtubeImportUrl,
              importJson: youtubeImportJson,
              loading: youtubeImportLoading,
              notice: youtubeImportNotice,
            })
          : ""
      }
    `;

    bindEvents();
  }

  function bindEvents() {
    root.querySelector("#video-search")?.addEventListener("input", (event) => {
      query = event.target.value;
      render();
    });

    root.querySelectorAll("[data-platform]").forEach((button) => {
      button.addEventListener("click", () => {
        platform = button.dataset.platform;
        render();
      });
    });

    root.querySelectorAll("[data-video-id]").forEach((button) => {
      button.addEventListener("click", () => {
        const video = videos.find((item) => item.id === button.dataset.videoId);
        if (video) selectAndOpenVideo(video);
      });
    });

    root.querySelector("#new-video")?.addEventListener("click", () => {
      modalOpen = true;
      modalVersion += 1;
      render();
    });

    root.querySelector("#import-youtube")?.addEventListener("click", () => {
      youtubeImportOpen = true;
      render();
    });

    root.querySelector("#close-youtube-import")?.addEventListener("click", () => {
      if (youtubeImportLoading) return;
      youtubeImportOpen = false;
      render();
    });

    root.querySelector("#youtube-playlist-form")?.addEventListener("submit", (event) => {
      event.preventDefault();
      loadYouTubePlaylist(event.currentTarget);
    });

    root.querySelector("#youtube-import-json-form")?.addEventListener("submit", (event) => {
      event.preventDefault();
      loadYouTubeImportJson(event.currentTarget);
    });

    root.querySelectorAll("[data-youtube-import-id]").forEach((checkbox) => {
      checkbox.addEventListener("change", () => {
        const videoId = checkbox.dataset.youtubeImportId;
        if (!videoId) return;
        if (checkbox.checked) youtubeImportSelected.add(videoId);
        else youtubeImportSelected.delete(videoId);
        updateYouTubeImportSelectionUi(root, youtubeImportBatch, youtubeImportSelected, videos);
      });
    });

    root.querySelector("#toggle-youtube-import")?.addEventListener("click", () => {
      const existingIds = collectExistingYouTubeIds(videos);
      const availableIds = youtubeImportBatch.entries
        .map((entry) => entry.youtubeVideoId)
        .filter((videoId) => !existingIds.has(videoId));
      const allSelected = availableIds.length > 0 && availableIds.every((videoId) => youtubeImportSelected.has(videoId));
      if (allSelected) availableIds.forEach((videoId) => youtubeImportSelected.delete(videoId));
      else availableIds.forEach((videoId) => youtubeImportSelected.add(videoId));
      render();
    });

    root.querySelector("#confirm-youtube-import")?.addEventListener("click", () => {
      importSelectedYouTubeVideos();
    });

    root.querySelector("#close-modal")?.addEventListener("click", () => {
      modalOpen = false;
      modalVersion += 1;
      isSaving = false;
      render();
    });

    root.querySelector("#cancel-modal")?.addEventListener("click", () => {
      modalOpen = false;
      modalVersion += 1;
      isSaving = false;
      render();
    });

    root.querySelector("#video-form")?.addEventListener("submit", (event) => {
      event.preventDefault();
      createVideo(event.currentTarget);
    });

    root.querySelector("#fetch-video-meta")?.addEventListener("click", (event) => {
      fetchMetadataForForm(event.currentTarget.form, { overwrite: true });
    });

    root.querySelector("#open-video")?.addEventListener("click", () => {
      openVideo(selectedVideo());
    });

    root.querySelector("#delete-video")?.addEventListener("click", () => {
      deleteSelectedVideo();
    });
  }

  render();
  syncSharedVideoStorage().finally(() => hydrateMissingVideoMetadata());
}

function renderCard(video, selected) {
  return `
    <button class="paperCard visual videoCard ${selected ? "selected" : ""}" data-video-id="${escapeAttr(video.id)}" style="--accent: ${escapeAttr(video.accent)}" type="button">
      <div class="pdfThumb videoThumb">
        ${renderVideoVisual(video)}
      </div>
      <div class="cardHeader">
        <span>${escapeHtml(video.platformLabel)}</span>
        <strong>${escapeHtml(statusLabels[video.status] ?? video.status)}</strong>
      </div>
      <h2>${escapeHtml(video.title)}</h2>
      ${video.author ? `<p class="videoAuthorLine">${escapeHtml(video.author)}</p>` : ""}
      <p class="videoUrlLine" title="${escapeAttr(video.url)}">${escapeHtml(formatVideoUrl(video.url))}</p>
      <div class="tagRow">
        ${(video.tags ?? []).slice(0, 3).map((tag) => `<span>${escapeHtml(tag)}</span>`).join("")}
      </div>
    </button>
  `;
}

function renderDetail(video) {
  return `
    <div class="detailHero" style="--accent: ${escapeAttr(video.accent)}">
      <span>${escapeHtml(video.platformLabel)}</span>
      <h2>${escapeHtml(video.title)}</h2>
      ${video.author ? `<p class="videoAuthorLine">${escapeHtml(video.author)}</p>` : ""}
      <p class="videoUrlLine" title="${escapeAttr(video.url)}">${escapeHtml(formatVideoUrl(video.url))}</p>
    </div>

    <div class="pdfPreviewLarge videoPreviewLarge">
      ${renderVideoVisual(video)}
    </div>

    <div class="detailActions">
      <button class="primaryButton" id="open-video" type="button"><span>↗</span><span>打开视频</span></button>
      <button class="secondaryButton" id="delete-video" type="button"><span>×</span><span>移除</span></button>
    </div>

    <section class="detailSection">
      <h3>状态</h3>
      <p>${escapeHtml(statusLabels[video.status] ?? video.status)}</p>
    </section>

    <section class="detailSection">
      <h3>作者 / Uploader</h3>
      <p>${video.authorUrl ? `<a href="${escapeAttr(video.authorUrl)}" target="_blank" rel="noreferrer">${escapeHtml(video.author)}</a>` : escapeHtml(video.author || "暂无作者信息")}</p>
    </section>

    <section class="detailSection">
      <h3>标签</h3>
      <p>${(video.tags ?? []).length ? video.tags.map((tag) => escapeHtml(tag)).join(" / ") : "暂无标签"}</p>
    </section>

    ${
      video.duration || video.sourcePlaylists?.length
        ? `<section class="detailSection"><h3>YouTube 收藏信息</h3><p>${escapeHtml(
            [video.duration ? `时长 ${video.duration}` : "", video.sourcePlaylists?.length ? video.sourcePlaylists.join(" / ") : ""]
              .filter(Boolean)
              .join(" · "),
          )}</p></section>`
        : ""
    }

    <section class="detailSection">
      <h3>备注</h3>
      <p>${escapeHtml(video.description || "暂无备注")}</p>
    </section>

    ${
      video.metadataSource
        ? `<section class="detailSection"><h3>Metadata</h3><p>${escapeHtml(video.metadataSource)}</p></section>`
        : ""
    }
  `;
}

function renderEmptyDetail() {
  return `
    <div class="emptyState">添加一个视频链接后，这里会显示详情</div>
  `;
}

function renderModal() {
  return `
    <div class="modalBackdrop" role="presentation">
      <form class="paperModal" id="video-form">
        <div class="modalHeader">
          <h2>新视频</h2>
          <button id="close-modal" type="button">关闭</button>
        </div>

        <div class="formGrid">
          <label class="wide">视频链接<input name="url" required placeholder="https://www.bilibili.com/video/... 或 https://youtu.be/..." /></label>
          <div class="metadataFetchRow wide">
            <button class="secondaryButton" id="fetch-video-meta" type="button">抓取信息</button>
            <span class="metadataStatus" data-metadata-status></span>
          </div>
          <label>标题<input name="title" placeholder="留空则自动生成" /></label>
          <label>作者 / Uploader<input name="author" placeholder="抓取后自动填入，可手动修改" /></label>
          <input name="authorUrl" type="hidden" />
          <label>
            状态
            <select name="status">
              <option value="planned">想看</option>
              <option value="watching">观看中</option>
              <option value="saved">已收藏</option>
              <option value="finished">看完</option>
            </select>
          </label>
          <label class="wide">封面 URL<input name="thumbnailUrl" placeholder="可选：B站 / 抖音可手动粘贴封面图片链接" /></label>
          <label class="wide">标签<input name="tags" placeholder="AI, 产品, 灵感" /></label>
          <label class="wide">备注<textarea name="description" placeholder="为什么想看、看到哪里、要提炼什么"></textarea></label>
        </div>

        <div class="modalFooter">
          <button class="secondaryButton" id="cancel-modal" type="button">取消</button>
          <button class="primaryButton" type="submit">保存</button>
        </div>
      </form>
    </div>
  `;
}

function renderYouTubeImportModal({ batch, selectedIds, existingVideos, playlistUrl, importJson, loading, notice }) {
  const existingIds = collectExistingYouTubeIds(existingVideos);
  const stats = youtubeImportStats(batch, existingIds, selectedIds);
  const updateCount = countYouTubePlaylistMembershipUpdates(existingVideos, batch.entries);
  const sourceDescription =
    batch.sourceType === "local-assisted-json"
      ? `本机辅助抓取结果仅在当前页面解析；确认后只把视频元数据写入 localStorage，不保存 Cookie 或令牌。`
      : `来自粘贴的公开/不公开播放列表 URL；私人播放列表仍需 OAuth 或本机登录辅助。`;

  return `
    <div class="modalBackdrop" role="presentation">
      <section class="paperModal youtubeImportModal" role="dialog" aria-modal="true" aria-labelledby="youtube-import-title">
        <div class="modalHeader">
          <div>
            <p class="eyebrow">YouTube Import</p>
            <h2 id="youtube-import-title">批量导入视频</h2>
          </div>
          <button id="close-youtube-import" type="button" ${loading ? "disabled" : ""}>关闭</button>
        </div>

        <div class="youtubeImportSources">
          <form class="youtubePlaylistForm" id="youtube-playlist-form">
            <label for="youtube-playlist-url">公开播放列表 URL</label>
            <div>
              <input id="youtube-playlist-url" name="playlistUrl" value="${escapeAttr(playlistUrl)}" placeholder="https://www.youtube.com/playlist?list=..." ${loading ? "disabled" : ""} />
              <button class="secondaryButton" type="submit" ${loading ? "disabled" : ""}>${loading ? "读取中…" : "读取列表"}</button>
            </div>
          </form>
          <form class="youtubeImportJsonForm" id="youtube-import-json-form">
            <details ${importJson ? "open" : ""}>
              <summary>粘贴本机辅助抓取结果 JSON</summary>
              <textarea name="importJson" placeholder='[{"id":"abcdefghijk","title":"...","author":"...","sourcePlaylists":["稍后观看"]}]' ${loading ? "disabled" : ""}>${escapeHtml(importJson)}</textarea>
              <button class="secondaryButton" type="submit" ${loading ? "disabled" : ""}>载入预览</button>
            </details>
          </form>
        </div>

        <div class="youtubeImportPrivacyNote">${escapeHtml(sourceDescription)}</div>
        <div class="metadataStatus youtubeImportNotice" data-state="${escapeAttr(notice?.state || "")}">${escapeHtml(notice?.message || "")}</div>

        <div class="youtubeImportHeading">
          <div>
            <strong>${escapeHtml(batch.playlistTitle || batch.label)}</strong>
            ${batch.accountHandle ? `<span>${escapeHtml(batch.accountHandle)}</span>` : ""}
          </div>
          <button class="secondaryButton" id="toggle-youtube-import" type="button" ${loading || !stats.newCount ? "disabled" : ""}>全选 / 全不选</button>
        </div>

        <div class="youtubeImportStats" aria-label="导入统计">
          <div><span>发现</span><strong>${stats.foundCount}</strong></div>
          <div><span>可新增</span><strong>${stats.newCount}</strong></div>
          <div><span>重复</span><strong>${stats.duplicateCount}</strong></div>
          <div><span>不可用</span><strong>${stats.unavailableCount}</strong></div>
        </div>
        ${batch.truncated ? `<div class="youtubeImportPrivacyNote">列表超过单次上限，本次只预览前 500 条；还有 ${escapeHtml(batch.truncatedCount)} 条未读取，请分批处理。</div>` : ""}

        <div class="youtubeImportList">
          ${
            batch.entries.length
              ? batch.entries
                  .map((entry) => {
                    const isExisting = existingIds.has(entry.youtubeVideoId);
                    const checked = !isExisting && selectedIds.has(entry.youtubeVideoId);
                    return `
                      <label class="youtubeImportItem ${isExisting ? "is-duplicate" : ""}">
                        <input type="checkbox" data-youtube-import-id="${escapeAttr(entry.youtubeVideoId)}" ${checked ? "checked" : ""} ${isExisting || loading ? "disabled" : ""} />
                        <img src="${escapeAttr(entry.thumbnailUrl)}" alt="" loading="lazy" />
                        <span class="youtubeImportItemText">
                          <strong>${escapeHtml(entry.title)}</strong>
                          <span>${escapeHtml([entry.author, entry.duration].filter(Boolean).join(" · "))}</span>
                          <small>${escapeHtml(entry.sourcePlaylists.join(" / ") || "YouTube 播放列表")}</small>
                        </span>
                        <em>${isExisting ? "已存在" : "待导入"}</em>
                      </label>
                    `;
                  })
                  .join("")
              : `<div class="emptyState">这个播放列表没有可导入的视频</div>`
          }
        </div>

        <div class="modalFooter youtubeImportFooter">
          <span id="youtube-import-selected">已选择 ${stats.selectedNewCount} 个新视频${updateCount ? ` · ${updateCount} 个已有视频将补充归属` : ""}</span>
          <button class="primaryButton" id="confirm-youtube-import" type="button" ${loading || (!stats.selectedNewCount && !updateCount) ? "disabled" : ""}>${youtubeImportConfirmLabel(stats.selectedNewCount, updateCount)}</button>
        </div>
      </section>
    </div>
  `;
}

function renderVideoVisual(video) {
  if (video.thumbnailUrl) {
    return `
      <img src="${escapeAttr(thumbnailDisplayUrl(video.thumbnailUrl))}" alt="${escapeAttr(video.title)} 封面" loading="lazy" onerror="handleVideoThumbError(this)" />
      ${renderVideoOverlay(video)}
    `;
  }

  return renderVideoOverlay(video);
}

function thumbnailDisplayUrl(url) {
  if (!url) return "";
  try {
    const parsed = new URL(url);
    const host = parsed.hostname.toLowerCase();
    const needsProxy =
      host.includes("hdslb.com") ||
      host.includes("douyinpic.com") ||
      host.includes("douyinstatic.com");
    return needsProxy ? `/api/video-image?url=${encodeURIComponent(url)}` : url;
  } catch {
    return url;
  }
}

function renderVideoOverlay(video) {
  return `
    <div class="videoOverlay">
      <div class="videoPlayMark">▶</div>
      <div class="videoOverlayMeta">
        <strong>${escapeHtml(video.platformLabel)}</strong>
        <span>${escapeHtml(formatVideoUrl(video.url))}</span>
      </div>
    </div>
  `;
}

function createYouTubeImportBatch(source) {
  const entriesById = new Map();
  let invalidEntryCount = 0;
  const allRawEntries = Array.isArray(source?.entries) ? source.entries : [];
  const rawEntries = allRawEntries.slice(0, 500);

  rawEntries.forEach((rawEntry) => {
    const youtubeVideoId = String(rawEntry?.youtubeVideoId || rawEntry?.id || extractYouTubeId(rawEntry?.url || "")).trim();
    if (!isYouTubeVideoId(youtubeVideoId)) {
      invalidEntryCount += 1;
      return;
    }

    const sourcePlaylists = normalizeStringList(
      rawEntry?.sourcePlaylists ?? rawEntry?.playlists ?? [source?.playlistTitle || source?.label || "YouTube"],
    )
      .slice(0, 50)
      .map((item) => safeImportText(item, 200));
    const existing = entriesById.get(youtubeVideoId);
    if (existing) {
      existing.sourcePlaylists = normalizeStringList([...existing.sourcePlaylists, ...sourcePlaylists]);
      return;
    }

    entriesById.set(youtubeVideoId, {
      youtubeVideoId,
      title: safeImportText(rawEntry?.title || `YouTube 视频 · ${youtubeVideoId}`, 500),
      author: safeImportText(rawEntry?.author || rawEntry?.uploader || rawEntry?.channel || "", 200),
      duration: formatImportDuration(rawEntry?.duration),
      sourcePlaylists,
      url: `https://www.youtube.com/watch?v=${youtubeVideoId}`,
      thumbnailUrl:
        safeImportText(rawEntry?.thumbnailUrl || rawEntry?.thumbnail || "", 2000) ||
        `https://i.ytimg.com/vi/${youtubeVideoId}/hqdefault.jpg`,
    });
  });

  const entries = [...entriesById.values()];
  const availableRecordCount = Math.max(
    entries.length,
    safeCount(source?.availableRecordCount ?? source?.availableCount, rawEntries.length - invalidEntryCount),
  );
  const unavailableCount = safeCount(source?.unavailableCount, 0) + invalidEntryCount;
  return {
    batchId: String(source?.batchId || `youtube-import-${Date.now()}`),
    sourceType: String(source?.sourceType || "youtube-playlist-url"),
    label: safeImportText(source?.label || "YouTube 播放列表", 200),
    accountHandle: safeImportText(source?.accountHandle || "", 200),
    playlistTitle: safeImportText(source?.playlistTitle || "", 500),
    foundCount: Math.max(
      availableRecordCount + unavailableCount,
      safeCount(source?.foundCount, availableRecordCount + unavailableCount),
    ),
    availableRecordCount,
    unavailableCount,
    truncated: Boolean(source?.truncated || allRawEntries.length > rawEntries.length),
    truncatedCount: Math.max(
      allRawEntries.length - rawEntries.length,
      safeCount(source?.truncatedCount, 0),
    ),
    entries,
  };
}

function youtubeImportStats(batch, existingIds, selectedIds) {
  const existingCount = batch.entries.filter((entry) => existingIds.has(entry.youtubeVideoId)).length;
  const batchDuplicateCount = Math.max(0, batch.availableRecordCount - batch.entries.length);
  const newEntries = batch.entries.filter((entry) => !existingIds.has(entry.youtubeVideoId));
  return {
    foundCount: batch.foundCount,
    newCount: newEntries.length,
    duplicateCount: batchDuplicateCount + existingCount,
    unavailableCount: batch.unavailableCount,
    selectedNewCount: newEntries.filter((entry) => selectedIds.has(entry.youtubeVideoId)).length,
  };
}

function updateYouTubeImportSelectionUi(root, batch, selectedIds, existingVideos) {
  const stats = youtubeImportStats(batch, collectExistingYouTubeIds(existingVideos), selectedIds);
  const updateCount = countYouTubePlaylistMembershipUpdates(existingVideos, batch.entries);
  const summary = root.querySelector("#youtube-import-selected");
  const confirm = root.querySelector("#confirm-youtube-import");
  if (summary) {
    summary.textContent = `已选择 ${stats.selectedNewCount} 个新视频${updateCount ? ` · ${updateCount} 个已有视频将补充归属` : ""}`;
  }
  if (confirm) {
    confirm.textContent = youtubeImportConfirmLabel(stats.selectedNewCount, updateCount);
    confirm.disabled = stats.selectedNewCount === 0 && updateCount === 0;
  }
}

function mergeYouTubePlaylistMemberships(videos, entries) {
  const entriesById = new Map(entries.map((entry) => [entry.youtubeVideoId, entry]));
  let updatedCount = 0;
  const nextVideos = videos.map((video) => {
    const videoId = String(video?.youtubeVideoId || extractYouTubeId(video?.url || "")).trim();
    const entry = entriesById.get(videoId);
    if (!entry) return video;
    const currentPlaylists = normalizeStringList(video.sourcePlaylists);
    const sourcePlaylists = normalizeStringList([...currentPlaylists, ...entry.sourcePlaylists]);
    const tags = normalizeStringList([...normalizeStringList(video.tags), ...entry.sourcePlaylists]);
    const hasPlaylistChange = sourcePlaylists.length !== currentPlaylists.length;
    const needsVideoId = video.youtubeVideoId !== videoId;
    if (!hasPlaylistChange && !needsVideoId) return video;
    updatedCount += 1;
    return { ...video, youtubeVideoId: videoId, sourcePlaylists, tags };
  });
  return { videos: nextVideos, updatedCount };
}

function countYouTubePlaylistMembershipUpdates(videos, entries) {
  return mergeYouTubePlaylistMemberships(videos, entries).updatedCount;
}

function youtubeImportConfirmLabel(newCount, updateCount) {
  if (newCount && updateCount) return `导入 ${newCount} 个，更新 ${updateCount} 个`;
  if (newCount) return `导入 ${newCount} 个视频`;
  if (updateCount) return `更新 ${updateCount} 个已有视频`;
  return "请选择视频";
}

function createVideoFromYouTubeImport(entry, batch, importedAt) {
  const sourcePlaylists = normalizeStringList(entry.sourcePlaylists);
  const isAssistedImport = batch.sourceType === "local-assisted-json";
  const sourceLabel = isAssistedImport ? "本机辅助抓取" : "播放列表 URL";
  const descriptionParts = [
    `来自 YouTube ${sourceLabel}`,
    batch.accountHandle ? `账号/频道：${batch.accountHandle}` : "",
    sourcePlaylists.length ? `收藏夹：${sourcePlaylists.join(" / ")}` : "",
    entry.duration ? `时长：${entry.duration}` : "",
  ].filter(Boolean);
  return {
    id: `youtube-${entry.youtubeVideoId}`,
    youtubeVideoId: entry.youtubeVideoId,
    title: entry.title,
    url: `https://www.youtube.com/watch?v=${entry.youtubeVideoId}`,
    platform: "youtube",
    platformLabel: platformLabels.youtube,
    status: "saved",
    tags: normalizeStringList(["YouTube", ...sourcePlaylists]),
    description: descriptionParts.join(" · "),
    thumbnailUrl: entry.thumbnailUrl,
    author: entry.author,
    authorUrl: "",
    duration: entry.duration,
    sourcePlaylists,
    sourceAccount: batch.accountHandle,
    importSource: isAssistedImport ? "local-assisted-json" : "youtube-playlist-url",
    importBatchId: batch.batchId,
    addedAt: importedAt,
    accent: platformAccents.youtube,
    titleSource: "metadata",
    metadataSource: "youtube-playlist-import",
    metadataFetchedAt: importedAt,
  };
}

function collectExistingYouTubeIds(videos) {
  const ids = new Set();
  videos.forEach((video) => {
    const videoId = String(video?.youtubeVideoId || extractYouTubeId(video?.url || "")).trim();
    if (isYouTubeVideoId(videoId)) ids.add(videoId);
  });
  return ids;
}

function isYouTubeVideoId(value) {
  return /^[A-Za-z0-9_-]{11}$/.test(String(value || ""));
}

function normalizeStringList(value) {
  const values = Array.isArray(value) ? value : value ? [value] : [];
  return [...new Set(values.map((item) => String(item || "").trim()).filter(Boolean))];
}

function safeCount(value, fallback = 0) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? Math.floor(parsed) : fallback;
}

function safeImportText(value, maxLength) {
  return String(value ?? "").trim().slice(0, maxLength);
}

function formatImportDuration(value) {
  if (typeof value === "string") return value.trim();
  const seconds = Number(value);
  if (!Number.isFinite(seconds) || seconds < 0) return "";
  const wholeSeconds = Math.floor(seconds);
  const hours = Math.floor(wholeSeconds / 3600);
  const minutes = Math.floor((wholeSeconds % 3600) / 60);
  const remainder = wholeSeconds % 60;
  return hours
    ? `${hours}:${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`
    : `${minutes}:${String(remainder).padStart(2, "0")}`;
}

function youtubePlaylistErrorMessage(error) {
  const messages = {
    invalid_youtube_playlist_url: "只支持 youtube.com/playlist?list=... 播放列表 URL。",
    playlist_not_found: "没有找到这个播放列表，或它不是公开/不公开列表。",
    playlist_private: "这个播放列表需要登录；私人列表后续需接入 OAuth 或本机登录辅助。",
    extractor_unavailable: "本地缺少 yt-dlp，暂时无法读取播放列表。",
    extractor_timeout: "读取播放列表超时，请稍后重试；原有收藏未发生变化。",
  };
  return messages[String(error || "")] || "播放列表读取失败，请检查 URL 和网络；原有收藏未发生变化。";
}

function detectPlatform(url) {
  const host = getHost(url);
  if (host.includes("bilibili.com") || host.includes("b23.tv")) return { id: "bilibili", label: platformLabels.bilibili };
  if (host.includes("youtube.com") || host.includes("youtu.be")) return { id: "youtube", label: platformLabels.youtube };
  if (host.includes("douyin.com")) return { id: "douyin", label: platformLabels.douyin };
  return { id: "other", label: platformLabels.other };
}

function createThumbnailUrl(url, platform) {
  if (platform !== "youtube") return "";
  const videoId = extractYouTubeId(url);
  return videoId ? `https://img.youtube.com/vi/${encodeURIComponent(videoId)}/hqdefault.jpg` : "";
}

function extractYouTubeId(url) {
  try {
    const parsed = new URL(url);
    if (parsed.hostname.includes("youtu.be")) return parsed.pathname.split("/").filter(Boolean)[0] ?? "";
    if (parsed.searchParams.get("v")) return parsed.searchParams.get("v");
    const shortsMatch = parsed.pathname.match(/\/shorts\/([^/?]+)/);
    if (shortsMatch) return shortsMatch[1];
    const embedMatch = parsed.pathname.match(/\/embed\/([^/?]+)/);
    return embedMatch ? embedMatch[1] : "";
  } catch {
    return "";
  }
}

function normalizeUrl(value) {
  const trimmed = value.trim();
  if (/^https?:\/\//i.test(trimmed)) return trimmed;
  return `https://${trimmed}`;
}

function getHost(url) {
  try {
    return new URL(url).hostname.toLowerCase();
  } catch {
    return "";
  }
}

function formatVideoUrl(url) {
  try {
    const parsed = new URL(url);
    const host = parsed.hostname.replace(/^www\./, "");
    const youtubeId = parsed.searchParams.get("v");
    if (youtubeId) return `${host} / ${youtubeId.slice(0, 18)}${youtubeId.length > 18 ? "..." : ""}`;
    const parts = parsed.pathname.split("/").filter(Boolean);
    const lastPart = parts.at(-1) ?? "";
    const label = lastPart.length > 18 ? `${lastPart.slice(0, 18)}...` : lastPart;
    return label ? `${host} / ${label}` : host;
  } catch {
    const value = String(url ?? "");
    return value.length > 42 ? `${value.slice(0, 42)}...` : value;
  }
}

function createTitleFromUrl(url, platformLabel) {
  try {
    const parsed = new URL(url);
    const parts = parsed.pathname.split("/").filter(Boolean);
    const lastPart = decodeURIComponent(parts.at(-1) ?? parsed.hostname);
    return `${platformLabel}视频 · ${lastPart || parsed.hostname}`;
  } catch {
    return `${platformLabel}视频`;
  }
}

function applyMetadataToForm(form, metadata, options = {}) {
  const overwrite = Boolean(options.overwrite);
  const titleInput = form.elements.title;
  const authorInput = form.elements.author;
  const authorUrlInput = form.elements.authorUrl;
  const thumbnailInput = form.elements.thumbnailUrl;
  const urlInput = form.elements.url;

  if (metadata.url && urlInput && (overwrite || !urlInput.value.trim())) {
    urlInput.value = metadata.url;
  }

  if (metadata.title && titleInput && (overwrite || !titleInput.value.trim())) {
    titleInput.value = metadata.title;
  }

  const author = metadata.author || metadata.uploader || "";
  if (author && authorInput && (overwrite || !authorInput.value.trim())) {
    authorInput.value = author;
  }

  if (metadata.authorUrl && authorUrlInput && (overwrite || !authorUrlInput.value.trim())) {
    authorUrlInput.value = metadata.authorUrl;
  }

  const thumbnailUrl = metadata.thumbnailUrl || metadata.image || "";
  if (thumbnailUrl && thumbnailInput && (overwrite || !thumbnailInput.value.trim())) {
    thumbnailInput.value = thumbnailUrl;
  }
}

function setMetadataStatus(form, message, state = "") {
  const status = form?.querySelector("[data-metadata-status]");
  if (!status) return;
  status.textContent = message;
  status.dataset.state = state;
}

function setFormBusy(form, busy) {
  if (!form) return;
  form.dataset.busy = busy ? "true" : "false";
  form.querySelectorAll('button[type="submit"], #fetch-video-meta').forEach((button) => {
    button.disabled = busy;
  });
}

function loadVideos() {
  const raw = localStorage.getItem(STORAGE_KEY);
  if (!raw) return [];

  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed)
      ? parsed.map(normalizeVideoRecord).filter((video) => video && !isLegacyDemoVideo(video))
      : [];
  } catch {
    return [];
  }
}

function normalizeVideoCollection(value) {
  return Array.isArray(value)
    ? value.map(normalizeVideoRecord).filter((video) => video && !isLegacyDemoVideo(video))
    : [];
}

function isLegacyDemoVideo(video) {
  return LEGACY_DEMO_IDS.has(String(video?.id || ""));
}

function loadSharedStorageMigrated() {
  try {
    return localStorage.getItem(SHARED_STORAGE_MIGRATED_KEY) === "1";
  } catch {
    return false;
  }
}

function saveSharedStorageMigrated() {
  localStorage.setItem(SHARED_STORAGE_MIGRATED_KEY, "1");
}

async function fetchSharedVideoStorage() {
  const response = await fetch(SHARED_STORAGE_ENDPOINT, { cache: "no-store" });
  const payload = await response.json();
  if (!response.ok || !payload.ok || !Array.isArray(payload.videos)) {
    throw new Error(payload.error || "shared_storage_read_failed");
  }
  return {
    videos: payload.videos,
    revision: Number.isInteger(payload.revision) && payload.revision >= 0 ? payload.revision : 0,
    initialized: Boolean(payload.initialized),
  };
}

async function writeSharedVideoStorage(videos, baseRevision) {
  const response = await fetch(SHARED_STORAGE_ENDPOINT, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ videos, baseRevision }),
  });
  const payload = await response.json();
  if (response.status === 409 && payload.state) {
    const error = new Error("revision_conflict");
    error.code = "revision_conflict";
    error.state = payload.state;
    throw error;
  }
  if (!response.ok || !payload.ok || !Array.isArray(payload.videos)) {
    throw new Error(payload.error || "shared_storage_write_failed");
  }
  return {
    videos: payload.videos,
    revision: Number.isInteger(payload.revision) && payload.revision >= 0 ? payload.revision : baseRevision + 1,
    initialized: true,
  };
}

function mergeVideoCollections(localVideos, remoteVideos) {
  const mergedByKey = new Map();
  normalizeVideoCollection(remoteVideos).forEach((video) => {
    mergedByKey.set(canonicalVideoKey(video), video);
  });
  normalizeVideoCollection(localVideos).forEach((localVideo) => {
    const key = canonicalVideoKey(localVideo);
    const remoteVideo = mergedByKey.get(key);
    mergedByKey.set(key, remoteVideo ? mergeVideoRecords(localVideo, remoteVideo) : localVideo);
  });
  return [...mergedByKey.values()];
}

function mergeVideoRecords(primary, fallback) {
  const merged = { ...fallback, ...primary };
  Object.entries(fallback).forEach(([key, value]) => {
    if (!(key in primary) || primary[key] === null || primary[key] === "") merged[key] = value;
  });
  merged.tags = normalizeStringList([...(primary.tags ?? []), ...(fallback.tags ?? [])]);
  merged.sourcePlaylists = normalizeStringList([
    ...normalizeStringList(primary.sourcePlaylists),
    ...normalizeStringList(fallback.sourcePlaylists),
  ]);
  return normalizeVideoRecord(merged);
}

function canonicalVideoKey(video) {
  const youtubeVideoId = String(video?.youtubeVideoId || extractYouTubeId(video?.url || "")).trim();
  if (isYouTubeVideoId(youtubeVideoId)) return `youtube:${youtubeVideoId}`;
  const normalizedUrl = normalizeCanonicalVideoUrl(video?.url || "");
  if (normalizedUrl) return `url:${normalizedUrl}`;
  return `id:${String(video?.id || "")}`;
}

function normalizeCanonicalVideoUrl(value) {
  try {
    const parsed = new URL(String(value || "").trim());
    if (!/^https?:$/.test(parsed.protocol)) return String(value || "").trim();
    parsed.hash = "";
    parsed.hostname = parsed.hostname.toLowerCase();
    if ((parsed.protocol === "https:" && parsed.port === "443") || (parsed.protocol === "http:" && parsed.port === "80")) {
      parsed.port = "";
    }
    parsed.pathname = parsed.pathname.replace(/\/{2,}/g, "/");
    if (parsed.pathname !== "/") parsed.pathname = parsed.pathname.replace(/\/+$/, "");
    const query = [...parsed.searchParams.entries()]
      .filter(([key]) => !key.toLowerCase().startsWith("utm_") && !["fbclid", "gclid"].includes(key.toLowerCase()))
      .sort(([leftKey, leftValue], [rightKey, rightValue]) =>
        leftKey === rightKey ? leftValue.localeCompare(rightValue) : leftKey.localeCompare(rightKey),
      );
    parsed.search = "";
    query.forEach(([key, item]) => parsed.searchParams.append(key, item));
    return parsed.toString();
  } catch {
    return String(value || "").trim();
  }
}

function normalizeVideoRecord(video) {
  if (!video?.url) return null;
  const platformInfo = detectPlatform(video.url);
  const thumbnailUrl = video.thumbnailUrl || createThumbnailUrl(video.url, video.platform ?? platformInfo.id);
  const youtubeVideoId = video.youtubeVideoId || extractYouTubeId(video.url);
  return {
    ...video,
    platform: video.platform ?? platformInfo.id,
    platformLabel: video.platformLabel ?? platformInfo.label,
    tags: Array.isArray(video.tags) ? video.tags : [],
    thumbnailUrl,
    author: video.author ?? video.uploader ?? "",
    authorUrl: video.authorUrl ?? "",
    accent: video.accent ?? platformAccents[platformInfo.id] ?? platformAccents.other,
    youtubeVideoId: isYouTubeVideoId(youtubeVideoId) ? youtubeVideoId : video.youtubeVideoId ?? "",
    sourcePlaylists: normalizeStringList(video.sourcePlaylists ?? video.playlistNames),
  };
}

window.handleVideoThumbError = function handleVideoThumbError(image) {
  image.classList.add("is-hidden");
  image.parentElement?.classList.add("has-failed-image");
};

function saveVideos(videos) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(videos));
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
