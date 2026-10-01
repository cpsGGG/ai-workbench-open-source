const STORAGE_KEY = "ai-workbench:tweets:v1";
const DEFAULT_BOOKMARKS_URL = "https://x.com/i/bookmarks";
const tweetWorkspaceCleanups = new WeakMap();

let legacyTweets = [];

const typeLabels = {
  all: "全部",
  text: "文字",
  image: "图片",
  video: "视频",
};

export function createTweetWorkspace(root) {
  tweetWorkspaceCleanups.get(root)?.();

  let tweets = loadTweets();
  let selectedId = tweets[0]?.id || "";
  let query = "";
  let type = "all";
  let modalOpen = false;
  let modalVersion = 0;
  let isSaving = false;
  let metadataRequest = null;
  let metadataContextUrl = "";
  let metadataAttemptedUrl = "";
  let currentMetadata = null;
  let currentMetadataUrl = "";
  let autoFetchTimer = 0;
  let appliedMetadataValues = {};
  let masonryResizeObserver = null;
  let masonryFrame = 0;
  let masonryResizeHandler = null;
  let masonryImageListeners = [];
  let autoCollectVisibilityObserver = null;
  let autoCollectRequest = null;
  let autoCollectRequestVersion = 0;
  let clipboardReadInProgress = false;
  let lastHandledClipboardStatusId = "";
  let autoCollectStatus = "监听中";
  let autoCollectState = "idle";
  let batchModalOpen = false;
  let batchImportVersion = 0;
  let batchPollTimer = 0;
  let batchRequestController = null;
  let batchJobId = "";
  let batchInputUrl = DEFAULT_BOOKMARKS_URL;
  let batchMaxItems = "";
  let batchImportState = createInitialBatchImportState();
  const workspaceLifecycle = new AbortController();

  function filteredTweets() {
    const normalized = query.trim().toLowerCase();
    return tweets.filter((tweet) => {
      const content = [
        tweet.authorName,
        tweet.handle,
        tweet.body,
        tweet.url,
        tweet.note,
        ...(tweet.tags || []),
      ]
        .join(" ")
        .toLowerCase();
      return (type === "all" || tweet.mediaType === type) && (!normalized || content.includes(normalized));
    });
  }

  function selectedTweet() {
    const visible = filteredTweets();
    return visible.find((tweet) => tweet.id === selectedId) || visible[0] || null;
  }

  function cleanupTweetMasonry() {
    if (masonryFrame) {
      window.cancelAnimationFrame(masonryFrame);
      masonryFrame = 0;
    }
    masonryResizeObserver?.disconnect();
    masonryResizeObserver = null;
    if (masonryResizeHandler) {
      window.removeEventListener("resize", masonryResizeHandler);
      masonryResizeHandler = null;
    }
    masonryImageListeners.forEach(({ image, eventName, handler }) => {
      image.removeEventListener(eventName, handler);
    });
    masonryImageListeners = [];
  }

  function setupTweetMasonry() {
    const grid = root.querySelector(".tweetGrid");
    if (!grid) return;

    const cards = Array.from(grid.querySelectorAll(".tweetCard"));
    if (!cards.length) return;
    const scheduleLayout = () => {
      if (!grid.isConnected || !root.contains(grid) || masonryFrame) return;
      masonryFrame = window.requestAnimationFrame(() => {
        masonryFrame = 0;
        if (!grid.isConnected || !root.contains(grid) || grid.clientWidth === 0) return;

        grid.classList.add("tweetMasonryReady");
        const styles = window.getComputedStyle(grid);
        const rowHeight = Number.parseFloat(styles.gridAutoRows) || 4;
        const rowGap = Number.parseFloat(styles.rowGap) || 0;
        cards.forEach((card) => {
          const span = Math.max(
            1,
            Math.ceil((card.getBoundingClientRect().height + rowGap) / (rowHeight + rowGap)),
          );
          const nextValue = "span " + span;
          if (card.style.gridRowEnd !== nextValue) card.style.gridRowEnd = nextValue;
        });
      });
    };

    if (typeof ResizeObserver === "function") {
      masonryResizeObserver = new ResizeObserver(scheduleLayout);
      masonryResizeObserver.observe(grid);
      cards.forEach((card) => masonryResizeObserver.observe(card));
    } else {
      masonryResizeHandler = scheduleLayout;
      window.addEventListener("resize", masonryResizeHandler);
    }

    grid.querySelectorAll("img").forEach((image) => {
      ["load", "error"].forEach((eventName) => {
        const handler = scheduleLayout;
        image.addEventListener(eventName, handler, { once: true });
        masonryImageListeners.push({ image, eventName, handler });
      });
    });

    queueMicrotask(scheduleLayout);
    document.fonts?.ready.then(scheduleLayout).catch(() => {});
  }

  function setAutoCollectStatus(message, state = "idle") {
    autoCollectStatus = message;
    autoCollectState = state;
    const status = root.querySelector("[data-tweet-auto-status]");
    if (!status) return;
    status.textContent = "自动收藏 · " + message;
    status.dataset.state = state;
  }

  function findTweetByStatusId(statusId) {
    return tweets.find((tweet) => getTweetStatusId(tweet.url) === statusId) || null;
  }

  function locateTweetByStatusId(statusId, options = {}) {
    const existing = findTweetByStatusId(statusId);
    if (!existing) return false;

    if (options.closeModal && modalOpen) {
      modalOpen = false;
      modalVersion += 1;
      cancelMetadataWork();
      isSaving = false;
    }
    query = "";
    type = "all";
    selectedId = existing.id;
    lastHandledClipboardStatusId = statusId;
    setAutoCollectStatus("已存在", "existing");
    render();
    window.requestAnimationFrame(() => {
      window.requestAnimationFrame(() => {
        const card = Array.from(root.querySelectorAll(".tweetCard")).find(
          (item) => item.dataset.tweetId === existing.id,
        );
        card?.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "nearest" });
      });
    });
    return true;
  }

  function createTweetFromMetadata(identity, metadata) {
    if (!metadata || metadata.ok === false) return null;
    const body = String(metadata.body || "");
    const mediaUrls = normalizeMediaUrls(metadata.mediaUrls, metadata.mediaUrl);
    const videoUrl = String(metadata.videoUrl || "").trim();
    if (!body.trim() && !mediaUrls.length && !videoUrl) return null;

    const declaredType = String(metadata.mediaType || "").trim();
    let mediaType = declaredType
      ? normalizeMediaType(declaredType)
      : videoUrl
        ? "video"
        : mediaUrls.length
          ? "image"
          : "text";
    if (!body.trim() && mediaType === "text") {
      mediaType = videoUrl ? "video" : mediaUrls.length ? "image" : "text";
    }
    const handle = normalizeHandle(String(metadata.handle || identity.handle || "").trim());
    return normalizeTweet({
      id: "tweet-" + Date.now() + "-" + Math.random().toString(16).slice(2),
      url: identity.canonicalUrl,
      authorName:
        String(metadata.authorName || "").trim() ||
        String(handle || "").replace(/^@/, "") ||
        "未命名作者",
      handle,
      body,
      mediaType,
      mediaUrl: String(metadata.mediaUrl || mediaUrls[0] || "").trim(),
      mediaUrls,
      videoUrl,
      avatarUrl: String(metadata.avatarUrl || "").trim(),
      tags: [],
      note: "",
      addedAt: new Date().toISOString(),
      accent: accentFor(mediaType),
      metadataSource: String(metadata.source || "").trim(),
      metadataFetchedAt: normalizeFetchedAt(metadata.fetchedAt),
      metadataUrl: String(metadata.url || "").trim(),
    });
  }

  function createTweetLinkPlaceholder(identity, metadata = {}) {
    const handle = normalizeHandle(String(metadata.handle || identity.handle || "").trim());
    return normalizeTweet({
      id: "tweet-" + Date.now() + "-" + Math.random().toString(16).slice(2),
      url: identity.canonicalUrl,
      authorName:
        String(metadata.authorName || "").trim() ||
        String(handle || "").replace(/^@/, "") ||
        "待补全作者",
      handle,
      body: "这条收藏暂未从 X 页面读取到正文或媒体，已为你保留原推链接。",
      mediaType: "text",
      mediaUrl: "",
      mediaUrls: [],
      videoUrl: "",
      avatarUrl: String(metadata.avatarUrl || "").trim(),
      tags: ["待补全"],
      note: "同步过程仅在本机读取；收藏链接未发送给第三方补全服务。",
      addedAt: new Date().toISOString(),
      accent: accentFor("text"),
      metadataSource: "x-bookmarks-local-placeholder",
      metadataFetchedAt: normalizeFetchedAt(metadata.fetchedAt),
      metadataUrl: "",
      importIncomplete: true,
    });
  }

  function cancelAutoCollectRequest(options = {}) {
    const request = autoCollectRequest;
    if (!request) return;

    autoCollectRequest = null;
    autoCollectRequestVersion += 1;
    request.controller.abort();
    if (options.allowRetry && request.statusId === lastHandledClipboardStatusId) {
      lastHandledClipboardStatusId = "";
    }
    if (options.allowRetry) setAutoCollectStatus("监听中", "idle");
  }

  async function collectTweetAutomatically(identity) {
    if (locateTweetByStatusId(identity.statusId)) return;
    if (autoCollectRequest?.statusId === identity.statusId) return autoCollectRequest.promise;

    autoCollectRequest?.controller.abort();
    const controller = new AbortController();
    const requestVersion = ++autoCollectRequestVersion;
    const request = {
      statusId: identity.statusId,
      controller,
      promise: null,
    };
    autoCollectRequest = request;
    setAutoCollectStatus("读取中", "loading");

    request.promise = (async () => {
      try {
        const response = await fetch(
          "/api/tweet-meta?url=" + encodeURIComponent(identity.canonicalUrl),
          {
            cache: "no-store",
            signal: controller.signal,
          },
        );
        if (!response.ok) throw new Error("tweet-meta:" + response.status);
        const metadata = await response.json();
        if (
          controller.signal.aborted ||
          requestVersion !== autoCollectRequestVersion ||
          autoCollectRequest !== request
        ) {
          return;
        }

        if (locateTweetByStatusId(identity.statusId)) return;
        const tweet = createTweetFromMetadata(identity, metadata);
        if (!tweet) throw new Error("tweet-meta:missing-content");

        tweets = [tweet, ...tweets];
        selectedId = tweet.id;
        query = "";
        type = "all";
        saveTweets(tweets);
        setAutoCollectStatus("已保存", "success");
        render();
        window.requestAnimationFrame(() => {
          root.querySelector('[data-tweet-id="' + tweet.id + '"]')?.scrollIntoView({
            behavior: "smooth",
            block: "nearest",
            inline: "nearest",
          });
        });
      } catch (error) {
        if (
          controller.signal.aborted ||
          requestVersion !== autoCollectRequestVersion ||
          autoCollectRequest !== request
        ) {
          return;
        }
        if (lastHandledClipboardStatusId === identity.statusId) {
          lastHandledClipboardStatusId = "";
        }
        setAutoCollectStatus("失败可手动", "error");
      } finally {
        if (autoCollectRequest === request) autoCollectRequest = null;
      }
    })();

    return request.promise;
  }

  function handleAutoCollectText(text, options = {}) {
    const identity = extractTweetIdentity(text);
    if (!identity) {
      if (options.fromClipboard) setAutoCollectStatus("监听中", "idle");
      return "none";
    }
    if (!options.force && identity.statusId === lastHandledClipboardStatusId) return "same";

    lastHandledClipboardStatusId = identity.statusId;
    collectTweetAutomatically(identity);
    return "handled";
  }

  function isTweetWorkspaceVisible() {
    return (
      root.isConnected &&
      !root.hidden &&
      document.visibilityState === "visible" &&
      root.getClientRects().length > 0
    );
  }

  async function attemptClipboardAutoCollect() {
    if (
      clipboardReadInProgress ||
      modalOpen ||
      batchModalOpen ||
      !isTweetWorkspaceVisible() ||
      (typeof document.hasFocus === "function" && !document.hasFocus())
    ) {
      return;
    }

    clipboardReadInProgress = true;
    const previousStatus = autoCollectStatus;
    const previousState = autoCollectState;
    const restorePreviousStatus = () => setAutoCollectStatus(previousStatus, previousState);
    const restoreInterruptedStatus = () => {
      if (modalOpen || batchModalOpen) {
        setAutoCollectStatus("监听中", "idle");
      } else {
        restorePreviousStatus();
      }
    };
    const canContinueReading = () =>
      !modalOpen &&
      !batchModalOpen &&
      isTweetWorkspaceVisible() &&
      (typeof document.hasFocus !== "function" || document.hasFocus());
    setAutoCollectStatus("读取中", "loading");
    try {
      let localClipboard = null;
      try {
        const response = await fetch("/api/tweet-clipboard", {
          cache: "no-store",
          signal: workspaceLifecycle.signal,
        });
        if (!response.ok) throw new Error("tweet-clipboard:" + response.status);
        localClipboard = await response.json();
      } catch {
        if (workspaceLifecycle.signal.aborted) return;
      }

      if (workspaceLifecycle.signal.aborted) return;
      if (localClipboard?.supported === true) {
        if (!canContinueReading()) {
          restoreInterruptedStatus();
          return;
        }
        if (localClipboard.found === true && String(localClipboard.url || "").trim()) {
          const result = handleAutoCollectText(localClipboard.url, { fromClipboard: true });
          if (result === "same") restorePreviousStatus();
          if (result === "none") setAutoCollectStatus("可粘贴链接", "error");
        } else {
          setAutoCollectStatus("监听中", "idle");
        }
        return;
      }

      if (!canContinueReading()) {
        restoreInterruptedStatus();
        return;
      }
      if (!navigator.clipboard || typeof navigator.clipboard.readText !== "function") {
        setAutoCollectStatus("可粘贴链接", "error");
        return;
      }

      try {
        const text = await navigator.clipboard.readText();
        if (workspaceLifecycle.signal.aborted) return;
        if (canContinueReading()) {
          const result = handleAutoCollectText(text, { fromClipboard: true });
          if (result === "same") restorePreviousStatus();
        } else {
          restoreInterruptedStatus();
        }
      } catch {
        if (workspaceLifecycle.signal.aborted) return;
        if (canContinueReading()) {
          setAutoCollectStatus("可粘贴链接", "error");
        } else {
          restoreInterruptedStatus();
        }
      }
    } finally {
      clipboardReadInProgress = false;
    }
  }

  function setupAutoCollect() {
    const signal = workspaceLifecycle.signal;
    const onFocus = () => attemptClipboardAutoCollect();
    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") attemptClipboardAutoCollect();
    };
    const onPaste = (event) => {
      if (modalOpen || batchModalOpen || !isTweetWorkspaceVisible()) return;
      const form = root.querySelector("#tweet-form");
      if (form && event.target instanceof Node && form.contains(event.target)) return;
      const text = event.clipboardData?.getData("text/plain") || "";
      if (!extractTweetIdentity(text)) return;
      handleAutoCollectText(text, { force: true });
    };

    window.addEventListener("focus", onFocus, { signal });
    document.addEventListener("visibilitychange", onVisibilityChange, { signal });
    document.addEventListener("paste", onPaste, { signal });

    autoCollectVisibilityObserver = new MutationObserver(() => {
      if (!root.hidden) attemptClipboardAutoCollect();
    });
    autoCollectVisibilityObserver.observe(root, {
      attributes: true,
      attributeFilter: ["hidden"],
    });
    queueMicrotask(attemptClipboardAutoCollect);
  }

  function cleanupTweetWorkspace() {
    workspaceLifecycle.abort();
    autoCollectVisibilityObserver?.disconnect();
    autoCollectVisibilityObserver = null;
    autoCollectRequest?.controller.abort();
    autoCollectRequest = null;
    autoCollectRequestVersion += 1;
    cleanupTweetMasonry();
    cancelMetadataWork();
    cancelBatchImportWork();
    tweetWorkspaceCleanups.delete(root);
  }

  async function addTweet(form) {
    if (isSaving) return;

    let data = new FormData(form);
    const urlInput = form.elements.namedItem("url");
    const urlResult = validateTweetUrl(String(data.get("url") || "").trim());
    if (urlInput && typeof urlInput.setCustomValidity === "function") {
      urlInput.setCustomValidity(urlResult.error);
    }
    if (urlResult.error) {
      if (urlInput && typeof urlInput.reportValidity === "function") urlInput.reportValidity();
      return;
    }

    const url = urlResult.url;
    const statusId = getTweetStatusId(url);
    if (statusId && locateTweetByStatusId(statusId, { closeModal: true })) return;
    syncMetadataContext(form, url);
    window.clearTimeout(autoFetchTimer);
    autoFetchTimer = 0;

    isSaving = true;
    const saveVersion = modalVersion;
    setTweetFormSaving(form, true);

    if (metadataRequest?.url === url) {
      await metadataRequest.promise;
    } else if (metadataAttemptedUrl !== url) {
      await fetchTweetMetadataForForm(form);
    }

    if (!modalOpen || saveVersion !== modalVersion) {
      isSaving = false;
      return;
    }

    data = new FormData(form);
    const bodyInput = form.elements.namedItem("body");
    const body = String(data.get("body") || "");
    const requestedMediaType = String(data.get("mediaType") || "text");
    let mediaType = normalizeMediaType(requestedMediaType);
    const metadata = currentMetadataUrl === url ? currentMetadata : null;
    const mediaUrl = String(data.get("mediaUrl") || "").trim();
    const metadataMediaUrls = normalizeMediaUrls(metadata?.mediaUrls, metadata?.mediaUrl);
    const mediaUrls = normalizeMediaUrls(metadataMediaUrls, mediaUrl);
    const videoUrl = String(data.get("videoUrl") || metadata?.videoUrl || "").trim();
    const hasTweetContent = Boolean(body.trim() || mediaUrls.length || videoUrl);
    if (!body.trim() && mediaType === "text") {
      mediaType = videoUrl ? "video" : mediaUrls.length ? "image" : "text";
    }
    if (bodyInput && typeof bodyInput.setCustomValidity === "function") {
      bodyInput.setCustomValidity(
        hasTweetContent ? "" : "请填写推文正文，或提供图片、视频媒体。",
      );
    }
    if (!hasTweetContent) {
      if (bodyInput && typeof bodyInput.reportValidity === "function") bodyInput.reportValidity();
      isSaving = false;
      setTweetFormSaving(form, false);
      return;
    }

    if (statusId && locateTweetByStatusId(statusId, { closeModal: true })) return;
    const tweet = {
      id: `tweet-${Date.now()}-${Math.random().toString(16).slice(2)}`,
      url,
      authorName: String(data.get("authorName") || "").trim() || "未命名作者",
      handle: normalizeHandle(String(data.get("handle") || "").trim()),
      body,
      mediaType,
      mediaUrl,
      mediaUrls,
      videoUrl,
      avatarUrl: String(metadata?.avatarUrl || "").trim(),
      tags: splitList(String(data.get("tags") || "")),
      note: String(data.get("note") || "").trim(),
      addedAt: new Date().toISOString(),
      accent: accentFor(mediaType),
      metadataSource: String(metadata?.source || "").trim(),
      metadataFetchedAt: normalizeFetchedAt(metadata?.fetchedAt),
      metadataUrl: String(metadata?.url || "").trim(),
    };

    tweets = [tweet, ...tweets];
    selectedId = tweet.id;
    saveTweets(tweets);
    modalOpen = false;
    modalVersion += 1;
    cancelMetadataWork();
    isSaving = false;
    render();
  }

  function syncMetadataContext(form, url) {
    if (metadataContextUrl === url) return;

    window.clearTimeout(autoFetchTimer);
    autoFetchTimer = 0;
    if (metadataRequest) metadataRequest.controller.abort();
    metadataRequest = null;
    setTweetMetadataButtonBusy(form, false);
    clearAppliedMetadata(form);
    metadataContextUrl = url;
    metadataAttemptedUrl = "";
    currentMetadata = null;
    currentMetadataUrl = "";
  }

  function scheduleTweetMetadataFetch(form) {
    const urlInput = form.elements.namedItem("url");
    const rawUrl = String(urlInput?.value || "").trim();
    const urlResult = validateTweetUrl(rawUrl);
    syncMetadataContext(form, urlResult.error ? rawUrl : urlResult.url);

    window.clearTimeout(autoFetchTimer);
    autoFetchTimer = 0;
    if (!rawUrl) {
      setTweetMetadataStatus(form, "", "");
      return;
    }
    if (urlResult.error) {
      setTweetMetadataStatus(form, "等待有效链接", "");
      return;
    }
    if (metadataAttemptedUrl === urlResult.url || metadataRequest?.url === urlResult.url) return;

    setTweetMetadataStatus(form, "准备读取...", "loading");
    autoFetchTimer = window.setTimeout(() => {
      autoFetchTimer = 0;
      fetchTweetMetadataForForm(form);
    }, 260);
  }

  async function fetchTweetMetadataForForm(form, options = {}) {
    const urlInput = form.elements.namedItem("url");
    const urlResult = validateTweetUrl(String(urlInput?.value || "").trim());
    if (urlResult.error) {
      if (urlInput && typeof urlInput.setCustomValidity === "function") {
        urlInput.setCustomValidity(urlResult.error);
      }
      setTweetMetadataStatus(form, "链接格式不正确", "error");
      return null;
    }

    const url = urlResult.url;
    syncMetadataContext(form, url);
    if (metadataRequest?.url === url) return metadataRequest.promise;
    if (!options.force && metadataAttemptedUrl === url) {
      return currentMetadataUrl === url ? currentMetadata : null;
    }

    const controller = new AbortController();
    const requestVersion = modalVersion;
    const request = { url, controller, promise: null };
    setTweetMetadataStatus(form, "读取中...", "loading");
    setTweetMetadataButtonBusy(form, true);

    request.promise = (async () => {
      try {
        const response = await fetch(`/api/tweet-meta?url=${encodeURIComponent(url)}`, {
          cache: "no-store",
          signal: controller.signal,
        });
        if (!response.ok) throw new Error(`tweet-meta:${response.status}`);
        const metadata = await response.json();
        if (controller.signal.aborted || !modalOpen || requestVersion !== modalVersion) return null;

        metadataAttemptedUrl = url;
        if (!metadata?.ok) {
          currentMetadata = null;
          currentMetadataUrl = "";
          setTweetMetadataStatus(form, "读取失败，可手动填写", "error");
          return metadata;
        }

        currentMetadata = metadata;
        currentMetadataUrl = url;
        applyTweetMetadataToForm(form, metadata, { overwrite: Boolean(options.overwrite) });
        setTweetMetadataStatus(form, "读取成功，已自动填充", "success");
        return metadata;
      } catch (error) {
        if (controller.signal.aborted || !modalOpen || requestVersion !== modalVersion) return null;
        metadataAttemptedUrl = url;
        currentMetadata = null;
        currentMetadataUrl = "";
        setTweetMetadataStatus(form, "读取失败，可手动填写", "error");
        return null;
      } finally {
        if (metadataRequest === request) {
          metadataRequest = null;
          setTweetMetadataButtonBusy(form, false);
        }
      }
    })();

    metadataRequest = request;
    return request.promise;
  }

  function applyTweetMetadataToForm(form, metadata, options = {}) {
    const overwrite = Boolean(options.overwrite);
    const mediaUrls = normalizeMediaUrls(metadata.mediaUrls, metadata.mediaUrl);
    const values = {
      authorName: String(metadata.authorName || "").trim(),
      handle: normalizeHandle(String(metadata.handle || "").trim()),
      body: String(metadata.body || "").trim(),
      mediaType: normalizeMediaType(metadata.mediaType),
      mediaUrl: String(metadata.mediaUrl || mediaUrls[0] || "").trim(),
      videoUrl: String(metadata.videoUrl || "").trim(),
    };

    Object.entries(values).forEach(([name, value]) => {
      const field = form.elements.namedItem(name);
      if (!field || (!value && name !== "mediaType")) return;

      const currentValue = String(field.value || "");
      const isDefaultMediaType = name === "mediaType" && !field.dataset.userEdited;
      const canApply =
        overwrite || isDefaultMediaType || !currentValue.trim() || appliedMetadataValues[name] === currentValue;
      if (!canApply) return;

      field.value = value;
      appliedMetadataValues[name] = value;
      if (name === "body" && typeof field.setCustomValidity === "function") field.setCustomValidity("");
    });
  }

  function clearAppliedMetadata(form) {
    Object.entries(appliedMetadataValues).forEach(([name, value]) => {
      const field = form.elements.namedItem(name);
      if (!field || String(field.value || "") !== value) return;
      field.value = name === "mediaType" ? "text" : "";
    });
    appliedMetadataValues = {};
  }

  function closeTweetModal() {
    modalOpen = false;
    modalVersion += 1;
    cancelMetadataWork();
    isSaving = false;
    render();
    queueMicrotask(attemptClipboardAutoCollect);
  }

  function cancelMetadataWork() {
    window.clearTimeout(autoFetchTimer);
    autoFetchTimer = 0;
    if (metadataRequest) metadataRequest.controller.abort();
    metadataRequest = null;
    metadataContextUrl = "";
    metadataAttemptedUrl = "";
    currentMetadata = null;
    currentMetadataUrl = "";
    appliedMetadataValues = {};
  }

  function openBatchImportModal() {
    cancelAutoCollectRequest({ allowRetry: true });
    cancelMetadataWork();
    modalOpen = false;
    modalVersion += 1;
    isSaving = false;
    cancelBatchImportWork();
    batchInputUrl = DEFAULT_BOOKMARKS_URL;
    batchMaxItems = "";
    batchImportState = createInitialBatchImportState();
    batchModalOpen = true;
    render();
    window.requestAnimationFrame(() => {
      root.querySelector('#tweet-batch-form input[name="url"]')?.focus();
    });
  }

  function closeBatchImportModal() {
    if (isBatchImportRunningStatus(batchImportState.status)) return;
    batchModalOpen = false;
    cancelBatchImportWork();
    batchInputUrl = DEFAULT_BOOKMARKS_URL;
    batchMaxItems = "";
    batchImportState = createInitialBatchImportState();
    render();
    queueMicrotask(attemptClipboardAutoCollect);
  }

  function cancelBatchImportWork() {
    window.clearTimeout(batchPollTimer);
    batchPollTimer = 0;
    batchRequestController?.abort();
    batchRequestController = null;
    batchJobId = "";
    batchImportVersion += 1;
  }

  function isCurrentBatchImport(version) {
    return (
      !workspaceLifecycle.signal.aborted &&
      batchModalOpen &&
      version === batchImportVersion
    );
  }

  function syncBatchImportUi() {
    if (!batchModalOpen) return;
    const form = root.querySelector("#tweet-batch-form");
    const statusHost = form?.querySelector("[data-tweet-batch-status]");
    if (!form || !statusHost) return;

    const running = isBatchImportRunningStatus(batchImportState.status);
    const complete = batchImportState.status === "complete";
    statusHost.innerHTML = renderTweetBatchStatus(batchImportState, batchMaxItems);
    form.querySelectorAll("input").forEach((input) => {
      input.disabled = running || complete;
    });
    const submitButton = form.querySelector("[data-tweet-batch-submit]");
    if (submitButton) {
      submitButton.hidden = complete;
      submitButton.disabled = running;
      submitButton.textContent = batchImportState.status === "error" ? "重试" : "开始同步";
    }
    form.querySelectorAll("#close-tweet-batch-modal, #cancel-tweet-batch-modal").forEach((button) => {
      button.disabled = running;
    });
  }

  function setBatchImportError(message, failures = []) {
    window.clearTimeout(batchPollTimer);
    batchPollTimer = 0;
    batchJobId = "";
    batchImportState = {
      ...batchImportState,
      status: "error",
      message: formatBatchFailure(message) || "批量导入失败，请稍后重试。",
      failureMessages: failures.map(formatBatchFailure).filter(Boolean).slice(0, 5),
    };
    syncBatchImportUi();
  }

  async function startBatchImport(form) {
    if (!form || isBatchImportRunningStatus(batchImportState.status)) return;

    const urlInput = form.elements.namedItem("url");
    const maxInput = form.elements.namedItem("maxItems");
    const urlResult = validateTweetBookmarksUrl(String(urlInput?.value || "").trim());
    if (urlInput && typeof urlInput.setCustomValidity === "function") {
      urlInput.setCustomValidity(urlResult.error);
    }
    if (urlResult.error) {
      urlInput?.reportValidity?.();
      return;
    }

    const maxItemsValue = String(maxInput?.value || "").trim();
    const maxItems = maxItemsValue ? Number(maxItemsValue) : null;
    const maxItemsError =
      maxItemsValue && (!Number.isInteger(maxItems) || maxItems < 1 || maxItems > 1000)
        ? "上限必须是 1 到 1000 的整数。"
        : "";
    if (maxInput && typeof maxInput.setCustomValidity === "function") {
      maxInput.setCustomValidity(maxItemsError);
    }
    if (maxItemsError) {
      maxInput?.reportValidity?.();
      return;
    }

    batchInputUrl = urlResult.url;
    batchMaxItems = maxItemsValue;
    cancelAutoCollectRequest({ allowRetry: true });
    cancelMetadataWork();
    cancelBatchImportWork();
    const requestVersion = batchImportVersion;
    batchImportState = {
      ...createInitialBatchImportState(),
      status: "starting",
      message: "正在启动导入任务...",
    };
    syncBatchImportUi();

    const controller = new AbortController();
    batchRequestController = controller;
    try {
      const requestBody = { url: batchInputUrl };
      if (maxItems !== null) requestBody.maxItems = maxItems;
      const response = await fetch("/api/tweet-bookmarks/import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(requestBody),
        cache: "no-store",
        signal: controller.signal,
      });
      const payload = await readJsonResponse(response);
      if (!isCurrentBatchImport(requestVersion)) return;
      const responseJobId = String(payload?.jobId || "").trim();
      const responseStatus = String(payload?.status || "").trim();
      const matchesRequest =
        payload?.requestUrl === batchInputUrl &&
        normalizeBatchRequestMaxItems(payload?.maxItems) === maxItems;
      const startedNewJob = response.status === 202 && payload?.ok && responseJobId && matchesRequest;
      const attachedToActiveJob =
        response.status === 409 &&
        responseJobId &&
        matchesRequest &&
        isBatchImportRunningStatus(responseStatus);
      if (response.status === 409 && responseJobId && !matchesRequest) {
        throw new Error("另一个收藏夹正在批量导入，请等它完成后再试。");
      }
      if (!startedNewJob && !attachedToActiveJob) {
        throw new Error(formatBatchFailure(payload?.message) || `启动失败 (${response.status})`);
      }

      batchJobId = responseJobId;
      batchImportState = {
        ...batchImportState,
        status: isBatchImportRunningStatus(responseStatus) ? responseStatus : "starting",
        message: attachedToActiveJob ? "已连接正在运行的导入任务。" : formatBatchFailure(payload.message),
      };
      syncBatchImportUi();
      scheduleBatchImportPoll(requestVersion, batchJobId);
    } catch (error) {
      if (controller.signal.aborted || !isCurrentBatchImport(requestVersion)) return;
      setBatchImportError(error?.message);
    } finally {
      if (batchRequestController === controller) batchRequestController = null;
    }
  }

  function importLocalBookmarkJson(form) {
    if (!form || isBatchImportRunningStatus(batchImportState.status)) return;
    const input = form.elements.namedItem("localJson");
    const raw = String(input?.value || "").trim();
    if (!raw) {
      setBatchImportError("请先粘贴本机浏览器读取结果。");
      return;
    }
    if (raw.length > 2_000_000) {
      setBatchImportError("本机读取结果超过 2 MB，请减少条数后分批导入。");
      return;
    }

    try {
      const parsed = JSON.parse(raw);
      const items = Array.isArray(parsed) ? parsed : parsed?.items;
      if (!Array.isArray(items) || !items.length) {
        throw new Error("读取结果中没有可导入的收藏。");
      }
      if (items.length > 1000) {
        throw new Error("单次最多导入 1000 条收藏，请分批处理。");
      }
      const safeItems = items.map(sanitizeLocalBookmarkItem);
      batchImportState = {
        ...createInitialBatchImportState(),
        status: "complete",
        foundCount: safeItems.length,
        processedCount: safeItems.length,
      };
      completeBatchImport(
        {
          status: "complete",
          foundCount: safeItems.length,
          processedCount: safeItems.length,
          items: safeItems,
          failures: Array.isArray(parsed?.failures) ? parsed.failures : [],
          truncated: false,
          truncationReason: "",
        },
        batchImportVersion,
      );
    } catch (error) {
      setBatchImportError(error instanceof Error ? error.message : "无法解析本机浏览器读取结果。");
    }
  }

  function scheduleBatchImportPoll(version, jobId) {
    window.clearTimeout(batchPollTimer);
    if (!isCurrentBatchImport(version)) return;
    batchPollTimer = window.setTimeout(() => {
      batchPollTimer = 0;
      pollBatchImport(version, jobId);
    }, 1000);
  }

  async function pollBatchImport(version, jobId) {
    if (!isCurrentBatchImport(version) || jobId !== batchJobId) return;
    const controller = new AbortController();
    batchRequestController = controller;
    try {
      const response = await fetch(
        "/api/tweet-bookmarks/import/" + encodeURIComponent(jobId),
        { cache: "no-store", signal: controller.signal },
      );
      const payload = await readJsonResponse(response);
      if (!isCurrentBatchImport(version) || jobId !== batchJobId) return;
      if (!response.ok) {
        throw new Error(formatBatchFailure(payload?.message) || `查询失败 (${response.status})`);
      }

      const status = String(payload?.status || "").trim();
      if (!isBatchImportStatus(status)) throw new Error("导入任务返回了未知状态。");
      batchImportState = {
        ...batchImportState,
        status,
        message: formatBatchFailure(payload.message),
        foundCount: normalizeBatchCount(payload.foundCount),
        processedCount: normalizeBatchCount(payload.processedCount),
      };

      if (status === "complete") {
        completeBatchImport(payload, version);
        return;
      }
      if (status === "error") {
        setBatchImportError(payload.message, Array.isArray(payload.failures) ? payload.failures : []);
        return;
      }

      syncBatchImportUi();
      scheduleBatchImportPoll(version, jobId);
    } catch (error) {
      if (controller.signal.aborted || !isCurrentBatchImport(version)) return;
      setBatchImportError(error?.message);
    } finally {
      if (batchRequestController === controller) batchRequestController = null;
    }
  }

  function completeBatchImport(payload, version) {
    if (!isCurrentBatchImport(version) || batchImportState.status !== "complete") return;
    window.clearTimeout(batchPollTimer);
    batchPollTimer = 0;

    const backendFailures = Array.isArray(payload.failures) ? payload.failures : [];
    const backendFailedStatusIds = new Set(
      backendFailures.map((failure) => getTweetStatusId(failure?.url)).filter(Boolean),
    );
    const existingStatusIds = new Set(tweets.map((tweet) => getTweetStatusId(tweet.url)).filter(Boolean));
    const batchStatusIds = new Set();
    const importedTweets = [];
    const localFailures = [];
    let addedCount = 0;
    let duplicateCount = 0;

    const items = Array.isArray(payload.items) ? payload.items : [];
    items.forEach((item) => {
      const identity = getBatchTweetIdentity(item);
      if (!identity) {
        localFailures.push("有一条推文缺少有效的 statusId 或链接。");
        return;
      }
      if (existingStatusIds.has(identity.statusId) || batchStatusIds.has(identity.statusId)) {
        duplicateCount += 1;
        return;
      }

      batchStatusIds.add(identity.statusId);
      let tweet = createTweetFromMetadata(identity, item?.metadata);
      if (!tweet) {
        if (!backendFailedStatusIds.has(identity.statusId)) {
          localFailures.push(`推文 ${identity.statusId} 的正文和媒体均不可用。`);
        }
        tweet = createTweetLinkPlaceholder(identity, item?.metadata);
      } else {
        addedCount += 1;
      }
      importedTweets.push(tweet);
    });

    const relevantBackendFailures = filterTweetBatchFailures(
      backendFailures,
      batchStatusIds,
    );
    const failureMessages = [...relevantBackendFailures, ...localFailures]
      .map(formatBatchFailure)
      .filter(Boolean)
      .slice(0, 5);
    const nextTweets = [...importedTweets, ...tweets];
    try {
      saveTweets(nextTweets);
    } catch {
      throw new Error("本地存储空间不足，本次批量结果没有写入，请减少导入数量后重试。");
    }
    tweets = nextTweets;
    if (importedTweets.length) {
      selectedId = importedTweets[0].id;
      query = "";
      type = "all";
    }

    batchJobId = "";
    batchInputUrl = "";
    batchMaxItems = "";
    const foundCount = normalizeBatchCount(payload.foundCount);
    batchImportState = {
      status: "complete",
      message: "",
      foundCount,
      processedCount: normalizeBatchCount(payload.processedCount),
      summary: createTweetBatchSummary(
        addedCount,
        duplicateCount,
        relevantBackendFailures,
        localFailures,
      ),
      failureMessages,
      notice: payload.truncated
        ? payload.truncationReason === "safety_limit"
          ? "已达到单次 1000 条的本地存储安全上限；更早的收藏可在下次按文件夹分批同步。"
          : "收藏夹内容较多，本次扫描达到安全时限，结果可能不完整。"
        : foundCount === 0
          ? "X 页面已显示当前收藏夹为空，本次没有可同步的内容。"
          : "",
    };
    render();
  }

  function deleteSelectedTweet() {
    const selected = selectedTweet();
    if (!selected) return;
    const deletedStatusId = getTweetStatusId(selected.url);
    tweets = tweets.filter((tweet) => tweet.id !== selected.id);
    if (deletedStatusId && deletedStatusId === lastHandledClipboardStatusId) {
      lastHandledClipboardStatusId = "";
    }
    selectedId = tweets[0]?.id || "";
    saveTweets(tweets);
    render();
  }

  function openTweet(tweet) {
    if (!tweet?.url) return;
    window.open(tweet.url, "_blank", "noreferrer");
  }

  function render() {
    const visible = filteredTweets();
    const selected = selectedTweet();
    cleanupTweetMasonry();

    root.innerHTML = `
      <header class="topbar">
        <div>
          <p class="eyebrow">Twitter / X</p>
          <h1>推特收藏</h1>
        </div>
        <div class="tweetTopActions">
          <button class="secondaryButton" id="new-tweet" type="button"><span>+</span><span>收藏推文</span></button>
          <button class="primaryButton" id="batch-import-tweets" type="button"><span>同步 X 收藏</span></button>
        </div>
      </header>

      <div class="paperStatusBar">
        <div class="tweetStatusCopy">
          <span>仅收藏来自 x.com 或 twitter.com 的文字、图片和视频推文</span>
          <small class="tweetAutoCollectStatus" data-tweet-auto-status data-state="${escapeAttr(autoCollectState)}" role="status" aria-live="polite">自动收藏 · ${escapeHtml(autoCollectStatus)}</small>
        </div>
        <span>${tweets.length} 条收藏 · 当前 ${visible.length} 条匹配</span>
      </div>

      <div class="tweetLayout">
        <section class="tweetListPane">
          <div class="searchBox">
            <span>Q</span>
            <input id="tweet-search" placeholder="搜索正文、作者、标签或链接" value="${escapeAttr(query)}" />
          </div>

          <div class="segmented">
            ${Object.entries(typeLabels)
              .map(
                ([key, label]) =>
                  `<button class="${type === key ? "selected" : ""}" data-tweet-type="${key}" type="button">${label}</button>`,
              )
              .join("")}
          </div>

          <div class="tweetGrid">
            ${
              visible.length
                ? visible.map((tweet) => renderTweetCard(tweet, selected?.id === tweet.id)).join("")
                : `<div class="emptyState">暂无匹配推文</div>`
            }
          </div>
        </section>

        <aside class="tweetDetailPane">
          ${selected ? renderTweetDetail(selected) : `<div class="emptyState">添加推文后，这里会显示详情</div>`}
        </aside>
      </div>

      ${modalOpen ? renderTweetModal() : ""}
      ${batchModalOpen ? renderTweetBatchModal(batchImportState, batchInputUrl, batchMaxItems) : ""}
    `;

    bindEvents();
    setupTweetMasonry();
  }

  function bindEvents() {
    root.querySelector("#new-tweet")?.addEventListener("click", () => {
      cancelAutoCollectRequest({ allowRetry: true });
      cancelMetadataWork();
      modalOpen = true;
      modalVersion += 1;
      render();
    });

    root.querySelector("#batch-import-tweets")?.addEventListener("click", () => {
      openBatchImportModal();
    });

    root.querySelector("#tweet-search")?.addEventListener("input", (event) => {
      query = event.target.value;
      render();
    });

    root.querySelectorAll("[data-tweet-type]").forEach((button) => {
      button.addEventListener("click", () => {
        type = button.dataset.tweetType || "all";
        render();
      });
    });

    root.querySelectorAll("[data-tweet-id]").forEach((button) => {
      button.addEventListener("click", () => {
        selectedId = button.dataset.tweetId;
        render();
      });
    });

    root.querySelector("#tweet-form")?.addEventListener("submit", (event) => {
      event.preventDefault();
      addTweet(event.currentTarget);
    });

    root.querySelector("#tweet-batch-form")?.addEventListener("submit", (event) => {
      event.preventDefault();
      startBatchImport(event.currentTarget);
    });

    root.querySelector("#import-tweet-local-json")?.addEventListener("click", (event) => {
      importLocalBookmarkJson(event.currentTarget.form);
    });

    root.querySelector('#tweet-batch-form input[name="url"]')?.addEventListener("input", (event) => {
      event.currentTarget.setCustomValidity("");
      batchInputUrl = event.currentTarget.value;
    });

    root
      .querySelector('#tweet-batch-form input[name="maxItems"]')
      ?.addEventListener("input", (event) => {
        event.currentTarget.setCustomValidity("");
        batchMaxItems = event.currentTarget.value;
      });

    const tweetForm = root.querySelector("#tweet-form");
    const urlInput = tweetForm?.elements.namedItem("url");
    urlInput?.addEventListener("input", (event) => {
      event.currentTarget.setCustomValidity("");
      scheduleTweetMetadataFetch(tweetForm);
    });
    urlInput?.addEventListener("blur", () => {
      const rawUrl = String(urlInput.value || "").trim();
      if (rawUrl && validateTweetUrl(rawUrl).error) {
        setTweetMetadataStatus(tweetForm, "链接格式不正确", "error");
      }
    });

    tweetForm?.elements.namedItem("body")?.addEventListener("input", (event) => {
      event.currentTarget.setCustomValidity("");
    });

    tweetForm?.elements.namedItem("mediaType")?.addEventListener("change", (event) => {
      event.currentTarget.dataset.userEdited = "true";
    });

    root.querySelector("#fetch-tweet-meta")?.addEventListener("click", (event) => {
      window.clearTimeout(autoFetchTimer);
      autoFetchTimer = 0;
      fetchTweetMetadataForForm(event.currentTarget.form, { force: true, overwrite: true });
    });

    root.querySelector("#close-tweet-modal")?.addEventListener("click", () => {
      closeTweetModal();
    });

    root.querySelector("#cancel-tweet-modal")?.addEventListener("click", () => {
      closeTweetModal();
    });

    root.querySelector("#close-tweet-batch-modal")?.addEventListener("click", () => {
      closeBatchImportModal();
    });

    root.querySelector("#cancel-tweet-batch-modal")?.addEventListener("click", () => {
      closeBatchImportModal();
    });

    root.querySelector("#open-tweet")?.addEventListener("click", () => {
      openTweet(selectedTweet());
    });

    root.querySelector("#delete-tweet")?.addEventListener("click", () => {
      deleteSelectedTweet();
    });
  }

  tweetWorkspaceCleanups.set(root, cleanupTweetWorkspace);
  render();
  setupAutoCollect();
}

function renderTweetCard(tweet, selected) {
  const sourceLabel = getTweetSourceLabel(tweet.url);
  return `
    <button class="tweetCard ${selected ? "selected" : ""}" data-tweet-id="${escapeAttr(tweet.id)}" style="--accent: ${escapeAttr(tweet.accent)}" type="button">
      <div class="tweetCardHeader">
        <div class="tweetAuthor">
          ${renderTweetAvatar(tweet)}
          <div class="tweetAuthorText">
            <strong>${escapeHtml(tweet.authorName)}</strong>
            <span>${escapeHtml(tweet.handle || "@unknown")}</span>
          </div>
        </div>
        <em>${escapeHtml(sourceLabel)} · ${escapeHtml(typeLabels[tweet.mediaType] || tweet.mediaType)}</em>
      </div>
      ${tweet.body.trim() ? `<p class="tweetCardBody">${escapeHtml(tweet.body)}</p>` : ""}
      ${renderTweetMedia(tweet)}
      <div class="tagRow">
        ${(tweet.tags || []).slice(0, 4).map((tag) => `<span>${escapeHtml(tag)}</span>`).join("")}
      </div>
    </button>
  `;
}

function renderTweetDetail(tweet) {
  const sourceLabel = getTweetSourceLabel(tweet.url);
  return `
    <div class="tweetDetailHero" style="--accent: ${escapeAttr(tweet.accent)}">
      <span>${escapeHtml(sourceLabel)} · ${escapeHtml(typeLabels[tweet.mediaType] || tweet.mediaType)}推文</span>
      <div class="tweetDetailIdentity">
        ${renderTweetAvatar(tweet, true)}
        <div>
          <h2>${escapeHtml(tweet.authorName)}</h2>
          <p>${escapeHtml(tweet.handle || "@unknown")}</p>
        </div>
      </div>
    </div>

    ${renderTweetMedia(tweet, true)}

    ${
      tweet.body.trim()
        ? `<section class="detailSection">
            <h3>正文</h3>
            <p>${escapeHtml(tweet.body)}</p>
          </section>`
        : ""
    }

    <section class="detailSection">
      <h3>标签</h3>
      <p>${(tweet.tags || []).length ? tweet.tags.map(escapeHtml).join(" / ") : "暂无标签"}</p>
    </section>

    <section class="detailSection">
      <h3>备注</h3>
      <p>${escapeHtml(tweet.note || "暂无备注")}</p>
    </section>

    <section class="detailSection">
      <h3>推文来源</h3>
      <p>${escapeHtml(sourceLabel)}${sourceLabel === "历史来源" ? "（旧数据；今后仅可新增 Twitter/X 推文）" : ""}</p>
      <p class="tweetUrlLine">${escapeHtml(tweet.url)}</p>
    </section>

    ${
      tweet.metadataSource
        ? `<section class="detailSection"><h3>读取信息</h3><p>${escapeHtml(tweet.metadataSource)}${tweet.metadataFetchedAt ? ` · ${escapeHtml(formatMetadataTime(tweet.metadataFetchedAt))}` : ""}</p></section>`
        : ""
    }

    <div class="detailActions">
      <button class="primaryButton" id="open-tweet" type="button"><span>↗</span><span>打开推文</span></button>
      <button class="secondaryButton" id="delete-tweet" type="button"><span>×</span><span>移除</span></button>
    </div>
  `;
}

function renderTweetMedia(tweet, large = false) {
  if (tweet.mediaType === "text") {
    if (!large) return "";
    return `
      <div class="tweetTextMedia large">
        <span>“</span>
        <strong>${escapeHtml(tweet.body.slice(0, 64))}${tweet.body.length > 64 ? "..." : ""}</strong>
      </div>
    `;
  }

  const mediaUrl = getPrimaryMediaUrl(tweet);
  const fallbackLabel = tweet.mediaType === "video" ? "视频封面不可用" : "图片暂不可用";
  return `
    <div class="tweetMedia ${tweet.mediaType === "video" ? "video" : ""} ${large ? "large" : ""} ${mediaUrl ? "" : "has-failed-image"}">
      ${
        mediaUrl
          ? `<img src="${escapeAttr(mediaUrl)}" alt="${escapeAttr(tweet.authorName)} 推文媒体" loading="lazy" onerror="handleTweetMediaError(this)" />`
          : ""
      }
      <div class="tweetMediaFallback"><span>${fallbackLabel}</span></div>
      ${tweet.mediaType === "video" ? `<span class="tweetPlay">▶</span>` : ""}
    </div>
  `;
}

function renderTweetAvatar(tweet, large = false) {
  const initial = getAuthorInitial(tweet.authorName || tweet.handle);
  return `
    <span class="tweetAvatar ${large ? "large" : ""}" aria-hidden="true">
      <span>${escapeHtml(initial)}</span>
      ${tweet.avatarUrl ? `<img src="${escapeAttr(tweet.avatarUrl)}" alt="" loading="lazy" onerror="this.remove()" />` : ""}
    </span>
  `;
}

function renderTweetModal() {
  return `
    <div class="modalBackdrop" role="presentation">
      <form class="paperModal" id="tweet-form">
        <div class="modalHeader">
          <h2>收藏 Twitter/X 推文</h2>
          <button id="close-tweet-modal" type="button">关闭</button>
        </div>

        <div class="formGrid">
          <label class="wide">Twitter/X 推文链接<input name="url" required inputmode="url" autocomplete="url" placeholder="https://x.com/账号/status/推文ID" /><small>仅支持 x.com 或 twitter.com 的标准 status 推文链接</small></label>
          <div class="metadataFetchRow wide">
            <button class="secondaryButton" id="fetch-tweet-meta" type="button">读取推文</button>
            <span class="metadataStatus" data-tweet-metadata-status role="status" aria-live="polite"></span>
          </div>
          <label>作者<input name="authorName" placeholder="作者名称" /></label>
          <label>账号<input name="handle" placeholder="@handle" /></label>
          <label>
            类型
            <select name="mediaType">
              <option value="text">文字</option>
              <option value="image">图片</option>
              <option value="video">视频</option>
            </select>
          </label>
          <label class="wide">正文<textarea name="body" placeholder="读取失败时可手动粘贴推文正文"></textarea></label>
          <label class="wide">图片或视频封面 URL<input name="mediaUrl" inputmode="url" placeholder="自动读取真实媒体地址，也可手动填写" /></label>
          <label class="wide">视频源 URL<input name="videoUrl" inputmode="url" placeholder="视频推文会自动填写真实视频地址" /></label>
          <label class="wide">标签<input name="tags" placeholder="AI, 灵感, 论文" /></label>
          <label class="wide">备注<textarea name="note" placeholder="为什么收藏、后面要怎么用"></textarea></label>
        </div>

        <div class="modalFooter">
          <button class="secondaryButton" id="cancel-tweet-modal" type="button">取消</button>
          <button class="primaryButton" type="submit" data-tweet-submit>保存推文</button>
        </div>
      </form>
    </div>
  `;
}

function renderTweetBatchModal(state, inputUrl, maxItems) {
  const complete = state.status === "complete";
  const running = isBatchImportRunningStatus(state.status);
  return `
    <div class="modalBackdrop tweetBatchBackdrop" role="presentation">
      <form class="paperModal tweetBatchModal" id="tweet-batch-form" role="dialog" aria-modal="true" aria-labelledby="tweet-batch-title" autocomplete="off">
        <div class="modalHeader">
          <h2 id="tweet-batch-title">同步 X 收藏</h2>
          <button id="close-tweet-batch-modal" type="button" aria-label="关闭 X 收藏同步" ${running ? "disabled" : ""}>关闭</button>
        </div>

        ${
          complete
            ? ""
            : `<div class="tweetBatchGuide">
                <strong>数据仅在本机处理，不需要粘贴 Cookie</strong>
                <span>首次同步会打开独立的 Edge 窗口供你登录 X；收藏链接、正文和媒体不会发送给第三方补全服务。</span>
              </div>`
        }

        ${
          complete
            ? ""
            : `<div class="formGrid tweetBatchFields">
                <label class="wide">X 收藏夹 URL<input name="url" required inputmode="url" autocomplete="off" value="${escapeAttr(inputUrl)}" placeholder="https://x.com/i/bookmarks" /><small>支持 /i/bookmarks 和收藏夹文件夹链接</small></label>
                <label class="tweetBatchLimit">最多同步（可选）<input name="maxItems" type="number" min="1" max="1000" step="1" inputmode="numeric" value="${escapeAttr(maxItems)}" placeholder="留空最多 1000 条" /><small>为保护本地存储，单次同步上限为 1000 条</small></label>
              </div>
              <details class="tweetBatchLocalImport">
                <summary>从本机浏览器读取结果导入</summary>
                <label>本地 JSON<textarea name="localJson" rows="5" spellcheck="false" placeholder="仅接收本机浏览器读取的收藏数据"></textarea></label>
                <button class="secondaryButton" id="import-tweet-local-json" type="button">导入读取结果</button>
              </details>`
        }

        <div class="tweetBatchStatusHost" data-tweet-batch-status role="status" aria-live="polite">
          ${renderTweetBatchStatus(state, maxItems)}
        </div>

        <div class="modalFooter">
          <button class="secondaryButton" id="cancel-tweet-batch-modal" type="button" ${running ? "disabled" : ""}>关闭</button>
          <button class="primaryButton" type="submit" data-tweet-batch-submit ${complete ? "hidden" : ""} ${running ? "disabled" : ""}>${state.status === "error" ? "重试" : "开始同步"}</button>
        </div>
      </form>
    </div>
  `;
}

function renderTweetBatchStatus(state, maxItems) {
  if (state.status === "idle") return "";
  if (state.status === "complete") {
    const summary = state.summary || { addedCount: 0, duplicateCount: 0, failureCount: 0 };
    return `
      <div class="tweetBatchComplete">
        <div class="tweetBatchSummary" aria-label="导入结果">
          <div><strong>${summary.addedCount}</strong><span>新增</span></div>
          <div><strong>${summary.duplicateCount}</strong><span>重复</span></div>
          <div><strong>${summary.failureCount}</strong><span>待补全 / 失败</span></div>
        </div>
        ${state.notice ? `<p class="tweetBatchNotice">${escapeHtml(state.notice)}</p>` : ""}
        ${renderTweetBatchFailures(state.failureMessages)}
      </div>
    `;
  }

  const foundCount = normalizeBatchCount(state.foundCount);
  const processedCount = normalizeBatchCount(state.processedCount);
  const statusLabels = {
    starting: "正在启动",
    awaiting_login: "等待登录",
    scanning: "正在扫描收藏夹",
    enriching: "正在整理本地数据",
    error: "导入失败",
  };
  const defaultMessages = {
    starting: "正在启动独立 Edge 窗口...",
    awaiting_login: "请在弹出的独立 Edge 窗口中登录 X，登录后这里会自动继续。",
    scanning: "正在查找收藏夹中的推文...",
    enriching: "正在整理从 X 页面读取到的正文和媒体，不会调用第三方补全服务...",
    error: "批量导入失败，请检查链接后重试。",
  };
  const message = ["starting", "awaiting_login", "scanning", "enriching"].includes(state.status)
    ? defaultMessages[state.status]
    : state.message || defaultMessages[state.status] || "正在处理...";
  const progress =
    state.status === "scanning"
      ? `<progress class="tweetBatchProgress" aria-label="扫描进度"></progress>`
      : state.status === "enriching"
        ? `<progress class="tweetBatchProgress" value="${Math.min(processedCount, Math.max(foundCount, processedCount, 1))}" max="${Math.max(foundCount, processedCount, 1)}" aria-label="内容读取进度"></progress>`
        : "";
  const counts = ["scanning", "enriching"].includes(state.status)
    ? `<p class="tweetBatchCounts"><span>已发现 ${foundCount}</span><span>已处理 ${processedCount}</span>${maxItems ? `<span>上限 ${escapeHtml(maxItems)}</span>` : ""}</p>`
    : "";
  return `
    <div class="tweetBatchStatus" data-state="${escapeAttr(state.status)}">
      <strong>${escapeHtml(statusLabels[state.status] || "导入中")}</strong>
      <p>${escapeHtml(message)}</p>
      ${progress}
      ${counts}
      ${state.status === "error" ? renderTweetBatchFailures(state.failureMessages) : ""}
    </div>
  `;
}

function renderTweetBatchFailures(failures) {
  const items = Array.isArray(failures) ? failures.filter(Boolean).slice(0, 5) : [];
  if (!items.length) return "";
  return `
    <div class="tweetBatchFailures">
      <strong>部分失败原因</strong>
      <ul>${items.map((message) => `<li>${escapeHtml(message)}</li>`).join("")}</ul>
    </div>
  `;
}

function setTweetMetadataStatus(form, message, state = "") {
  const status = form?.querySelector("[data-tweet-metadata-status]");
  if (!status) return;
  status.textContent = message;
  status.dataset.state = state;
}

function setTweetMetadataButtonBusy(form, busy) {
  const button = form?.querySelector("#fetch-tweet-meta");
  if (!button) return;
  button.disabled = busy || form.dataset.saving === "true";
}

function setTweetFormSaving(form, saving) {
  if (!form) return;
  form.dataset.saving = saving ? "true" : "false";
  const submitButton = form.querySelector("[data-tweet-submit]");
  if (submitButton) {
    submitButton.disabled = saving;
    submitButton.textContent = saving ? "读取并保存..." : "保存推文";
  }
  setTweetMetadataButtonBusy(form, saving);
}

function createInitialBatchImportState() {
  return {
    status: "idle",
    message: "",
    foundCount: 0,
    processedCount: 0,
    summary: null,
    failureMessages: [],
    notice: "",
  };
}

function isBatchImportStatus(status) {
  return ["starting", "awaiting_login", "scanning", "enriching", "complete", "error"].includes(
    status,
  );
}

function isBatchImportRunningStatus(status) {
  return ["starting", "awaiting_login", "scanning", "enriching"].includes(status);
}

function normalizeBatchCount(value) {
  const count = Number(value);
  return Number.isFinite(count) && count > 0 ? Math.floor(count) : 0;
}

function normalizeBatchRequestMaxItems(value) {
  if (value === null || value === undefined || value === "") return null;
  const count = Number(value);
  return Number.isInteger(count) && count >= 1 && count <= 1000 ? count : NaN;
}

function formatBatchFailure(value) {
  const raw =
    typeof value === "string"
      ? value
      : String(value?.message || value?.error || value?.reason || "");
  if (!raw.trim()) return "";
  const normalized = raw.trim().toLowerCase();
  const errorMappings = [
    ["selenium", "当前环境未安装 Selenium，无法批量导入。"],
    ["unable to start edge", "无法启动用于导入的 Edge 窗口。"],
    ["unable to open the x bookmarks page", "无法在 Edge 中打开 X 收藏夹页面。"],
    ["login was not completed within 5 minutes", "登录等待已超过 5 分钟，请重试。"],
    ["unable to read bookmarks from the x page", "读取 X 收藏夹失败，请重试。"],
    ["bookmark import failed", "收藏夹导入失败，请重试。"],
    ["metadata_timeout", "部分推文内容读取超时，未保存无法确认的空内容。"],
    ["metadata_fetch_failed", "部分推文内容读取失败，未保存无法确认的空内容。"],
    ["metadata_not_found", "部分推文没有读取到正文或媒体。"],
    ["tweet_not_found", "部分推文已删除、不可见或无法访问。"],
    ["tweet body and media are unavailable", "部分推文没有可用的正文或媒体。"],
  ];
  const mappedMessage = errorMappings.find(([source]) => normalized.includes(source))?.[1];
  if (mappedMessage) return mappedMessage;
  if (
    /cookie|profile|authorization|bearer|token|session|password|登录信息|浏览器配置|用户目录/i.test(
      raw,
    )
  ) {
    return "导入失败（敏感详情已隐藏）。";
  }
  return raw
    .replace(/https?:\/\/\S+/gi, "[链接已隐藏]")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 120);
}

async function readJsonResponse(response) {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

function validateTweetBookmarksUrl(value) {
  const normalized = normalizeUrl(value);
  if (!normalized) return { url: "", error: "请输入 X 收藏夹链接。" };

  let parsed;
  try {
    parsed = new URL(normalized);
  } catch {
    return { url: "", error: "收藏夹链接格式无效。" };
  }

  const allowedHosts = ["x.com", "www.x.com", "twitter.com", "www.twitter.com"];
  const pathMatch = parsed.pathname.match(/^\/i\/bookmarks(?:\/(\d{1,30}))?\/?$/i);
  if (
    !["http:", "https:"].includes(parsed.protocol) ||
    !allowedHosts.includes(parsed.hostname.toLowerCase()) ||
    parsed.username ||
    parsed.password ||
    parsed.port ||
    !pathMatch
  ) {
    return { url: "", error: "请使用 X 的 /i/bookmarks 或收藏夹文件夹链接。" };
  }

  const folderPath = pathMatch[1] ? "/" + pathMatch[1] : "";
  return { url: "https://x.com/i/bookmarks" + folderPath, error: "" };
}

function getBatchTweetIdentity(item) {
  if (!item || typeof item !== "object") return null;
  const explicitStatusId = /^\d{1,19}$/.test(String(item.statusId || "").trim())
    ? String(item.statusId).trim()
    : "";
  const identity = [item.url, item.metadata?.url]
    .map((value) => extractTweetIdentity(value))
    .find(Boolean);
  if (identity && explicitStatusId && identity.statusId !== explicitStatusId) return null;

  const statusId = explicitStatusId || identity?.statusId || "";
  if (!statusId) return null;
  if (identity) return { ...identity, statusId };

  const handle = String(item.metadata?.handle || "")
    .trim()
    .replace(/^@/, "");
  if (!/^[a-z0-9_]{1,15}$/i.test(handle)) return null;
  return {
    statusId,
    handle,
    url: `https://x.com/${handle}/status/${statusId}`,
    canonicalUrl: `https://x.com/${handle}/status/${statusId}`,
  };
}

export function filterTweetBatchFailures(failures, processedStatusIds) {
  const processed =
    processedStatusIds instanceof Set
      ? processedStatusIds
      : new Set(Array.isArray(processedStatusIds) ? processedStatusIds : []);
  return (Array.isArray(failures) ? failures : []).filter((failure) => {
    const statusId = getTweetStatusId(failure?.url);
    return Boolean(statusId && processed.has(statusId));
  });
}

function sanitizeLocalBookmarkItem(item) {
  if (!item || typeof item !== "object") return item;
  const metadata = item.metadata && typeof item.metadata === "object" ? item.metadata : {};
  const mediaUrls = normalizeMediaUrls(metadata.mediaUrls, metadata.mediaUrl)
    .map(sanitizeXAssetUrl)
    .filter(Boolean);
  return {
    ...item,
    metadata: {
      ...metadata,
      authorName: String(metadata.authorName || "").slice(0, 200),
      handle: String(metadata.handle || "").slice(0, 32),
      body: String(metadata.body || "").slice(0, 50_000),
      mediaUrl: mediaUrls[0] || "",
      mediaUrls,
      videoUrl: sanitizeXAssetUrl(metadata.videoUrl),
      avatarUrl: sanitizeXAssetUrl(metadata.avatarUrl),
    },
  };
}

function sanitizeXAssetUrl(value) {
  const raw = String(value || "").trim();
  if (!raw) return "";
  try {
    const parsed = new URL(raw);
    const allowedHosts = new Set(["pbs.twimg.com", "video.twimg.com"]);
    return parsed.protocol === "https:" && allowedHosts.has(parsed.hostname.toLowerCase())
      ? parsed.href
      : "";
  } catch {
    return "";
  }
}

export function createTweetBatchSummary(
  addedCount,
  duplicateCount,
  backendFailures,
  localFailures,
) {
  return {
    addedCount: normalizeBatchCount(addedCount),
    duplicateCount: normalizeBatchCount(duplicateCount),
    failureCount:
      (Array.isArray(backendFailures) ? backendFailures.length : 0) +
      (Array.isArray(localFailures) ? localFailures.length : 0),
  };
}

function loadTweets() {
  legacyTweets = [];
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];

    const parsed = JSON.parse(raw);
    const normalized = Array.isArray(parsed) ? parsed.map(normalizeTweet).filter(Boolean) : [];
    legacyTweets = normalized.filter((tweet) => !isSupportedTweet(tweet));
    return normalized.filter(isSupportedTweet);
  } catch {
    return [];
  }
}

function normalizeTweet(tweet) {
  if (!tweet?.url) return null;
  let mediaType = normalizeMediaType(tweet.mediaType);
  const body = String(tweet.body || "");
  const mediaUrl = String(tweet.mediaUrl || "").trim();
  const mediaUrls = normalizeMediaUrls(tweet.mediaUrls, mediaUrl);
  const videoUrl = String(tweet.videoUrl || "").trim();
  if (!body.trim() && !mediaUrls.length && !videoUrl) return null;
  if (!body.trim() && mediaType === "text") {
    mediaType = videoUrl ? "video" : mediaUrls.length ? "image" : "text";
  }
  return {
    ...tweet,
    id: tweet.id || `tweet-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    authorName: tweet.authorName || "未命名作者",
    handle: normalizeHandle(tweet.handle || ""),
    body,
    mediaType,
    mediaUrl,
    mediaUrls,
    videoUrl,
    avatarUrl: String(tweet.avatarUrl || "").trim(),
    tags: Array.isArray(tweet.tags) ? tweet.tags : [],
    note: tweet.note || "",
    addedAt: tweet.addedAt || new Date().toISOString(),
    accent: tweet.accent || accentFor(mediaType),
    metadataSource: String(tweet.metadataSource || "").trim(),
    metadataFetchedAt: normalizeFetchedAt(tweet.metadataFetchedAt),
    metadataUrl: String(tweet.metadataUrl || "").trim(),
  };
}

function saveTweets(items) {
  const visibleIds = new Set(items.map((item) => item.id));
  const preservedLegacy = legacyTweets.filter((item) => !visibleIds.has(item.id));
  localStorage.setItem(STORAGE_KEY, JSON.stringify([...items, ...preservedLegacy]));
}

function isSupportedTweet(tweet) {
  return !validateTweetUrl(String(tweet?.url || "")).error;
}

function normalizeUrl(value) {
  if (!value) return "";
  return /^https?:\/\//i.test(value) ? value : `https://${value}`;
}

function validateTweetUrl(value) {
  const normalized = normalizeUrl(value);
  if (!normalized) return { url: "", error: "请输入 Twitter/X 推文链接。" };

  let parsed;
  try {
    parsed = new URL(normalized);
  } catch {
    return { url: "", error: "链接格式无效，请粘贴完整的 Twitter/X 推文链接。" };
  }

  if (!["http:", "https:"].includes(parsed.protocol)) {
    return { url: "", error: "链接必须使用 http 或 https。" };
  }

  const hostname = parsed.hostname.toLowerCase();
  const allowedHosts = ["x.com", "www.x.com", "twitter.com", "www.twitter.com"];
  if (
    !allowedHosts.includes(hostname) ||
    parsed.username ||
    parsed.password ||
    parsed.port ||
    parsed.hash
  ) {
    return { url: "", error: "仅支持 x.com 或 twitter.com 的推文链接，不能收藏其他平台内容。" };
  }

  const statusPath = /^\/[a-z0-9_]{1,15}\/status\/\d{1,19}(?:\/(?:photo|video)\/[1-9]\d*)?\/?$/i;
  if (!statusPath.test(parsed.pathname)) {
    return {
      url: "",
      error: "请使用标准推文链接，例如 https://x.com/账号/status/123456789。",
    };
  }

  parsed.protocol = "https:";
  parsed.hash = "";
  return { url: parsed.toString(), error: "" };
}

function extractTweetIdentity(value) {
  const text = String(value || "");
  const protocolMatch = text.match(
    /https?:\/\/(?:www\.)?(?:x\.com|twitter\.com)\/[a-z0-9_]{1,15}\/status\/\d{1,19}(?:\/(?:photo|video)\/[1-9]\d*)?(?:\?[^\s<>"']*)?/i,
  );
  const bareMatch = text.match(
    /(?:^|[\s([{"'“‘：])((?:www\.)?(?:x\.com|twitter\.com)\/[a-z0-9_]{1,15}\/status\/\d{1,19}(?:\/(?:photo|video)\/[1-9]\d*)?(?:\?[^\s<>"']*)?)/i,
  );
  const candidate = String(protocolMatch?.[0] || bareMatch?.[1] || "")
    .replace(/[\])},.;!?，。；！？、]+$/u, "")
    .trim();
  if (!candidate) return null;

  const result = validateTweetUrl(candidate);
  if (result.error) return null;
  const parsed = new URL(result.url);
  const match = parsed.pathname.match(/^\/([a-z0-9_]{1,15})\/status\/(\d{1,19})/i);
  if (!match) return null;
  const [, handle, statusId] = match;
  return {
    statusId,
    handle,
    url: result.url,
    canonicalUrl: "https://x.com/" + handle + "/status/" + statusId,
  };
}

function getTweetStatusId(value) {
  const result = validateTweetUrl(String(value || "").trim());
  if (result.error) return "";
  try {
    return new URL(result.url).pathname.match(/^\/[a-z0-9_]{1,15}\/status\/(\d{1,19})/i)?.[1] || "";
  } catch {
    return "";
  }
}

function getTweetSourceLabel(value) {
  try {
    const hostname = new URL(normalizeUrl(String(value || ""))).hostname.toLowerCase();
    if (hostname === "x.com" || hostname === "www.x.com") return "X";
    if (hostname === "twitter.com" || hostname === "www.twitter.com") return "Twitter";
  } catch {
    // Keep malformed legacy records visible instead of changing stored user data.
  }
  return "历史来源";
}

function normalizeHandle(value) {
  if (!value) return "";
  return value.startsWith("@") ? value : `@${value}`;
}

function normalizeMediaType(value) {
  if (value === "photo") return "image";
  return ["text", "image", "video"].includes(value) ? value : "text";
}

function normalizeMediaUrls(...values) {
  const urls = [];
  const append = (value) => {
    if (Array.isArray(value)) {
      value.forEach(append);
      return;
    }
    const url = String(value || "").trim();
    if (url && !urls.includes(url)) urls.push(url);
  };
  values.forEach(append);
  return urls;
}

function normalizeFetchedAt(value) {
  if (value === undefined || value === null || value === "") return "";
  const numericValue = Number(value);
  const date = Number.isFinite(numericValue)
    ? new Date(numericValue < 1_000_000_000_000 ? numericValue * 1000 : numericValue)
    : new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toISOString();
}

function formatMetadataTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value || "");
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function getPrimaryMediaUrl(tweet) {
  return String(tweet.mediaUrl || normalizeMediaUrls(tweet.mediaUrls)[0] || "").trim();
}

function getAuthorInitial(value) {
  const text = String(value || "?").replace(/^@/, "").trim();
  return (text[0] || "?").toUpperCase();
}

function splitList(value) {
  return value
    .split(/[,，\n]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function accentFor(mediaType) {
  if (mediaType === "image") return "#0f766e";
  if (mediaType === "video") return "#db2777";
  return "#2563eb";
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

window.handleTweetMediaError = function handleTweetMediaError(image) {
  image.hidden = true;
  image.parentElement?.classList.add("has-failed-image");
};
