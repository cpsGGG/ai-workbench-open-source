from __future__ import annotations

import copy
import ctypes
import hashlib
import gzip
import io
import ipaddress
import json
import math
import mimetypes
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zlib
from ctypes import wintypes
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from html import unescape
from html.parser import HTMLParser
from pathlib import Path

from paper_trends import (
    PAPER_TREND_LIKES_MAX_BODY,
    PaperTrendImageFetchError,
    PaperTrendImageNotFound,
    PaperTrendLikesReadError,
    PaperTrendLikesRevisionConflict,
    fetch_paper_trends,
    fetch_paper_trend_image,
    paper_trend_topics_payload,
    read_paper_trend_likes_state,
    public_paper_trends_payload,
    write_paper_trend_like_intent,
)

try:
    import fitz
except Exception:
    fitz = None

try:
    import zstandard
except Exception:
    zstandard = None

try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
except Exception:
    webdriver = None
    By = None


ROOT = Path(__file__).resolve().parent
CACHE_DIR = ROOT / ".cache" / "paper-thumbnails"
CODEX_HOME = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
CODEX_LOG_DB = CODEX_HOME / "logs_2.sqlite"
CODEX_SESSIONS_DIR = CODEX_HOME / "sessions"
CODEX_SESSION_INDEX = CODEX_HOME / "session_index.jsonl"
CHAT_HISTORY_CLAUDE_HOME = Path(os.environ.get("CLAUDE_HOME", Path.home() / ".claude"))
TOKEN_STATS_ANTIGRAVITY_HOME = Path(
    os.environ.get(
        "TOKEN_STATS_ANTIGRAVITY_HOME",
        os.environ.get("ANTIGRAVITY_HOME", Path.home() / ".gemini" / "antigravity"),
    )
)
TOKEN_STATS_DEEPSEEK_HOME = Path(
    os.environ.get(
        "TOKEN_STATS_DEEPSEEK_HOME",
        os.environ.get("DEEPSEEK_HOME", Path.home() / ".dsh"),
    )
)
TOKEN_STATS_WORKBUDDY_HOME = Path(
    os.environ.get("TOKEN_STATS_WORKBUDDY_HOME", Path.home() / ".workbuddy")
)
TOKEN_STATS_CLINE_CHINESE_HOME = Path(
    os.environ.get(
        "TOKEN_STATS_CLINE_CHINESE_HOME",
        Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        / "Code"
        / "User"
        / "globalStorage"
        / "hybridtalentcomputing.cline-chinese",
    )
)
TOKEN_STATS_CLINE_HOME = Path(
    os.environ.get(
        "TOKEN_STATS_CLINE_HOME",
        Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        / "Code"
        / "User"
        / "globalStorage"
        / "saoudrizwan.claude-dev",
    )
)
CHAT_HISTORY_CACHE_TTL = 60
CHAT_HISTORY_BODY_LIMIT = 64 * 1024
CHAT_HISTORY_TERMINAL_MODES = {
    "terminal",
    "codex",
    "codex-resume",
    "claude",
    "claude-resume",
}
CHAT_HISTORY_USAGE_KEYS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)
CHAT_HISTORY_CACHE: dict | None = None
CHAT_HISTORY_CACHE_BUILT_AT = 0.0
CHAT_HISTORY_SESSIONS_BY_KEY: dict[str, dict] = {}
CHAT_HISTORY_FOLDERS_BY_PATH: dict[str, dict] = {}
CHAT_HISTORY_BUILD_LOCK = threading.Lock()
TOKEN_STATS_TZ = timezone(timedelta(hours=8))
TOKEN_STATS_TIMEZONE = "Asia/Shanghai"
TOKEN_STATS_CACHE_TTL = 60
TOKEN_STATS_DETAILS_MAX_DAYS = 3660
TOKEN_STATS_CODEX_STATE_DB = CODEX_HOME / "state_5.sqlite"
TOKEN_STATS_CLAUDE_USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)
TOKEN_STATS_CACHE: dict | None = None
TOKEN_STATS_CACHE_BUILT_AT = 0.0
TOKEN_STATS_BUILD_LOCK = threading.Lock()
TOKEN_STATS_WORKBUDDY_BUILD_LOCK = threading.Lock()
TOKEN_STATS_DETAILS_CACHE: dict | None = None
TOKEN_STATS_DETAILS_CACHE_BUILT_AT = 0.0
TOKEN_STATS_DETAILS_BUILD_LOCK = threading.Lock()
TOKEN_STATS_DETAILS_CACHE_SIGNATURE: str | None = None
TOKEN_STATS_DETAILS_CACHE_REVISION = 0
# Payloads are shared as read-only objects. Incremental parsers clone their mutable
# rows/state before extending them, and cross-source normalization uses row copies.
TOKEN_STATS_DETAILS_PAYLOAD_CACHE: dict[tuple[str, str, str, str], dict] = {}
TOKEN_STATS_DETAILS_PAYLOAD_CACHE_LOCK = threading.Lock()
TOKEN_STATS_DETAILS_FILE_CACHE_VERSION = "2026-09-30-dsh-v4-cache-coverage-v2"
TOKEN_STATS_CODEX_SUMMARY_CACHE_SOURCE = "codex-summary-v2"
TOKEN_STATS_DETAILS_FILE_CACHE_DB = ROOT / ".cache" / "token-stats-details.sqlite"
TOKEN_STATS_CODEX_CONFIG = CODEX_HOME / "config.toml"
SKIP_DIRS = {".obsidian", ".git", ".trash", ".cache", "node_modules", "__pycache__"}
PAPER_INDEX: dict[str, dict] = {}
VIDEO_META_CACHE: dict[str, dict] = {}
TWEET_META_CACHE: dict[str, dict] = {}
TWEET_META_CACHE_TTL = 60 * 60
TWEET_META_ERROR_CACHE_TTL = 60
TWEET_CLIPBOARD_MAX_BYTES = 64 * 1024
TWEET_CLIPBOARD_TIMEOUT = 2.0
TWEET_INPUT_HOSTS = {
    "x.com",
    "www.x.com",
    "twitter.com",
    "www.twitter.com",
}
TWEET_STATUS_URL_RE = re.compile(
    r"https?://(?:www\.)?(?:x\.com|twitter\.com)/"
    r"[A-Za-z0-9_]{1,15}/status/[0-9]{1,19}"
    r"(?:/(?:photo|video)/[1-9][0-9]*)?/?"
    r"(?:\?[^\s<>\"'`]+)?",
    flags=re.IGNORECASE,
)
TWEET_UPSTREAM_HOSTS = {
    "api.fxtwitter.com",
    "publish.x.com",
    "publish.twitter.com",
}
TWEET_BOOKMARK_PROFILE_DIR = (
    Path(os.environ.get("LOCALAPPDATA") or Path.home())
    / "AIWorkbench"
    / "browser-profiles"
    / "x-bookmarks"
)
TWEET_BOOKMARK_IMPORT_MAX_ITEMS = 1000
TWEET_BOOKMARK_IMPORT_JOB_LIMIT = 20
TWEET_BOOKMARK_IMPORT_LOGIN_TIMEOUT = 5 * 60
TWEET_BOOKMARK_IMPORT_SCAN_TIMEOUT = 10 * 60
TWEET_BOOKMARK_IMPORT_SCAN_ROUNDS = 300
TWEET_BOOKMARK_IMPORT_IDLE_ROUNDS = 10
TWEET_BOOKMARK_IMPORT_SCROLL_DELAY = 1.2
TWEET_BOOKMARK_PROFILE_RELEASE_DELAY = 0.75
TWEET_BOOKMARK_IMPORT_ACTIVE_STATUSES = {
    "starting",
    "awaiting_login",
    "scanning",
    "enriching",
}
TWEET_BOOKMARK_IMPORT_JOBS: dict[str, dict] = {}
TWEET_BOOKMARK_IMPORT_LOCK = threading.RLock()
TWEET_BOOKMARK_DOM_SCRIPT = r"""
const absoluteUrl = (value) => {
  if (!value) return "";
  try { return new URL(value, window.location.origin).href; }
  catch (_) { return ""; }
};
return Array.from(document.querySelectorAll("article")).map((article) => {
  const time = article.querySelector('a[href*="/status/"] time');
  const statusLink = time && time.closest('a[href*="/status/"]');
  if (!statusLink) return null;
  const statusId = (statusLink.getAttribute("href") || "").match(/\/status\/(\d{1,19})/)?.[1] || "";
  const foreignStatusLinks = Array.from(article.querySelectorAll('a[href*="/status/"]'))
    .filter((link) => {
      const id = (link.getAttribute("href") || "").match(/\/status\/(\d{1,19})/)?.[1] || "";
      return id && id !== statusId;
    });
  const foreignBoundary = foreignStatusLinks[0] || null;
  const belongsToMainTweet = (node) => {
    if (!node || !statusId) return false;
    const statusAncestor = node.closest('a[href*="/status/"]');
    if (statusAncestor) {
      const ancestorStatusId = (statusAncestor.getAttribute("href") || "")
        .match(/\/status\/(\d{1,19})/)?.[1] || "";
      if (ancestorStatusId && ancestorStatusId !== statusId) return false;
    }
    const linkedContainer = node.closest('[role="link"]');
    if (linkedContainer && foreignStatusLinks.some((link) => linkedContainer.contains(link))) {
      return false;
    }
    if (foreignBoundary) {
      const relation = node.compareDocumentPosition(foreignBoundary);
      if (!(relation & Node.DOCUMENT_POSITION_FOLLOWING) && !node.contains(foreignBoundary)) {
        return false;
      }
    }
    return true;
  };
  const firstMain = (selector) => Array.from(article.querySelectorAll(selector))
    .find(belongsToMainTweet) || null;
  const text = firstMain('[data-testid="tweetText"]');
  const userName = article.querySelector('[data-testid="User-Name"]');
  const authorLink = userName && Array.from(userName.querySelectorAll('a[href^="/"]'))
    .find((link) => /^\/[A-Za-z0-9_]{1,15}\/?$/.test(link.getAttribute("href") || ""));
  const avatar = article.querySelector('[data-testid="Tweet-User-Avatar"] img')
    || article.querySelector('img[src*="profile_images"]');
  const imageUrls = Array.from(article.querySelectorAll('[data-testid="tweetPhoto"] img'))
    .filter(belongsToMainTweet)
    .map((image) => absoluteUrl(image.currentSrc || image.src));
  const videoPosterUrls = Array.from(article.querySelectorAll("video[poster]"))
    .filter(belongsToMainTweet)
    .map((video) => absoluteUrl(video.poster));
  return {
    href: absoluteUrl(statusLink.getAttribute("href")),
    body: text ? text.innerText : "",
    userName: userName ? userName.innerText : "",
    authorHref: authorLink ? absoluteUrl(authorLink.getAttribute("href")) : "",
    avatarUrl: avatar ? absoluteUrl(avatar.currentSrc || avatar.src) : "",
    imageUrls,
    videoPosterUrls,
  };
}).filter(Boolean);
"""
TWEET_BOOKMARK_PAGE_STATE_SCRIPT = r"""
const primary = document.querySelector('[data-testid="primaryColumn"]');
const articleCount = primary?.querySelectorAll('article').length || 0;
const emptyState = primary?.querySelector('[data-testid="emptyState"]');
const loading = primary?.querySelector('[role="progressbar"]');
const retry = document.querySelector(
  '[data-testid="primaryColumn"] [data-testid="retry"], '
  + '[data-testid="primaryColumn"] button[data-testid="retry"]'
);
const errorDetail = document.querySelector(
  '[data-testid="primaryColumn"] [data-testid="error-detail"], '
  + '[data-testid="primaryColumn"] [role="alert"]'
);
const retryByText = Array.from(primary?.querySelectorAll('button') || []).some((button) => {
  const label = (button.innerText || button.getAttribute('aria-label') || '').trim().toLowerCase();
  return /^(retry|try again|重试|再试一次)$/.test(label);
});
return {
  pageReady: Boolean(primary),
  articleCount,
  empty: Boolean(emptyState) && articleCount === 0,
  loading: Boolean(loading),
  retryError: Boolean(retry || errorDetail || retryByText),
};
"""
MODEL_PRICES_PER_1M = {
    # https://developers.openai.com/api/docs/models/gpt-6-astra
    # Verified 2026-09-05. Fast uses the existing priority tier alias.
    "gpt-6-astra": {
        "standard": {"input": 10.00, "cachedInput": 1.00, "cacheWrite": 12.50, "output": 50.00},
        "batch": {"input": 5.00, "cachedInput": 0.50, "cacheWrite": 6.25, "output": 25.00},
        "flex": {"input": 5.00, "cachedInput": 0.50, "cacheWrite": 6.25, "output": 25.00},
        "priority": {"input": 20.00, "cachedInput": 2.00, "cacheWrite": 25.00, "output": 100.00},
    },
    "gpt-5.6-sol": {
        "standard": {"input": 5.00, "cachedInput": 0.50, "output": 30.00},
        "batch": {"input": 2.50, "cachedInput": 0.25, "output": 15.00},
        "flex": {"input": 2.50, "cachedInput": 0.25, "output": 15.00},
        "priority": {"input": 10.00, "cachedInput": 1.00, "output": 60.00},
    },
    "gpt-5.6-terra": {
        "standard": {"input": 2.50, "cachedInput": 0.25, "output": 15.00},
        "batch": {"input": 1.25, "cachedInput": 0.125, "output": 7.50},
        "flex": {"input": 1.25, "cachedInput": 0.125, "output": 7.50},
        "priority": {"input": 5.00, "cachedInput": 0.50, "output": 30.00},
    },
    "gpt-5.6-luna": {
        "standard": {"input": 1.00, "cachedInput": 0.10, "output": 6.00},
        "batch": {"input": 0.50, "cachedInput": 0.05, "output": 3.00},
        "flex": {"input": 0.50, "cachedInput": 0.05, "output": 3.00},
        "priority": {"input": 2.00, "cachedInput": 0.20, "output": 12.00},
    },
    "gpt-5.5": {
        "standard": {"input": 5.00, "cachedInput": 0.50, "output": 30.00},
        "batch": {"input": 2.50, "cachedInput": 0.25, "output": 15.00},
        "flex": {"input": 2.50, "cachedInput": 0.25, "output": 15.00},
        "priority": {"input": 12.50, "cachedInput": 1.25, "output": 75.00},
    },
    "gpt-5.4": {
        "standard": {"input": 2.50, "cachedInput": 0.25, "output": 15.00},
        "batch": {"input": 1.25, "cachedInput": 0.13, "output": 7.50},
        "flex": {"input": 1.25, "cachedInput": 0.13, "output": 7.50},
        "priority": {"input": 5.00, "cachedInput": 0.50, "output": 30.00},
    },
    "gpt-5.4-mini": {
        "standard": {"input": 0.75, "cachedInput": 0.075, "output": 4.50},
        "batch": {"input": 0.375, "cachedInput": 0.0375, "output": 2.25},
        "flex": {"input": 0.375, "cachedInput": 0.0375, "output": 2.25},
        "priority": {"input": 1.50, "cachedInput": 0.15, "output": 9.00},
    },
    "gpt-5.3-codex": {
        "standard": {"input": 1.75, "cachedInput": 0.175, "output": 14.00},
        "priority": {"input": 3.50, "cachedInput": 0.35, "output": 28.00},
    },
}
MODEL_PRICE_SOURCE = {
    "name": "OpenAI API Pricing",
    "url": "https://developers.openai.com/api/docs/pricing",
    "checkedAt": "2026-07-12",
    "modelChecks": {
        "gpt-6-astra": {
            "checkedAt": "2026-09-05",
            "url": "https://developers.openai.com/api/docs/models/gpt-6-astra",
        },
    },
    "unit": "USD per 1M tokens",
}
VIDEO_META_ALLOWED_HOSTS = (
    "bilibili.com",
    "b23.tv",
    "youtube.com",
    "youtu.be",
    "douyin.com",
    "iesdouyin.com",
)
VIDEO_IMAGE_ALLOWED_HOSTS = VIDEO_META_ALLOWED_HOSTS + (
    "hdslb.com",
    "douyinpic.com",
    "douyinstatic.com",
    "ytimg.com",
    "googleusercontent.com",
)
YOUTUBE_PLAYLIST_IMPORT_MAX_ITEMS = 500
YOUTUBE_PLAYLIST_IMPORT_TIMEOUT = 45
VIDEO_STORAGE_DIR = (
    Path(os.environ.get("LOCALAPPDATA") or Path.home())
    / "AIWorkbench"
)
VIDEO_STORAGE_PATH = VIDEO_STORAGE_DIR / "videos.json"
VIDEO_STORAGE_MAX_BODY_BYTES = 5 * 1024 * 1024
VIDEO_STORAGE_MAX_RECORDS = 5000
VIDEO_STORAGE_LOCK = threading.RLock()
HEALTH_STORAGE_DIR = VIDEO_STORAGE_DIR
HEALTH_STORAGE_PATH = HEALTH_STORAGE_DIR / "health.json"
HEALTH_STORAGE_SCHEMA_VERSION = 1
HEALTH_STORAGE_MAX_BODY_BYTES = 2 * 1024 * 1024
HEALTH_STORAGE_MAX_RECORDS = 10_000
HEALTH_STORAGE_LOCK = threading.RLock()
SOCIAL_STORAGE_DIR = VIDEO_STORAGE_DIR
SOCIAL_STORAGE_PATH = SOCIAL_STORAGE_DIR / "social.json"
SOCIAL_MEDIA_DIR = SOCIAL_STORAGE_DIR / "social-media"
SOCIAL_STORAGE_SCHEMA_VERSION = 1
SOCIAL_STORAGE_MAX_BODY_BYTES = 12 * 1024 * 1024
SOCIAL_STORAGE_MAX_RECORDS = 10_000
SOCIAL_MEDIA_MAX_BYTES = 250 * 1024 * 1024
SOCIAL_STORAGE_LOCK = threading.RLock()
SOCIAL_MEDIA_LOCK = threading.RLock()
SOCIAL_MEDIA_CHUNK_BYTES = 256 * 1024
SOCIAL_MEDIA_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
}
ACCOUNTING_STORAGE_DIR = VIDEO_STORAGE_DIR
ACCOUNTING_STORAGE_PATH = ACCOUNTING_STORAGE_DIR / "accounting.json"
ACCOUNTING_AUTH_DIR = ACCOUNTING_STORAGE_DIR / "accounting-auth"
ACCOUNTING_SECRET_DIR = ACCOUNTING_STORAGE_DIR / "accounting-secrets"
ACCOUNTING_STORAGE_SCHEMA_VERSION = 2
ACCOUNTING_STORAGE_MAX_BODY_BYTES = 24 * 1024 * 1024
ACCOUNTING_STORAGE_MAX_RECORDS = 20_000
ACCOUNTING_AUTH_MAX_BYTES = 2 * 1024 * 1024
ACCOUNTING_CPA_MAX_CANDIDATES = 30
ACCOUNTING_CPA_RECENT_SECONDS = 6 * 60 * 60
ACCOUNTING_STORAGE_LOCK = threading.RLock()
ACCOUNTING_AUTH_LOCK = threading.RLock()
ACCOUNTING_CATEGORIES = {
    "正规代充",
    "日抛 Plus",
    "日抛 Team",
    "Claude",
    "其他",
}


class DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def accounting_protect_bytes(data: bytes) -> bytes:
    if os.name != "nt":
        return data
    buffer = ctypes.create_string_buffer(data)
    source = DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    target = DataBlob()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(source), "AIWorkbench accounting", None, None, None, 0x01, ctypes.byref(target)
    ):
        raise OSError("accounting_secret_encrypt_failed")
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(target.pbData)


def accounting_unprotect_bytes(data: bytes) -> bytes:
    if os.name != "nt":
        return data
    buffer = ctypes.create_string_buffer(data)
    source = DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    target = DataBlob()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None, 0x01, ctypes.byref(target)
    ):
        raise OSError("accounting_secret_decrypt_failed")
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(target.pbData)


def appdata_obsidian_config() -> Path:
    appdata = os.environ.get("APPDATA", "")
    return Path(appdata) / "obsidian" / "obsidian.json"


def load_vaults() -> list[dict]:
    config_path = appdata_obsidian_config()
    if not config_path.exists():
        return []

    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return []

    vaults = []
    for vault_id, item in config.get("vaults", {}).items():
        path = Path(item.get("path", "")).expanduser()
        if not path.exists():
            continue
        vaults.append(
            {
                "id": vault_id,
                "name": path.name,
                "path": str(path),
                "open": bool(item.get("open")),
                "ts": item.get("ts", 0),
            }
        )

    vaults.sort(key=lambda vault: (not vault["open"], -int(vault.get("ts") or 0)))
    return vaults


def scan_papers() -> list[dict]:
    global PAPER_INDEX
    papers = []
    index = {}

    for vault in load_vaults():
        vault_root = Path(vault["path"]).resolve()
        for pdf_path in walk_pdfs(vault_root):
            paper = build_paper(vault, vault_root, pdf_path)
            papers.append(paper)
            index[paper["id"]] = paper

    papers.sort(key=lambda item: (item["vaultOpen"] is not True, item["relativeDir"].lower(), item["title"].lower()))
    PAPER_INDEX = index
    return papers


def walk_pdfs(vault_root: Path):
    for current, dirs, files in os.walk(vault_root):
        dirs[:] = [name for name in dirs if name not in SKIP_DIRS and not name.startswith(".")]
        for name in files:
            if name.lower().endswith(".pdf"):
                yield Path(current) / name


def build_paper(vault: dict, vault_root: Path, pdf_path: Path) -> dict:
    resolved = pdf_path.resolve()
    relative_path = resolved.relative_to(vault_root).as_posix()
    title, venue, year = parse_pdf_name(resolved.stem)
    stat = resolved.stat()
    paper_id = hashlib.sha1(str(resolved).encode("utf-8")).hexdigest()[:16]
    note_path = find_matching_note(resolved)

    return {
        "id": paper_id,
        "title": title,
        "authors": "",
        "venue": venue,
        "year": year,
        "status": "unread",
        "tags": tags_from_path(relative_path),
        "abstract": "",
        "keyIdeas": [],
        "pdfUrl": f"/api/pdf?id={paper_id}",
        "thumbnailUrl": f"/api/thumbnail?id={paper_id}",
        "obsidianUrl": obsidian_url(vault["name"], relative_path),
        "obsidianVault": vault["name"],
        "obsidianPath": relative_path,
        "notePath": note_path,
        "absolutePath": str(resolved),
        "relativeDir": str(Path(relative_path).parent).replace(".", ""),
        "vaultPath": vault["path"],
        "vaultOpen": vault.get("open", False),
        "size": stat.st_size,
        "modifiedAt": stat.st_mtime,
        "accent": accent_for(relative_path),
    }


def parse_pdf_name(stem: str) -> tuple[str, str, str]:
    cleaned = re.sub(r"[_]+", " ", stem).strip()
    match = re.match(r"^([A-Za-z][A-Za-z\s]*?)(20\d{2}|19\d{2})\s*-\s*(.+)$", cleaned)
    if match:
        source = match.group(1).strip()
        year = match.group(2)
        title = match.group(3).strip()
        return title, f"{source} {year}", year

    match = re.match(r"^(20\d{2}|19\d{2})\s*-\s*(.+)$", cleaned)
    if match:
        year = match.group(1)
        return match.group(2).strip(), year, year

    return cleaned, "", ""


def tags_from_path(relative_path: str) -> list[str]:
    parts = [part for part in Path(relative_path).parts[:-1] if part and part != "."]
    return parts[-2:] if len(parts) > 2 else parts


def find_matching_note(pdf_path: Path) -> str:
    candidates = [pdf_path.with_suffix(".md"), Path(str(pdf_path) + ".md")]
    for candidate in candidates:
        if candidate.exists():
            return candidate.name
    return ""


def obsidian_url(vault_name: str, relative_path: str) -> str:
    query = urllib.parse.urlencode({"vault": vault_name, "file": relative_path})
    return f"obsidian://open?{query}"


def accent_for(value: str) -> str:
    colors = ["#3b82f6", "#14b8a6", "#f97316", "#a855f7", "#ef4444", "#22c55e"]
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()
    return colors[int(digest[:2], 16) % len(colors)]


class TweetOEmbedTextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_paragraph = False
        self.finished = False
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs):
        if tag.lower() == "p" and not self.finished:
            self.in_paragraph = True
        elif tag.lower() == "br" and self.in_paragraph:
            self.parts.append("\n")

    def handle_endtag(self, tag: str):
        if tag.lower() == "p" and self.in_paragraph:
            self.in_paragraph = False
            self.finished = True

    def handle_data(self, data: str):
        if self.in_paragraph:
            self.parts.append(data)

    def text(self) -> str:
        return clean_tweet_body("".join(self.parts))


class TweetAllowlistedRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urljoin(req.full_url, newurl)
        if not is_allowed_tweet_upstream_url(target):
            raise ValueError("redirected_to_unsupported_url")
        return super().redirect_request(req, fp, code, msg, headers, target)


def fetch_tweet_meta(url: str) -> dict:
    link = parse_tweet_url(url)
    if not link:
        return empty_tweet_meta((url or "").strip(), "invalid_tweet_url")

    cached = get_cached_tweet_meta(link["id"])
    if cached:
        return cached

    errors: list[Exception] = []
    try:
        data = fetch_fxtwitter_json(link["id"])
        payload = extract_fxtwitter_tweet_meta(data, link)
        if tweet_meta_has_content(payload):
            cache_tweet_meta(link["id"], payload)
            return payload
        errors.append(ValueError("metadata_not_found"))
    except Exception as exc:
        errors.append(exc)

    try:
        payload = fetch_tweet_oembed_meta(link)
        if tweet_meta_has_content(payload):
            cache_tweet_meta(link["id"], payload)
            return payload
        errors.append(ValueError("metadata_not_found"))
    except Exception as exc:
        errors.append(exc)

    payload = empty_tweet_meta(link["url"], tweet_error_code(errors))
    cache_tweet_meta(link["id"], payload, is_error=True)
    return payload


def parse_tweet_url(value: str) -> dict | None:
    value = (value or "").strip()
    if not value or any(character.isspace() for character in value):
        return None

    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError):
        return None

    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or host not in TWEET_INPUT_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.fragment
    ):
        return None

    match = re.fullmatch(
        r"/([A-Za-z0-9_]{1,15})/status/([0-9]{1,19})(?:/(?:photo|video)/[1-9][0-9]*)?/?",
        parsed.path,
    )
    if not match:
        return None

    handle, tweet_id = match.groups()
    return {
        "id": tweet_id,
        "handle": handle,
        "url": canonical_tweet_url(handle, tweet_id),
    }


def canonical_tweet_url(handle: str, tweet_id: str) -> str:
    return f"https://x.com/{handle}/status/{tweet_id}"


def tweet_clipboard_result(
    supported: bool,
    link: dict | None = None,
) -> dict:
    found = bool(link)
    return {
        "ok": True,
        "supported": supported,
        "found": found,
        "url": (
            canonical_tweet_url(link["handle"], link["id"])
            if link
            else ""
        ),
        "statusId": link["id"] if link else "",
    }


def extract_tweet_link_from_text(text: str) -> dict | None:
    if not isinstance(text, str) or not text:
        return None
    for match in TWEET_STATUS_URL_RE.finditer(text):
        link = parse_tweet_url(match.group(0))
        if link:
            return link
    return None


def read_tweet_clipboard() -> dict:
    if os.name != "nt":
        return tweet_clipboard_result(False)

    system_root = Path(os.environ.get("SystemRoot") or r"C:\Windows")
    powershell = system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if not powershell.is_file():
        return tweet_clipboard_result(False)

    script = (
        "$ErrorActionPreference = 'Stop'; "
        "$text = Get-Clipboard -Raw; "
        "if ($null -eq $text) { exit 0 }; "
        "$bytes = [System.Text.Encoding]::UTF8.GetBytes([string]$text); "
        f"$count = [Math]::Min([int]$bytes.Length, {TWEET_CLIPBOARD_MAX_BYTES}); "
        "$output = [Console]::OpenStandardOutput(); "
        "$output.Write($bytes, 0, $count); "
        "$output.Flush()"
    )
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    try:
        completed = subprocess.run(
            [
                str(powershell),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-STA",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=TWEET_CLIPBOARD_TIMEOUT,
            check=False,
            shell=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            startupinfo=startupinfo,
        )
    except Exception:
        return tweet_clipboard_result(False)
    if completed.returncode != 0:
        return tweet_clipboard_result(False)

    raw = (completed.stdout or b"")[:TWEET_CLIPBOARD_MAX_BYTES]
    text = raw.decode("utf-8", errors="replace")
    return tweet_clipboard_result(True, extract_tweet_link_from_text(text))


def empty_tweet_meta(url: str, error: str) -> dict:
    return {
        "ok": False,
        "url": url,
        "authorName": "",
        "handle": "",
        "body": "",
        "mediaType": "text",
        "mediaUrl": "",
        "mediaUrls": [],
        "videoUrl": "",
        "avatarUrl": "",
        "source": "",
        "fetchedAt": int(time.time()),
        "error": error,
    }


def get_cached_tweet_meta(tweet_id: str) -> dict | None:
    cached = TWEET_META_CACHE.get(tweet_id)
    if not cached:
        return None
    if cached.get("expiresAt", 0) <= time.monotonic():
        TWEET_META_CACHE.pop(tweet_id, None)
        return None
    return copy_tweet_meta_payload(cached["payload"])


def cache_tweet_meta(tweet_id: str, payload: dict, is_error: bool = False):
    ttl = TWEET_META_ERROR_CACHE_TTL if is_error else TWEET_META_CACHE_TTL
    TWEET_META_CACHE[tweet_id] = {
        "expiresAt": time.monotonic() + ttl,
        "payload": copy_tweet_meta_payload(payload),
    }


def copy_tweet_meta_payload(payload: dict) -> dict:
    copied = dict(payload)
    copied["mediaUrls"] = list(payload.get("mediaUrls") or [])
    return copied


def fetch_tweet_upstream_json(url: str) -> dict:
    if not is_allowed_tweet_upstream_url(url):
        raise ValueError("unsupported_upstream_url")

    request = urllib.request.Request(
        url,
        headers={
            **browser_headers(),
            "Accept": "application/json,text/plain,*/*",
        },
    )
    opener = urllib.request.build_opener(TweetAllowlistedRedirectHandler())
    with opener.open(request, timeout=8) as response:
        final_url = response.geturl()
        if not is_allowed_tweet_upstream_url(final_url):
            raise ValueError("redirected_to_unsupported_url")
        charset = response.headers.get_content_charset() or "utf-8"
        raw = response.read(2_000_001)
        encoding = (response.headers.get("Content-Encoding") or "").lower()

    if len(raw) > 2_000_000:
        raise ValueError("upstream_response_too_large")
    raw = decode_response_body(raw, encoding)
    if not raw:
        raise ValueError("empty_upstream_response")
    data = json.loads(raw.decode(charset, errors="replace"))
    if not isinstance(data, dict):
        raise ValueError("invalid_upstream_response")
    return data


def fetch_fxtwitter_json(tweet_id: str) -> dict:
    endpoint = f"https://api.fxtwitter.com/status/{tweet_id}"
    try:
        return fetch_tweet_upstream_json(endpoint)
    except Exception as exc:
        if not is_retryable_tweet_error(exc):
            raise
        time.sleep(0.2)
        return fetch_tweet_upstream_json(endpoint)


def is_retryable_tweet_error(error: Exception) -> bool:
    if isinstance(error, urllib.error.HTTPError):
        return error.code == 429 or 500 <= error.code < 600
    return isinstance(error, (urllib.error.URLError, TimeoutError, json.JSONDecodeError))


def is_allowed_tweet_upstream_url(url: str) -> bool:
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except (TypeError, ValueError):
        return False

    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme.lower() != "https"
        or host not in TWEET_UPSTREAM_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.fragment
    ):
        return False

    if host == "api.fxtwitter.com":
        return bool(re.fullmatch(r"/status/[0-9]{1,19}", parsed.path)) and not parsed.query
    if host in {"publish.twitter.com", "publish.x.com"}:
        return parsed.path == "/oembed"
    return False


def extract_fxtwitter_tweet_meta(data: dict, link: dict) -> dict:
    tweet = data.get("tweet")
    if not isinstance(tweet, dict):
        raise ValueError("tweet_not_found")

    author = tweet.get("author") if isinstance(tweet.get("author"), dict) else {}
    handle_name = clean_tweet_handle(author.get("screen_name")) or link["handle"]
    author_name = clean_meta_value(str(author.get("name") or ""))
    media_type, media_url, media_urls, video_url = extract_fxtwitter_media(tweet.get("media"))
    return {
        "ok": True,
        "url": canonical_tweet_url(handle_name, link["id"]),
        "authorName": author_name,
        "handle": f"@{handle_name}" if handle_name else "",
        "body": clean_tweet_body(tweet.get("text") or tweet.get("raw_text")),
        "mediaType": media_type,
        "mediaUrl": media_url,
        "mediaUrls": media_urls,
        "videoUrl": video_url,
        "avatarUrl": safe_tweet_asset_url(author.get("avatar_url"), ("pbs.twimg.com",)),
        "source": "fxtwitter",
        "fetchedAt": int(time.time()),
    }


def extract_fxtwitter_media(media_value) -> tuple[str, str, list[str], str]:
    media = media_value if isinstance(media_value, dict) else {}
    items = media.get("all") if isinstance(media.get("all"), list) else []
    if not items:
        items = [
            item
            for key in ("photos", "videos")
            for item in (media.get(key) if isinstance(media.get(key), list) else [])
        ]

    previews: list[str] = []
    video_url = ""
    has_image = False
    has_video = False
    for item in items:
        if not isinstance(item, dict):
            continue
        source_type = str(item.get("type") or "").lower()
        if source_type == "photo":
            has_image = True
            preview = safe_tweet_asset_url(item.get("url"), ("pbs.twimg.com",))
        elif source_type in {"video", "gif", "animated_gif"}:
            has_video = True
            preview = safe_tweet_asset_url(item.get("thumbnail_url"), ("pbs.twimg.com",))
            if not video_url:
                video_url = best_tweet_video_url(item)
        else:
            continue
        if preview and preview not in previews:
            previews.append(preview)

    media_type = "video" if has_video else "image" if has_image else "text"
    return media_type, previews[0] if previews else "", previews, video_url


def best_tweet_video_url(item: dict) -> str:
    direct = safe_tweet_asset_url(item.get("url"), ("video.twimg.com",))
    if direct:
        return direct

    candidates = []
    for key in ("formats", "variants"):
        values = item.get(key) if isinstance(item.get(key), list) else []
        for value in values:
            if not isinstance(value, dict):
                continue
            url = safe_tweet_asset_url(value.get("url"), ("video.twimg.com",))
            if not url:
                continue
            media_format = str(value.get("container") or value.get("content_type") or "").lower()
            is_mp4 = "mp4" in media_format or ".mp4" in urllib.parse.urlsplit(url).path.lower()
            try:
                bitrate = int(value.get("bitrate") or 0)
            except (TypeError, ValueError):
                bitrate = 0
            candidates.append((is_mp4, bitrate, url))
    return max(candidates, default=(False, 0, ""))[2]


def fetch_tweet_oembed_meta(link: dict) -> dict:
    endpoint = "https://publish.x.com/oembed?" + urllib.parse.urlencode(
        {
            "url": link["url"],
            "omit_script": "true",
            "dnt": "true",
        }
    )
    data = fetch_tweet_upstream_json(endpoint)
    author_name = clean_meta_value(str(data.get("author_name") or ""))
    handle_name = tweet_handle_from_profile_url(data.get("author_url")) or link["handle"]
    parser = TweetOEmbedTextParser()
    parser.feed(str(data.get("html") or ""))
    parser.close()
    return {
        "ok": True,
        "url": canonical_tweet_url(handle_name, link["id"]),
        "authorName": author_name,
        "handle": f"@{handle_name}" if handle_name else "",
        "body": parser.text(),
        "mediaType": "text",
        "mediaUrl": "",
        "mediaUrls": [],
        "videoUrl": "",
        "avatarUrl": "",
        "source": "x-publish-oembed",
        "fetchedAt": int(time.time()),
    }


def tweet_handle_from_profile_url(value) -> str:
    try:
        parsed = urllib.parse.urlsplit(str(value or ""))
    except ValueError:
        return ""
    if (parsed.hostname or "").lower() not in TWEET_INPUT_HOSTS:
        return ""
    parts = [part for part in parsed.path.split("/") if part]
    return clean_tweet_handle(parts[0]) if len(parts) == 1 else ""


def clean_tweet_handle(value) -> str:
    handle = str(value or "").strip().lstrip("@")
    return handle if re.fullmatch(r"[A-Za-z0-9_]{1,15}", handle) else ""


def clean_tweet_body(value) -> str:
    if isinstance(value, dict):
        value = value.get("text") or ""
    body = unescape(str(value or "")).replace("\r\n", "\n").replace("\r", "\n").strip()
    return body[:10_000]


def safe_tweet_asset_url(value, allowed_hosts: tuple[str, ...]) -> str:
    url = ensure_https(str(value or "").strip())
    return url if is_allowed_external_url(url, allowed_hosts) else ""


def tweet_meta_has_content(payload: dict) -> bool:
    return bool(payload.get("body") or payload.get("mediaUrls") or payload.get("videoUrl"))


def tweet_error_code(errors: list[Exception]) -> str:
    for error in errors:
        if isinstance(error, urllib.error.HTTPError) and error.code in {404, 410, 422}:
            return "tweet_not_found"
        if str(error) == "tweet_not_found":
            return "tweet_not_found"
    for error in errors:
        reason = error.reason if isinstance(error, urllib.error.URLError) else error
        if isinstance(reason, TimeoutError) or "timed out" in str(reason).lower():
            return "upstream_timeout"
    return "metadata_fetch_failed"


class TweetBookmarkImportFailure(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def parse_tweet_bookmarks_url(value: str) -> dict | None:
    value = (value or "").strip()
    if not value or any(character.isspace() for character in value):
        return None

    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError):
        return None

    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or host not in TWEET_INPUT_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.fragment
    ):
        return None

    match = re.fullmatch(r"/i/bookmarks(?:/([0-9]{1,30}))?/?", parsed.path)
    if not match:
        return None
    folder_id = match.group(1) or ""
    canonical_path = "/i/bookmarks" + (f"/{folder_id}" if folder_id else "")
    return {
        "url": f"https://x.com{canonical_path}",
        "folderId": folder_id,
    }


def validate_tweet_bookmark_import_payload(payload: dict) -> tuple[str, int | None]:
    unknown_fields = set(payload) - {"url", "maxItems"}
    if unknown_fields:
        raise ValueError("Unsupported request field.")

    raw_url = payload.get("url")
    if not isinstance(raw_url, str):
        raise ValueError("A valid X bookmarks URL is required.")
    parsed_url = parse_tweet_bookmarks_url(raw_url)
    if not parsed_url:
        raise ValueError("URL must be an X or Twitter bookmarks page.")

    max_items = payload.get("maxItems")
    if max_items is None:
        return parsed_url["url"], None
    if isinstance(max_items, bool) or not isinstance(max_items, int):
        raise ValueError("maxItems must be an integer between 1 and 1000.")
    if not 1 <= max_items <= TWEET_BOOKMARK_IMPORT_MAX_ITEMS:
        raise ValueError("maxItems must be an integer between 1 and 1000.")
    return parsed_url["url"], max_items


def tweet_bookmark_handle_from_dom(raw: dict) -> str:
    author_href = str(raw.get("authorHref") or "")
    handle = tweet_handle_from_profile_url(author_href)
    if handle:
        return handle
    for line in str(raw.get("userName") or "").splitlines():
        match = re.fullmatch(r"@([A-Za-z0-9_]{1,15})", line.strip())
        if match:
            return match.group(1)
    return ""


def tweet_bookmark_author_name_from_dom(value) -> str:
    for line in str(value or "").splitlines():
        candidate = line.strip()
        if candidate and not candidate.startswith("@") and candidate != "\u00b7":
            return clean_meta_value(candidate)
    return ""


def normalize_tweet_bookmark_dom_item(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    href = str(raw.get("href") or "").strip()
    link = parse_tweet_url(href)
    if not link:
        try:
            parsed = urllib.parse.urlsplit(href)
            port = parsed.port
        except (TypeError, ValueError):
            return None
        fallback_match = re.fullmatch(r"/i/web/status/([0-9]{1,19})/?", parsed.path)
        handle = tweet_bookmark_handle_from_dom(raw)
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or (parsed.hostname or "").lower() not in TWEET_INPUT_HOSTS
            or parsed.username is not None
            or parsed.password is not None
            or port is not None
            or parsed.fragment
            or not fallback_match
            or not handle
        ):
            return None
        tweet_id = fallback_match.group(1)
        link = {
            "id": tweet_id,
            "handle": handle,
            "url": canonical_tweet_url(handle, tweet_id),
        }

    image_urls = []
    for value in raw.get("imageUrls") if isinstance(raw.get("imageUrls"), list) else []:
        url = safe_tweet_asset_url(value, ("pbs.twimg.com",))
        if url and url not in image_urls:
            image_urls.append(url)
    video_posters = []
    for value in (
        raw.get("videoPosterUrls")
        if isinstance(raw.get("videoPosterUrls"), list)
        else []
    ):
        url = safe_tweet_asset_url(value, ("pbs.twimg.com",))
        if url and url not in image_urls and url not in video_posters:
            video_posters.append(url)

    media_urls = image_urls + video_posters
    body = clean_tweet_body(raw.get("body"))
    has_content = bool(body or media_urls)
    metadata = {
        "ok": has_content,
        "url": link["url"],
        "authorName": tweet_bookmark_author_name_from_dom(raw.get("userName")),
        "handle": f'@{link["handle"]}',
        "body": body,
        "mediaType": "video" if video_posters else "image" if image_urls else "text",
        "mediaUrl": media_urls[0] if media_urls else "",
        "mediaUrls": media_urls,
        "videoUrl": "",
        "avatarUrl": safe_tweet_asset_url(raw.get("avatarUrl"), ("pbs.twimg.com",)),
        "source": "x-bookmarks-dom",
        "fetchedAt": int(time.time()),
    }
    if not has_content:
        metadata["error"] = "metadata_not_found"
    return {
        "url": link["url"],
        "statusId": link["id"],
        "metadata": metadata,
    }


def merge_tweet_bookmark_dom_items(existing: dict, incoming: dict) -> dict:
    merged = {
        "url": existing["url"],
        "statusId": existing["statusId"],
        "metadata": copy_tweet_meta_payload(existing["metadata"]),
    }
    current = merged["metadata"]
    candidate = incoming["metadata"]
    if len(candidate.get("body") or "") > len(current.get("body") or ""):
        current["body"] = candidate["body"]
    for key in ("authorName", "handle", "avatarUrl"):
        if not current.get(key) and candidate.get(key):
            current[key] = candidate[key]
    media_urls = list(current.get("mediaUrls") or [])
    for url in candidate.get("mediaUrls") or []:
        if url not in media_urls:
            media_urls.append(url)
    current["mediaUrls"] = media_urls
    current["mediaUrl"] = media_urls[0] if media_urls else ""
    if current.get("mediaType") != "video" and candidate.get("mediaType") == "video":
        current["mediaType"] = "video"
    elif current.get("mediaType") == "text" and media_urls:
        current["mediaType"] = "image"
    current["ok"] = bool(current.get("body") or media_urls)
    if current["ok"]:
        current.pop("error", None)
    return merged


def copy_tweet_bookmark_import_item(item: dict) -> dict:
    return {
        "url": str(item.get("url") or ""),
        "statusId": str(item.get("statusId") or ""),
        "metadata": copy_tweet_meta_payload(item.get("metadata") or {}),
    }


def tweet_bookmark_import_job_snapshot(job_id: str) -> dict | None:
    with TWEET_BOOKMARK_IMPORT_LOCK:
        job = TWEET_BOOKMARK_IMPORT_JOBS.get(job_id)
        if not job:
            return None
        return {
            "ok": True,
            "jobId": job_id,
            "requestUrl": job["requestUrl"],
            "maxItems": job["maxItems"],
            "status": job["status"],
            "message": job["message"],
            "foundCount": job["foundCount"],
            "processedCount": job["processedCount"],
            "items": [copy_tweet_bookmark_import_item(item) for item in job["items"]],
            "failures": [dict(failure) for failure in job["failures"]],
            "truncated": bool(job.get("truncated")),
            "truncationReason": str(job.get("truncationReason") or ""),
        }


def update_tweet_bookmark_import_job(job_id: str, **updates):
    with TWEET_BOOKMARK_IMPORT_LOCK:
        job = TWEET_BOOKMARK_IMPORT_JOBS.get(job_id)
        if not job:
            return
        for key, value in updates.items():
            if key == "items":
                job[key] = [copy_tweet_bookmark_import_item(item) for item in value]
            elif key == "failures":
                job[key] = [dict(failure) for failure in value]
            elif key in {
                "status",
                "message",
                "foundCount",
                "processedCount",
                "truncated",
                "truncationReason",
            }:
                job[key] = value


def prune_tweet_bookmark_import_jobs_locked():
    while len(TWEET_BOOKMARK_IMPORT_JOBS) > TWEET_BOOKMARK_IMPORT_JOB_LIMIT:
        removable = next(
            (
                job_id
                for job_id, job in TWEET_BOOKMARK_IMPORT_JOBS.items()
                if job.get("status") not in TWEET_BOOKMARK_IMPORT_ACTIVE_STATUSES
            ),
            None,
        )
        if not removable:
            return
        TWEET_BOOKMARK_IMPORT_JOBS.pop(removable, None)


def create_tweet_bookmark_import_job(
    url: str,
    max_items: int | None,
) -> tuple[dict | None, dict | None]:
    with TWEET_BOOKMARK_IMPORT_LOCK:
        active = next(
            (
                (job_id, job)
                for job_id, job in TWEET_BOOKMARK_IMPORT_JOBS.items()
                if job.get("status") in TWEET_BOOKMARK_IMPORT_ACTIVE_STATUSES
            ),
            None,
        )
        if active:
            return None, {
                "jobId": active[0],
                "requestUrl": active[1]["requestUrl"],
                "maxItems": active[1]["maxItems"],
                "status": active[1]["status"],
            }

        job_id = uuid.uuid4().hex
        TWEET_BOOKMARK_IMPORT_JOBS[job_id] = {
            "requestUrl": url,
            "maxItems": max_items,
            "status": "starting",
            "message": "Starting Edge for bookmark import.",
            "foundCount": 0,
            "processedCount": 0,
            "items": [],
            "failures": [],
            "truncated": False,
            "truncationReason": "",
        }
        prune_tweet_bookmark_import_jobs_locked()

    thread = threading.Thread(
        target=run_tweet_bookmark_import_job,
        args=(job_id, url, max_items),
        name=f"tweet-bookmark-import-{job_id[:8]}",
        daemon=True,
    )
    try:
        thread.start()
    except Exception:
        update_tweet_bookmark_import_job(
            job_id,
            status="error",
            message="Unable to start the bookmark import task.",
        )
    snapshot = tweet_bookmark_import_job_snapshot(job_id)
    return {
        "jobId": job_id,
        "requestUrl": url,
        "maxItems": max_items,
        "status": snapshot["status"] if snapshot else "error",
    }, None


def create_tweet_bookmark_edge_driver(headless: bool):
    if webdriver is None or By is None:
        raise TweetBookmarkImportFailure("selenium_unavailable")
    try:
        TWEET_BOOKMARK_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        options = webdriver.EdgeOptions()
        options.page_load_strategy = "eager"
        options.add_argument(f"--user-data-dir={TWEET_BOOKMARK_PROFILE_DIR}")
        options.add_argument("--profile-directory=Default")
        options.add_argument("--window-size=1440,1200")
        options.add_argument("--disable-notifications")
        options.add_argument("--no-first-run")
        if headless:
            options.add_argument("--headless=new")
        driver = webdriver.Edge(options=options)
        driver.set_page_load_timeout(45)
        return driver
    except TweetBookmarkImportFailure:
        raise
    except Exception as exc:
        raise TweetBookmarkImportFailure("browser_start_failed") from exc


def quit_tweet_bookmark_edge_driver(driver):
    if driver is None:
        return
    try:
        driver.quit()
    except Exception:
        pass


def open_tweet_bookmarks_page(driver, url: str):
    try:
        driver.get(url)
    except Exception as exc:
        raise TweetBookmarkImportFailure("browser_navigation_failed") from exc


def tweet_bookmarks_login_state(driver) -> bool | None:
    try:
        path = urllib.parse.urlsplit(driver.current_url or "").path.rstrip("/") or "/"
    except Exception:
        return None
    if path in {"/i/jf/onboarding", "/i/flow/login", "/login", "/account/access"}:
        return True

    try:
        authenticated = driver.find_elements(
            By.CSS_SELECTOR,
            '[data-testid="SideNav_AccountSwitcher_Button"], '
            '[data-testid="AppTabBar_Profile_Link"], nav a[href="/i/bookmarks"]',
        )
        if authenticated:
            return False
        unauthenticated = driver.find_elements(
            By.CSS_SELECTOR,
            'input[autocomplete="username"], [data-testid="loginButton"], '
            'a[href="/login"], a[href="/i/flow/login"]',
        )
        if unauthenticated:
            return True
        if path.startswith("/i/bookmarks") and driver.find_elements(By.CSS_SELECTOR, "article"):
            return False
    except Exception:
        return None
    return None


def wait_for_tweet_bookmarks_login_state(driver, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = tweet_bookmarks_login_state(driver)
        if state is not None:
            return state
        time.sleep(0.5)
    return True


def wait_for_interactive_tweet_login(driver, url: str):
    open_tweet_bookmarks_page(driver, url)
    deadline = time.monotonic() + TWEET_BOOKMARK_IMPORT_LOGIN_TIMEOUT
    while time.monotonic() < deadline:
        if tweet_bookmarks_login_state(driver) is False:
            open_tweet_bookmarks_page(driver, url)
            time.sleep(2.0)
            return
        time.sleep(1.0)
    raise TweetBookmarkImportFailure("login_timeout")


def scan_tweet_bookmarks(
    driver,
    job_id: str,
    max_items: int | None,
) -> tuple[list[dict], bool, str]:
    seen: dict[str, dict] = {}
    idle_rounds = 0
    started_at = time.monotonic()
    truncated = False
    truncation_reason = ""
    confirmed_empty = False
    item_limit = max_items or TWEET_BOOKMARK_IMPORT_MAX_ITEMS

    for _ in range(TWEET_BOOKMARK_IMPORT_SCAN_ROUNDS):
        if time.monotonic() - started_at >= TWEET_BOOKMARK_IMPORT_SCAN_TIMEOUT:
            truncated = True
            truncation_reason = "scan_timeout"
            break
        try:
            raw_items = driver.execute_script(TWEET_BOOKMARK_DOM_SCRIPT) or []
            page_state = driver.execute_script(TWEET_BOOKMARK_PAGE_STATE_SCRIPT) or {}
        except Exception as exc:
            raise TweetBookmarkImportFailure("scan_failed") from exc
        if not isinstance(page_state, dict):
            page_state = {}
        if page_state.get("retryError"):
            raise TweetBookmarkImportFailure("scan_failed")
        confirmed_empty = bool(page_state.get("empty"))
        before = len(seen)
        if isinstance(raw_items, list):
            for raw in raw_items:
                item = normalize_tweet_bookmark_dom_item(raw)
                if not item:
                    continue
                status_id = item["statusId"]
                if status_id in seen:
                    seen[status_id] = merge_tweet_bookmark_dom_items(seen[status_id], item)
                elif len(seen) < item_limit:
                    seen[status_id] = item

        items = list(seen.values())
        update_tweet_bookmark_import_job(
            job_id,
            message=f"Scanning bookmarks ({len(items)} found).",
            foundCount=len(items),
            items=items,
        )
        if len(items) >= item_limit:
            if max_items is None:
                truncated = True
                truncation_reason = "safety_limit"
            break
        waiting_for_page = bool(page_state.get("loading")) or not bool(page_state.get("pageReady"))
        idle_rounds = idle_rounds + 1 if len(seen) == before and not waiting_for_page else 0
        if idle_rounds >= TWEET_BOOKMARK_IMPORT_IDLE_ROUNDS:
            break
        try:
            driver.execute_script(
                "window.scrollBy({top: Math.max(window.innerHeight * 0.85, 900), "
                "left: 0, behavior: 'instant'});"
            )
        except Exception as exc:
            raise TweetBookmarkImportFailure("scan_failed") from exc
        time.sleep(TWEET_BOOKMARK_IMPORT_SCROLL_DELAY)
    else:
        truncated = True
        truncation_reason = "scan_round_limit"
    if not seen and not confirmed_empty:
        raise TweetBookmarkImportFailure("scan_failed")
    return list(seen.values()), truncated, truncation_reason


def enrich_tweet_bookmark_items(job_id: str, items: list[dict]) -> tuple[list[dict], list[dict]]:
    """Finalize DOM results without disclosing private bookmark URLs upstream."""
    enriched = [copy_tweet_bookmark_import_item(item) for item in items]
    failures = []
    for processed, item in enumerate(enriched, start=1):
        metadata = item.get("metadata") or {}
        if not tweet_meta_has_content(metadata):
            metadata["ok"] = False
            metadata["error"] = "metadata_not_found"
            failures.append({"url": item["url"], "error": "metadata_not_found"})
        item["metadata"] = metadata
        update_tweet_bookmark_import_job(
            job_id,
            message=f"Finalizing local bookmark data ({processed}/{len(enriched)}).",
            processedCount=processed,
        )
    return enriched, failures


def tweet_bookmark_import_error_message(code: str) -> str:
    messages = {
        "selenium_unavailable": "Bookmark import is unavailable because Selenium is not installed.",
        "browser_start_failed": "Unable to start Edge for bookmark import.",
        "browser_navigation_failed": "Unable to open the X bookmarks page.",
        "login_timeout": "Login was not completed within 5 minutes.",
        "scan_failed": "Unable to read bookmarks from the X page.",
    }
    return messages.get(code, "Bookmark import failed.")


def run_tweet_bookmark_import_job(job_id: str, url: str, max_items: int | None):
    driver = None
    error_message = ""
    try:
        update_tweet_bookmark_import_job(
            job_id,
            status="starting",
            message="Checking the saved X login in headless Edge.",
        )
        driver = create_tweet_bookmark_edge_driver(headless=True)
        open_tweet_bookmarks_page(driver, url)
        needs_login = wait_for_tweet_bookmarks_login_state(driver, timeout=20.0)
        if needs_login:
            quit_tweet_bookmark_edge_driver(driver)
            driver = None
            time.sleep(TWEET_BOOKMARK_PROFILE_RELEASE_DELAY)
            update_tweet_bookmark_import_job(
                job_id,
                status="awaiting_login",
                message="Sign in to X in the opened Edge window to continue.",
            )
            driver = create_tweet_bookmark_edge_driver(headless=False)
            wait_for_interactive_tweet_login(driver, url)

        update_tweet_bookmark_import_job(
            job_id,
            status="scanning",
            message="Scanning bookmarks.",
        )
        items, truncated, truncation_reason = scan_tweet_bookmarks(
            driver,
            job_id,
            max_items,
        )
        quit_tweet_bookmark_edge_driver(driver)
        driver = None

        update_tweet_bookmark_import_job(
            job_id,
            status="enriching",
            message="Finalizing bookmark data locally.",
            foundCount=len(items),
            processedCount=0,
            items=items,
            truncated=truncated,
            truncationReason=truncation_reason,
        )
        items, failures = enrich_tweet_bookmark_items(job_id, items)
        update_tweet_bookmark_import_job(
            job_id,
            status="complete",
            message="Bookmark import complete.",
            foundCount=len(items),
            processedCount=len(items),
            items=items,
            failures=failures,
            truncated=truncated,
            truncationReason=truncation_reason,
        )
    except TweetBookmarkImportFailure as exc:
        error_message = tweet_bookmark_import_error_message(exc.code)
    except Exception:
        error_message = "Bookmark import failed."
    finally:
        quit_tweet_bookmark_edge_driver(driver)
        if error_message:
            update_tweet_bookmark_import_job(
                job_id,
                status="error",
                message=error_message,
            )


class VideoStorageRevisionConflict(Exception):
    def __init__(self, state: dict):
        super().__init__("revision_conflict")
        self.state = state


class VideoStorageReadError(Exception):
    pass


def empty_video_storage_state() -> dict:
    return {"ok": True, "initialized": False, "videos": [], "revision": 0, "updatedAt": ""}


def read_video_storage_state() -> dict:
    with VIDEO_STORAGE_LOCK:
        if not VIDEO_STORAGE_PATH.exists():
            return empty_video_storage_state()
        try:
            raw = VIDEO_STORAGE_PATH.read_bytes()
            if len(raw) > VIDEO_STORAGE_MAX_BODY_BYTES:
                raise VideoStorageReadError("video_storage_read_failed")
            payload = json.loads(raw.decode("utf-8"))
        except VideoStorageReadError:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise VideoStorageReadError("video_storage_read_failed") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("videos"), list):
            raise VideoStorageReadError("video_storage_read_failed")
        videos = payload["videos"]
        if len(videos) > VIDEO_STORAGE_MAX_RECORDS or any(not isinstance(item, dict) for item in videos):
            raise VideoStorageReadError("video_storage_read_failed")
        try:
            revision = max(0, int(payload.get("revision") or 0))
        except (TypeError, ValueError):
            raise VideoStorageReadError("video_storage_read_failed")
        return {
            "ok": True,
            "initialized": True,
            "videos": videos,
            "revision": revision,
            "updatedAt": str(payload.get("updatedAt") or ""),
        }


def normalize_video_storage_url(value: str) -> str:
    value = (value or "").strip()
    try:
        parsed = urllib.parse.urlparse(value)
    except Exception:
        return value
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return value
    scheme = parsed.scheme.lower()
    host = parsed.hostname.lower().rstrip(".")
    port = parsed.port
    netloc = host if not port or (scheme == "https" and port == 443) or (scheme == "http" and port == 80) else f"{host}:{port}"
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    query_items = [
        (key, value)
        for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}
    ]
    query = urllib.parse.urlencode(sorted(query_items))
    return urllib.parse.urlunparse((scheme, netloc, path, "", query, ""))


def video_storage_record_key(record: dict) -> str:
    youtube_id = str(record.get("youtubeVideoId") or "").strip()
    url = str(record.get("url") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", youtube_id):
        youtube_id = extract_youtube_id(url)
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", youtube_id):
        return f"youtube:{youtube_id}"
    normalized_url = normalize_video_storage_url(url)
    if normalized_url:
        return f"url:{normalized_url}"
    record_id = str(record.get("id") or "").strip()
    return f"id:{record_id}" if record_id else ""


def merge_video_storage_record(primary: dict, fallback: dict) -> dict:
    merged = copy.deepcopy(primary)
    for key, value in fallback.items():
        if key not in merged or merged[key] is None or merged[key] == "":
            merged[key] = copy.deepcopy(value)
    for key in ("tags", "sourcePlaylists"):
        primary_values = merged.get(key) if isinstance(merged.get(key), list) else []
        fallback_values = fallback.get(key) if isinstance(fallback.get(key), list) else []
        if primary_values or fallback_values:
            merged[key] = list(dict.fromkeys(
                str(item).strip()
                for item in [*primary_values, *fallback_values]
                if str(item).strip()
            ))
    return merged


def validate_video_storage_records(value) -> list[dict]:
    if not isinstance(value, list):
        raise ValueError("videos_must_be_an_array")
    if len(value) > VIDEO_STORAGE_MAX_RECORDS:
        raise ValueError("too_many_video_records")
    field_limits = {
        "id": 512,
        "youtubeVideoId": 64,
        "url": 4096,
        "title": 2000,
        "author": 1000,
        "authorUrl": 4096,
        "thumbnailUrl": 4096,
        "description": 20_000,
    }
    records: list[dict] = []
    by_key: dict[str, int] = {}
    for raw_record in value:
        if not isinstance(raw_record, dict):
            raise ValueError("video_record_must_be_an_object")
        for field, limit in field_limits.items():
            field_value = raw_record.get(field)
            if field_value is not None and not isinstance(field_value, str):
                raise ValueError(f"invalid_video_field:{field}")
            if isinstance(field_value, str) and len(field_value) > limit:
                raise ValueError(f"video_field_too_long:{field}")
        record = copy.deepcopy(raw_record)
        key = video_storage_record_key(record)
        if not key:
            raise ValueError("video_record_requires_id_or_url")
        existing_index = by_key.get(key)
        if existing_index is None:
            by_key[key] = len(records)
            records.append(record)
        else:
            records[existing_index] = merge_video_storage_record(records[existing_index], record)
    return records


def write_video_storage_state(videos, base_revision) -> dict:
    records = validate_video_storage_records(videos)
    if not isinstance(base_revision, int) or isinstance(base_revision, bool) or base_revision < 0:
        raise ValueError("invalid_base_revision")
    with VIDEO_STORAGE_LOCK:
        current = read_video_storage_state()
        if base_revision != current["revision"]:
            raise VideoStorageRevisionConflict(current)
        state = {
            "ok": True,
            "initialized": True,
            "videos": records,
            "revision": current["revision"] + 1,
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        }
        body = json.dumps(state, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(body) > VIDEO_STORAGE_MAX_BODY_BYTES:
            raise ValueError("video_storage_too_large")
        VIDEO_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
        temporary = VIDEO_STORAGE_PATH.with_name(f".{VIDEO_STORAGE_PATH.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as output:
                output.write(body)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, VIDEO_STORAGE_PATH)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        return state


class HealthStorageRevisionConflict(Exception):
    def __init__(self, state: dict):
        super().__init__("revision_conflict")
        self.state = state


class HealthStorageReadError(Exception):
    pass


def empty_health_storage_state() -> dict:
    return {
        "ok": True,
        "initialized": False,
        "schemaVersion": HEALTH_STORAGE_SCHEMA_VERSION,
        "profile": {},
        "entries": [],
        "revision": 0,
        "updatedAt": "",
    }


def normalize_health_storage_json(value, path: str, depth: int = 0):
    if depth > 8:
        raise ValueError(f"health_value_too_deep:{path}")
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"invalid_health_number:{path}")
        return value
    if isinstance(value, str):
        if len(value) > 50_000:
            raise ValueError(f"health_string_too_long:{path}")
        return value
    if isinstance(value, list):
        if len(value) > 10_000:
            raise ValueError(f"health_array_too_large:{path}")
        return [
            normalize_health_storage_json(item, f"{path}[{index}]", depth + 1)
            for index, item in enumerate(value)
        ]
    if isinstance(value, dict):
        if len(value) > 500:
            raise ValueError(f"health_object_too_large:{path}")
        normalized = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 128:
                raise ValueError(f"invalid_health_field:{path}")
            normalized[key] = normalize_health_storage_json(item, f"{path}.{key}", depth + 1)
        return normalized
    raise ValueError(f"invalid_health_value:{path}")


def validate_health_storage_profile(value) -> dict:
    if not isinstance(value, dict):
        raise ValueError("profile_must_be_an_object")
    return normalize_health_storage_json(value, "profile")


def validate_health_storage_entries(value) -> list[dict]:
    if not isinstance(value, list):
        raise ValueError("entries_must_be_an_array")
    if len(value) > HEALTH_STORAGE_MAX_RECORDS:
        raise ValueError("too_many_health_entries")

    records_by_date: dict[str, dict] = {}
    for index, raw_record in enumerate(value):
        if not isinstance(raw_record, dict):
            raise ValueError("health_entry_must_be_an_object")
        raw_date = raw_record.get("date")
        if not isinstance(raw_date, str):
            raise ValueError("health_entry_requires_date")
        entry_date = raw_date.strip()
        try:
            parsed_date = date.fromisoformat(entry_date)
        except ValueError as exc:
            raise ValueError("invalid_health_entry_date") from exc
        if parsed_date.isoformat() != entry_date:
            raise ValueError("invalid_health_entry_date")

        record = normalize_health_storage_json(raw_record, f"entries[{index}]")
        record["date"] = entry_date
        previous = records_by_date.get(entry_date)
        if previous is None:
            records_by_date[entry_date] = record
        else:
            records_by_date[entry_date] = {**previous, **record}

    return [records_by_date[key] for key in sorted(records_by_date)]


def read_health_storage_state() -> dict:
    with HEALTH_STORAGE_LOCK:
        if not HEALTH_STORAGE_PATH.exists():
            return empty_health_storage_state()
        try:
            raw = HEALTH_STORAGE_PATH.read_bytes()
            if len(raw) > HEALTH_STORAGE_MAX_BODY_BYTES:
                raise HealthStorageReadError("health_storage_read_failed")
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                raise HealthStorageReadError("health_storage_read_failed")
            schema_version = payload.get("schemaVersion", HEALTH_STORAGE_SCHEMA_VERSION)
            if (
                not isinstance(schema_version, int)
                or isinstance(schema_version, bool)
                or schema_version != HEALTH_STORAGE_SCHEMA_VERSION
            ):
                raise HealthStorageReadError("health_storage_read_failed")
            profile = validate_health_storage_profile(payload.get("profile", {}))
            entries = validate_health_storage_entries(payload.get("entries"))
            revision = payload.get("revision")
            if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
                raise HealthStorageReadError("health_storage_read_failed")
            updated_at = payload.get("updatedAt", "")
            if not isinstance(updated_at, str) or len(updated_at) > 128:
                raise HealthStorageReadError("health_storage_read_failed")
        except HealthStorageReadError:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise HealthStorageReadError("health_storage_read_failed") from exc
        return {
            "ok": True,
            "initialized": True,
            "schemaVersion": HEALTH_STORAGE_SCHEMA_VERSION,
            "profile": profile,
            "entries": entries,
            "revision": revision,
            "updatedAt": updated_at,
        }


def write_health_storage_state(profile, entries, base_revision) -> dict:
    normalized_profile = validate_health_storage_profile(profile)
    normalized_entries = validate_health_storage_entries(entries)
    if not isinstance(base_revision, int) or isinstance(base_revision, bool) or base_revision < 0:
        raise ValueError("invalid_base_revision")

    with HEALTH_STORAGE_LOCK:
        current = read_health_storage_state()
        if base_revision != current["revision"]:
            raise HealthStorageRevisionConflict(current)
        state = {
            "ok": True,
            "initialized": True,
            "schemaVersion": HEALTH_STORAGE_SCHEMA_VERSION,
            "profile": normalized_profile,
            "entries": normalized_entries,
            "revision": current["revision"] + 1,
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        }
        body = json.dumps(state, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(body) > HEALTH_STORAGE_MAX_BODY_BYTES:
            raise ValueError("health_storage_too_large")
        HEALTH_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
        temporary = HEALTH_STORAGE_PATH.with_name(f".{HEALTH_STORAGE_PATH.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as output:
                output.write(body)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, HEALTH_STORAGE_PATH)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        return state


class SocialStorageRevisionConflict(Exception):
    def __init__(self, state: dict):
        super().__init__("revision_conflict")
        self.state = state


class SocialStorageReadError(Exception):
    pass


def empty_social_storage_state() -> dict:
    return {
        "ok": True,
        "initialized": False,
        "schemaVersion": SOCIAL_STORAGE_SCHEMA_VERSION,
        "entries": [],
        "revision": 0,
        "updatedAt": "",
    }


def social_storage_datetime(value, field: str, required: bool = True) -> str:
    if value in (None, "") and not required:
        return ""
    if not isinstance(value, str) or len(value) > 128:
        raise ValueError(f"invalid_social_{field}")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid_social_{field}") from exc
    return value


def social_storage_occurred_on(value, created_at: str) -> str:
    if value in (None, ""):
        try:
            created_datetime = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            if created_datetime.tzinfo is not None:
                created_datetime = created_datetime.astimezone()
            return created_datetime.date().isoformat()
        except ValueError as exc:
            raise ValueError("invalid_social_occurred_on") from exc
    if not isinstance(value, str):
        raise ValueError("invalid_social_occurred_on")
    try:
        occurred_on = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("invalid_social_occurred_on") from exc
    if occurred_on.isoformat() != value:
        raise ValueError("invalid_social_occurred_on")
    return value


def validate_social_storage_entries(value) -> list[dict]:
    if not isinstance(value, list):
        raise ValueError("entries_must_be_an_array")
    if len(value) > SOCIAL_STORAGE_MAX_RECORDS:
        raise ValueError("too_many_social_entries")
    records_by_id: dict[str, dict] = {}
    for index, raw in enumerate(value):
        if not isinstance(raw, dict):
            raise ValueError("social_entry_must_be_an_object")
        record_id = raw.get("id")
        if not isinstance(record_id, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", record_id):
            raise ValueError("invalid_social_entry_id")
        body = raw.get("body", "")
        if not isinstance(body, str) or len(body) > 100_000:
            raise ValueError("invalid_social_entry_body")
        attachments = raw.get("attachments", [])
        if not isinstance(attachments, list) or len(attachments) > 50:
            raise ValueError("invalid_social_attachments")
        normalized_attachments = []
        attachment_ids = set()
        for attachment in attachments:
            if not isinstance(attachment, dict):
                raise ValueError("invalid_social_attachment")
            attachment_id = attachment.get("id")
            if (
                not isinstance(attachment_id, str)
                or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", attachment_id)
                or attachment_id in attachment_ids
            ):
                raise ValueError("invalid_social_attachment_id")
            attachment_ids.add(attachment_id)
            name = attachment.get("name", "")
            media_type = attachment.get("type", "")
            mime_type = attachment.get("mimeType", "")
            url = attachment.get("url", "")
            if not isinstance(name, str) or len(name) > 512:
                raise ValueError("invalid_social_attachment_name")
            if media_type not in {"image", "video"}:
                raise ValueError("invalid_social_attachment_type")
            if not isinstance(mime_type, str) or len(mime_type) > 128:
                raise ValueError("invalid_social_attachment_mime")
            if not isinstance(url, str) or len(url) > 2048:
                raise ValueError("invalid_social_attachment_url")
            if url and not re.fullmatch(r"/api/social-media/[0-9a-f]{64}", url):
                raise ValueError("invalid_social_attachment_url")
            size = attachment.get("size", 0)
            if not isinstance(size, int) or isinstance(size, bool) or size < 0 or size > SOCIAL_MEDIA_MAX_BYTES:
                raise ValueError("invalid_social_attachment_size")
            normalized_attachments.append({
                "id": attachment_id,
                "name": name,
                "type": media_type,
                "mimeType": mime_type,
                "size": size,
                "url": url,
            })
        created_at = social_storage_datetime(raw.get("createdAt"), "created_at")
        record = {
            "id": record_id,
            "body": body,
            "occurredOn": social_storage_occurred_on(raw.get("occurredOn"), created_at),
            "attachments": normalized_attachments,
            "createdAt": created_at,
            "updatedAt": social_storage_datetime(raw.get("updatedAt"), "updated_at"),
            "deletedAt": social_storage_datetime(raw.get("deletedAt"), "deleted_at", required=False),
        }
        previous = records_by_id.get(record_id)
        if previous is None or record["updatedAt"] >= previous["updatedAt"]:
            records_by_id[record_id] = record
    return sorted(
        records_by_id.values(),
        key=lambda item: (item["occurredOn"], item["createdAt"], item["id"]),
        reverse=True,
    )


def read_social_storage_state() -> dict:
    with SOCIAL_STORAGE_LOCK:
        if not SOCIAL_STORAGE_PATH.exists():
            return empty_social_storage_state()
        try:
            raw = SOCIAL_STORAGE_PATH.read_bytes()
            if len(raw) > SOCIAL_STORAGE_MAX_BODY_BYTES:
                raise SocialStorageReadError("social_storage_read_failed")
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict) or payload.get("schemaVersion") != SOCIAL_STORAGE_SCHEMA_VERSION:
                raise SocialStorageReadError("social_storage_read_failed")
            entries = validate_social_storage_entries(payload.get("entries"))
            revision = payload.get("revision")
            if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
                raise SocialStorageReadError("social_storage_read_failed")
            updated_at = payload.get("updatedAt", "")
            if not isinstance(updated_at, str) or len(updated_at) > 128:
                raise SocialStorageReadError("social_storage_read_failed")
        except SocialStorageReadError:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise SocialStorageReadError("social_storage_read_failed") from exc
        return {
            "ok": True,
            "initialized": True,
            "schemaVersion": SOCIAL_STORAGE_SCHEMA_VERSION,
            "entries": entries,
            "revision": revision,
            "updatedAt": updated_at,
        }


def write_social_storage_state(entries, base_revision) -> dict:
    normalized_entries = validate_social_storage_entries(entries)
    if not isinstance(base_revision, int) or isinstance(base_revision, bool) or base_revision < 0:
        raise ValueError("invalid_base_revision")
    with SOCIAL_STORAGE_LOCK:
        current = read_social_storage_state()
        if base_revision != current["revision"]:
            raise SocialStorageRevisionConflict(current)
        state = {
            "ok": True,
            "initialized": True,
            "schemaVersion": SOCIAL_STORAGE_SCHEMA_VERSION,
            "entries": normalized_entries,
            "revision": current["revision"] + 1,
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        }
        body = json.dumps(state, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(body) > SOCIAL_STORAGE_MAX_BODY_BYTES:
            raise ValueError("social_storage_too_large")
        SOCIAL_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
        temporary = SOCIAL_STORAGE_PATH.with_name(f".{SOCIAL_STORAGE_PATH.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as output:
                output.write(body)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, SOCIAL_STORAGE_PATH)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        return state


class AccountingStorageRevisionConflict(Exception):
    def __init__(self, state: dict):
        super().__init__("revision_conflict")
        self.state = state


class AccountingStorageReadError(Exception):
    pass


def empty_accounting_storage_state() -> dict:
    return {
        "ok": True,
        "initialized": False,
        "schemaVersion": ACCOUNTING_STORAGE_SCHEMA_VERSION,
        "entries": [],
        "revision": 0,
        "updatedAt": "",
    }


def accounting_storage_datetime(value, field: str, required: bool = True) -> str:
    if value in (None, "") and not required:
        return ""
    if not isinstance(value, str) or len(value) > 128:
        raise ValueError(f"invalid_accounting_{field}")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid_accounting_{field}") from exc
    return value


def accounting_storage_date(value, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"invalid_accounting_{field}")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"invalid_accounting_{field}") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"invalid_accounting_{field}")
    return value


def accounting_storage_money(value, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"invalid_accounting_{field}")
    if value < 0 or value > 100_000_000_000:
        raise ValueError(f"invalid_accounting_{field}")
    return value


def validate_accounting_storage_entries(value) -> list[dict]:
    if not isinstance(value, list):
        raise ValueError("entries_must_be_an_array")
    if len(value) > ACCOUNTING_STORAGE_MAX_RECORDS:
        raise ValueError("too_many_accounting_entries")
    records_by_id: dict[str, dict] = {}
    for raw in value:
        if not isinstance(raw, dict):
            raise ValueError("accounting_entry_must_be_an_object")
        record_id = raw.get("id")
        if not isinstance(record_id, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", record_id):
            raise ValueError("invalid_accounting_entry_id")
        category = raw.get("category")
        if category not in ACCOUNTING_CATEGORIES:
            raise ValueError("invalid_accounting_category")
        quantity = raw.get("quantity")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1 or quantity > 1_000_000:
            raise ValueError("invalid_accounting_quantity")
        has_credential = raw.get("hasCredential", False)
        if not isinstance(has_credential, bool):
            raise ValueError("invalid_accounting_has_credential")
        text_limits = {
            "merchant": 1_000,
            "note": 20_000,
        }
        text_fields = {}
        for field, limit in text_limits.items():
            field_value = raw.get(field, "")
            if not isinstance(field_value, str) or len(field_value) > limit:
                raise ValueError(f"invalid_accounting_{field}")
            text_fields[field] = field_value
        auth_files = raw.get("authFiles", [])
        if not isinstance(auth_files, list) or len(auth_files) > 20:
            raise ValueError("invalid_accounting_auth_files")
        normalized_files = []
        file_ids = set()
        for item in auth_files:
            if not isinstance(item, dict):
                raise ValueError("invalid_accounting_auth_file")
            file_id = item.get("id")
            name = item.get("name")
            url = item.get("url")
            size = item.get("size")
            if (
                not isinstance(file_id, str)
                or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", file_id)
                or file_id in file_ids
            ):
                raise ValueError("invalid_accounting_auth_file_id")
            if not isinstance(name, str) or not name or len(name) > 512:
                raise ValueError("invalid_accounting_auth_file_name")
            if not isinstance(url, str) or not re.fullmatch(r"/api/accounting-auth/[0-9a-f]{64}", url):
                raise ValueError("invalid_accounting_auth_file_url")
            if isinstance(size, bool) or not isinstance(size, int) or size <= 0 or size > ACCOUNTING_AUTH_MAX_BYTES:
                raise ValueError("invalid_accounting_auth_file_size")
            file_ids.add(file_id)
            normalized_files.append({"id": file_id, "name": name, "size": size, "url": url})
        import_fingerprint = raw.get("importFingerprint", "")
        if not isinstance(import_fingerprint, str) or (
            import_fingerprint and not re.fullmatch(r"[0-9a-f]{64}", import_fingerprint)
        ):
            raise ValueError("invalid_accounting_import_fingerprint")
        created_at = accounting_storage_datetime(raw.get("createdAt"), "created_at")
        updated_at = accounting_storage_datetime(raw.get("updatedAt"), "updated_at")
        record = {
            "id": record_id,
            "purchasedOn": accounting_storage_date(raw.get("purchasedOn"), "purchased_on"),
            "category": category,
            "quantity": quantity,
            "unitPriceCents": accounting_storage_money(raw.get("unitPriceCents"), "unit_price"),
            "totalPriceCents": accounting_storage_money(raw.get("totalPriceCents"), "total_price"),
            **text_fields,
            "hasCredential": has_credential,
            "authFiles": normalized_files,
            "importFingerprint": import_fingerprint,
            "createdAt": created_at,
            "updatedAt": updated_at,
            "deletedAt": accounting_storage_datetime(raw.get("deletedAt"), "deleted_at", required=False),
        }
        previous = records_by_id.get(record_id)
        if previous is None or updated_at >= previous["updatedAt"]:
            records_by_id[record_id] = record
    records = sorted(
        records_by_id.values(),
        key=lambda item: (item["purchasedOn"], item["createdAt"], item["id"]),
        reverse=True,
    )
    active_auth_urls: set[str] = set()
    for record in records:
        if record["deletedAt"]:
            continue
        for item in record["authFiles"]:
            if item["url"] in active_auth_urls:
                raise ValueError("accounting_auth_already_bound")
            active_auth_urls.add(item["url"])
    return records


def read_accounting_storage_state() -> dict:
    with ACCOUNTING_STORAGE_LOCK:
        if not ACCOUNTING_STORAGE_PATH.exists():
            return empty_accounting_storage_state()
        try:
            raw = ACCOUNTING_STORAGE_PATH.read_bytes()
            if len(raw) > ACCOUNTING_STORAGE_MAX_BODY_BYTES:
                raise AccountingStorageReadError("accounting_storage_read_failed")
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict) or payload.get("schemaVersion") != ACCOUNTING_STORAGE_SCHEMA_VERSION:
                raise AccountingStorageReadError("accounting_storage_read_failed")
            entries = validate_accounting_storage_entries(payload.get("entries"))
            revision = payload.get("revision")
            if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
                raise AccountingStorageReadError("accounting_storage_read_failed")
            updated_at = payload.get("updatedAt", "")
            if not isinstance(updated_at, str) or len(updated_at) > 128:
                raise AccountingStorageReadError("accounting_storage_read_failed")
        except AccountingStorageReadError:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise AccountingStorageReadError("accounting_storage_read_failed") from exc
        return {
            "ok": True,
            "initialized": True,
            "schemaVersion": ACCOUNTING_STORAGE_SCHEMA_VERSION,
            "entries": entries,
            "revision": revision,
            "updatedAt": updated_at,
        }


def write_accounting_storage_state(entries, base_revision) -> dict:
    normalized_entries = validate_accounting_storage_entries(entries)
    if not isinstance(base_revision, int) or isinstance(base_revision, bool) or base_revision < 0:
        raise ValueError("invalid_base_revision")
    with ACCOUNTING_STORAGE_LOCK:
        current = read_accounting_storage_state()
        if base_revision != current["revision"]:
            raise AccountingStorageRevisionConflict(current)
        state = {
            "ok": True,
            "initialized": True,
            "schemaVersion": ACCOUNTING_STORAGE_SCHEMA_VERSION,
            "entries": normalized_entries,
            "revision": current["revision"] + 1,
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        }
        body = json.dumps(state, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(body) > ACCOUNTING_STORAGE_MAX_BODY_BYTES:
            raise ValueError("accounting_storage_too_large")
        ACCOUNTING_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
        temporary = ACCOUNTING_STORAGE_PATH.with_name(f".{ACCOUNTING_STORAGE_PATH.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as output:
                output.write(body)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, ACCOUNTING_STORAGE_PATH)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        active_ids = {item["id"] for item in normalized_entries if not item["deletedAt"]}
        deleted_ids = {item["id"] for item in normalized_entries if item["deletedAt"]}
        for record_id in deleted_ids - active_ids:
            try:
                accounting_secret_path(record_id).unlink(missing_ok=True)
            except OSError:
                pass
        active_auth_urls = {
            item["url"]
            for record in normalized_entries
            if not record["deletedAt"]
            for item in record["authFiles"]
        }
        deleted_auth_urls = {
            item["url"]
            for record in normalized_entries
            if record["deletedAt"]
            for item in record["authFiles"]
        }
        for url in deleted_auth_urls - active_auth_urls:
            digest = url.removeprefix("/api/accounting-auth/")
            try:
                (ACCOUNTING_AUTH_DIR / f"{digest}.json.dpapi").unlink(missing_ok=True)
            except OSError:
                pass
        return state


def store_accounting_auth_stream(source, content_length: int) -> dict:
    if content_length <= 0:
        raise ValueError("empty_accounting_auth")
    if content_length > ACCOUNTING_AUTH_MAX_BYTES:
        raise ValueError("accounting_auth_too_large")
    remaining = content_length
    chunks = bytearray()
    while remaining:
        chunk = source.read(min(SOCIAL_MEDIA_CHUNK_BYTES, remaining))
        if not chunk:
            raise ValueError("Request body is incomplete.")
        chunks.extend(chunk)
        remaining -= len(chunk)
    return store_accounting_auth_bytes(bytes(chunks))


def store_accounting_auth_bytes(data: bytes) -> dict:
    content_length = len(data)
    if content_length <= 0:
        raise ValueError("empty_accounting_auth")
    if content_length > ACCOUNTING_AUTH_MAX_BYTES:
        raise ValueError("accounting_auth_too_large")
    try:
        parsed = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid_accounting_auth_json") from exc
    if not isinstance(parsed, (dict, list)):
        raise ValueError("invalid_accounting_auth_json")

    # The content digest gives manual uploads and CPA imports one shared,
    # deterministic encrypted blob instead of accumulating duplicates.
    file_id = hashlib.sha256(data).hexdigest()
    ACCOUNTING_AUTH_DIR.mkdir(parents=True, exist_ok=True)
    target = ACCOUNTING_AUTH_DIR / f"{file_id}.json.dpapi"
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    with ACCOUNTING_AUTH_LOCK:
        if target.is_file():
            return {"ok": True, "url": f"/api/accounting-auth/{file_id}", "size": content_length}
        encrypted = accounting_protect_bytes(data)
        try:
            with temporary.open("xb") as output:
                output.write(encrypted)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    return {"ok": True, "url": f"/api/accounting-auth/{file_id}", "size": content_length}


def accounting_cpa_running_config_paths() -> list[Path]:
    """Read config arguments only from a running CLIProxyAPI process."""
    if os.name != "nt":
        return []
    script = (
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.Name -match 'cli-proxy-api' } | "
        "Select-Object ExecutablePath,CommandLine | ConvertTo-Json -Compress"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        payload = json.loads(result.stdout or "[]")
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return []
    records = payload if isinstance(payload, list) else [payload]
    configs: list[Path] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        command_line = record.get("CommandLine")
        executable = record.get("ExecutablePath")
        if not isinstance(command_line, str):
            continue
        match = re.search(r"(?:^|\s)--config(?:=|\s+)(?:\"([^\"]+)\"|'([^']+)'|(\S+))", command_line)
        if not match:
            continue
        raw_path = next((value for value in match.groups() if value), "")
        path = Path(raw_path)
        if not path.is_absolute() and isinstance(executable, str) and executable:
            path = Path(executable).parent / path
        configs.append(path)
    return configs


def accounting_cpa_config_paths() -> list[Path]:
    configured = os.environ.get("AI_WORKBENCH_CPA_CONFIG", "").strip()
    candidate_groups = []
    if configured:
        candidate_groups.append([Path(configured)])
    running = accounting_cpa_running_config_paths()
    if running:
        candidate_groups.append(running)
    candidate_groups.append([
            ROOT.parent / "token_free" / "CLIProxyAPI-main" / "config.yaml",
            ROOT.parent / "CLIProxyAPI-main" / "config.yaml",
            ROOT / "CLIProxyAPI-main" / "config.yaml",
    ])
    # Use exactly one CPA installation. Mixing a running instance with stale
    # fallback installs can surface an auth file from the wrong account pool.
    for candidates in candidate_groups:
        for path in candidates:
            try:
                resolved = path.resolve(strict=True)
            except (OSError, RuntimeError):
                continue
            if resolved.is_file() and not accounting_path_is_reparse(resolved):
                return [resolved]
    return []


def accounting_path_is_reparse(path: Path) -> bool:
    try:
        stat = path.lstat()
    except OSError:
        return True
    return path.is_symlink() or bool(getattr(stat, "st_file_attributes", 0) & 0x400)


def accounting_cpa_auth_root(config_path: Path) -> Path | None:
    if accounting_path_is_reparse(config_path):
        return None
    try:
        text = config_path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return None
    match = re.search(r"(?mi)^\s*auth-dir\s*:\s*([^#\r\n]+?)\s*$", text)
    if not match:
        return None
    raw = match.group(1).strip().strip("\"'")
    if not raw or "\x00" in raw:
        return None
    config_parent = config_path.parent.resolve()
    requested = Path(raw)
    if not requested.is_absolute():
        requested = config_parent / requested
    try:
        root = requested.resolve(strict=True)
        root.relative_to(config_parent)
    except (OSError, RuntimeError, ValueError):
        return None
    if not root.is_dir() or accounting_path_is_reparse(root):
        return None
    return root


def accounting_cpa_auth_roots() -> list[Path]:
    roots: list[Path] = []
    seen: set[str] = set()
    for config_path in accounting_cpa_config_paths():
        root = accounting_cpa_auth_root(config_path)
        if root is None:
            continue
        key = os.path.normcase(str(root))
        if key not in seen:
            seen.add(key)
            roots.append(root)
    return roots


def accounting_cpa_display_name(name: str, content_sha256: str) -> str:
    normalized = name.lower()
    if "team" in normalized:
        kind = "Codex Team"
    elif "plus" in normalized:
        kind = "Codex Plus"
    elif "k12" in normalized:
        kind = "Codex K12"
    else:
        kind = "Codex auth"
    return f"{kind} · 标识 {content_sha256[:6]}"


def accounting_cpa_candidate(path: Path, auth_root: Path) -> dict | None:
    name = path.name
    if (
        not re.fullmatch(r"codex-[^\\/]{1,480}\.json", name, flags=re.IGNORECASE)
        or ".cpa." in name.lower()
        or name.lower() == "cpa-1-accounts.json"
        or accounting_path_is_reparse(path)
    ):
        return None
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(auth_root)
        if resolved.parent != auth_root or not resolved.is_file():
            return None
        before = resolved.stat()
        if before.st_size <= 0 or before.st_size > ACCOUNTING_AUTH_MAX_BYTES:
            return None
        with resolved.open("rb") as source:
            handle_before = os.fstat(source.fileno())
            data = source.read(ACCOUNTING_AUTH_MAX_BYTES + 1)
            handle_after = os.fstat(source.fileno())
        after = resolved.stat()
    except (OSError, RuntimeError, ValueError):
        return None
    if (
        accounting_path_is_reparse(path)
        or before.st_dev != handle_before.st_dev
        or before.st_ino != handle_before.st_ino
        or handle_before.st_dev != handle_after.st_dev
        or handle_before.st_ino != handle_after.st_ino
        or handle_before.st_size != handle_after.st_size
        or handle_before.st_mtime_ns != handle_after.st_mtime_ns
        or handle_after.st_dev != after.st_dev
        or handle_after.st_ino != after.st_ino
        or handle_after.st_size != after.st_size
        or handle_after.st_mtime_ns != after.st_mtime_ns
        or len(data) != after.st_size
    ):
        return None
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or str(payload.get("type", "")).lower() != "codex":
        return None
    if not isinstance(payload.get("access_token"), str) or not payload["access_token"]:
        return None
    if not isinstance(payload.get("refresh_token"), str) or not payload["refresh_token"]:
        return None
    content_sha256 = hashlib.sha256(data).hexdigest()
    binding = "\0".join(
        [os.path.normcase(str(auth_root)), name, str(after.st_size), str(after.st_mtime_ns), content_sha256]
    )
    candidate_id = hashlib.sha256(("accounting-cpa-auth-v1\0" + binding).encode("utf-8")).hexdigest()
    modified_at = datetime.fromtimestamp(after.st_mtime, timezone.utc).isoformat()
    return {
        "id": candidate_id,
        "name": accounting_cpa_display_name(name, content_sha256),
        "source": "CPA auths",
        "modifiedAt": modified_at,
        "mtimeNs": after.st_mtime_ns,
        "createdNs": getattr(after, "st_ctime_ns", after.st_mtime_ns),
        "size": after.st_size,
        "_path": resolved,
        "_root": auth_root,
        "_sha256": content_sha256,
        "_data": data,
    }


def scan_accounting_cpa_auth_candidates() -> list[dict]:
    candidates: list[dict] = []
    for auth_root in accounting_cpa_auth_roots():
        try:
            paths = list(auth_root.iterdir())
        except OSError:
            continue
        safe_paths = []
        for path in paths:
            try:
                stat = path.lstat()
            except OSError:
                continue
            if (
                path.is_file()
                and not accounting_path_is_reparse(path)
                and 0 < stat.st_size <= ACCOUNTING_AUTH_MAX_BYTES
                and re.fullmatch(r"codex-[^\\/]{1,480}\.json", path.name, flags=re.IGNORECASE)
                and ".cpa." not in path.name.lower()
            ):
                safe_paths.append((min(getattr(stat, "st_ctime_ns", stat.st_mtime_ns), stat.st_mtime_ns), path))
        safe_paths.sort(key=lambda item: item[0], reverse=True)
        for _, path in safe_paths[: ACCOUNTING_CPA_MAX_CANDIDATES * 2]:
            candidate = accounting_cpa_candidate(path, auth_root)
            if candidate is not None:
                candidates.append(candidate)
    candidates.sort(key=lambda item: (min(item["createdNs"], item["mtimeNs"]), item["name"]), reverse=True)
    return candidates[:ACCOUNTING_CPA_MAX_CANDIDATES]


def eligible_accounting_cpa_auth_candidates() -> list[dict]:
    cutoff_ns = time.time_ns() - ACCOUNTING_CPA_RECENT_SECONDS * 1_000_000_000
    state = read_accounting_storage_state()
    used_digests = {
        item["url"].removeprefix("/api/accounting-auth/")
        for entry in state["entries"]
        if not entry["deletedAt"]
        for item in entry["authFiles"]
    }
    candidates = []
    seen_digests = set(used_digests)
    for item in scan_accounting_cpa_auth_candidates():
        if min(item["createdNs"], item["mtimeNs"]) < cutoff_ns or item["_sha256"] in seen_digests:
            continue
        seen_digests.add(item["_sha256"])
        candidates.append(item)
    return candidates


def public_accounting_cpa_auth_candidates() -> dict:
    candidates = eligible_accounting_cpa_auth_candidates()
    return {
        "ok": True,
        "recentHours": ACCOUNTING_CPA_RECENT_SECONDS // 3600,
        "candidates": [
            {key: item[key] for key in ("id", "name", "source", "modifiedAt", "size")}
            for item in candidates
        ],
    }


def import_accounting_cpa_auth_candidate(candidate_id) -> dict:
    if not isinstance(candidate_id, str) or not re.fullmatch(r"[0-9a-f]{64}", candidate_id):
        raise ValueError("invalid_accounting_auth_candidate")
    candidate = next((item for item in eligible_accounting_cpa_auth_candidates() if item["id"] == candidate_id), None)
    if candidate is None:
        raise ValueError("accounting_auth_candidate_changed")
    data = candidate["_data"]
    if len(data) != candidate["size"] or hashlib.sha256(data).hexdigest() != candidate["_sha256"]:
        raise ValueError("accounting_auth_candidate_changed")
    return store_accounting_auth_bytes(data)


def accounting_secret_path(record_id: str) -> Path:
    digest = hashlib.sha256(record_id.encode("utf-8")).hexdigest()
    return ACCOUNTING_SECRET_DIR / f"{digest}.dpapi"


def write_accounting_secret(record_id: str, credential: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", record_id) or not isinstance(credential, str) or len(credential) > 100_000:
        raise ValueError("invalid_accounting_credential")
    ACCOUNTING_SECRET_DIR.mkdir(parents=True, exist_ok=True)
    path = accounting_secret_path(record_id)
    if not credential:
        path.unlink(missing_ok=True)
        return
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(accounting_protect_bytes(credential.encode("utf-8")))
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_accounting_secret(record_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", record_id):
        raise ValueError("invalid_accounting_entry_id")
    path = accounting_secret_path(record_id)
    if not path.is_file():
        return ""
    return accounting_unprotect_bytes(path.read_bytes()).decode("utf-8")


def social_media_type_for_path(path: Path) -> str:
    suffix = path.suffix.lower()
    return next(
        (content_type for content_type, extension in SOCIAL_MEDIA_TYPES.items() if extension == suffix),
        mimetypes.guess_type(path.name)[0] or "application/octet-stream",
    )


def store_social_media_stream(content_type: str, source, content_length: int) -> dict:
    suffix = SOCIAL_MEDIA_TYPES.get(content_type)
    if not suffix:
        raise ValueError("unsupported_social_media_type")
    if content_length <= 0:
        raise ValueError("empty_social_media")
    if content_length > SOCIAL_MEDIA_MAX_BYTES:
        raise ValueError("social_media_too_large")

    SOCIAL_MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    temporary = SOCIAL_MEDIA_DIR / f".upload.{uuid.uuid4().hex}.tmp"
    digest_builder = hashlib.sha256()
    remaining = content_length
    try:
        with temporary.open("xb") as output:
            while remaining:
                chunk = source.read(min(SOCIAL_MEDIA_CHUNK_BYTES, remaining))
                if not chunk:
                    raise ValueError("Request body is incomplete.")
                output.write(chunk)
                digest_builder.update(chunk)
                remaining -= len(chunk)
            output.flush()
            os.fsync(output.fileno())

        digest = digest_builder.hexdigest()
        path = SOCIAL_MEDIA_DIR / f"{digest}{suffix}"
        with SOCIAL_MEDIA_LOCK:
            existing = sorted(
                candidate
                for candidate in SOCIAL_MEDIA_DIR.glob(f"{digest}.*")
                if candidate.is_file() and not candidate.name.endswith(".tmp")
            )
            if existing:
                path = existing[0]
                temporary.unlink(missing_ok=True)
            else:
                os.replace(temporary, path)
        return {
            "ok": True,
            "url": f"/api/social-media/{digest}",
            "mimeType": social_media_type_for_path(path),
            "size": content_length,
        }
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def parse_social_media_range(value: str, size: int) -> tuple[int, int]:
    if "," in value:
        raise ValueError("multiple_ranges_not_supported")
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", value.strip())
    if not match or (not match.group(1) and not match.group(2)):
        raise ValueError("invalid_range")
    start_value, end_value = match.groups()
    if not start_value:
        suffix_length = int(end_value)
        if suffix_length <= 0 or size <= 0:
            raise ValueError("invalid_range")
        return max(0, size - suffix_length), size - 1
    start = int(start_value)
    if start >= size:
        raise ValueError("range_not_satisfiable")
    end = int(end_value) if end_value else size - 1
    if end < start:
        raise ValueError("range_not_satisfiable")
    return start, min(end, size - 1)


def normalize_youtube_playlist_import_url(value: str) -> tuple[str, str]:
    value = (value or "").strip()
    try:
        parsed = urllib.parse.urlparse(value)
    except Exception as exc:
        raise ValueError("invalid_youtube_playlist_url") from exc

    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or host not in {"youtube.com", "www.youtube.com"}:
        raise ValueError("invalid_youtube_playlist_url")
    if parsed.path.rstrip("/") != "/playlist":
        raise ValueError("invalid_youtube_playlist_url")

    playlist_id = urllib.parse.parse_qs(parsed.query).get("list", [""])[0].strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{10,100}", playlist_id):
        raise ValueError("invalid_youtube_playlist_url")

    canonical = "https://www.youtube.com/playlist?" + urllib.parse.urlencode({"list": playlist_id})
    return canonical, playlist_id


def youtube_playlist_entry_thumbnail(entry: dict, video_id: str) -> str:
    thumbnail = str(entry.get("thumbnail") or "").strip()
    if thumbnail:
        return thumbnail
    thumbnails = entry.get("thumbnails")
    if isinstance(thumbnails, list):
        for candidate in reversed(thumbnails):
            if isinstance(candidate, dict) and candidate.get("url"):
                return str(candidate["url"]).strip()
    return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"


def fetch_youtube_playlist_import(value: str) -> dict:
    url, playlist_id = normalize_youtube_playlist_import_url(value)
    command = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--flat-playlist",
        "--dump-single-json",
        "--skip-download",
        "--no-warnings",
        "--playlist-end",
        str(YOUTUBE_PLAYLIST_IMPORT_MAX_ITEMS),
        url,
    ]
    run_options = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": YOUTUBE_PLAYLIST_IMPORT_TIMEOUT,
        "check": False,
    }
    if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
        run_options["creationflags"] = subprocess.CREATE_NO_WINDOW

    try:
        completed = subprocess.run(command, **run_options)
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError("extractor_timeout") from exc
    except OSError as exc:
        raise RuntimeError("extractor_unavailable") from exc

    if completed.returncode != 0:
        diagnostic = f"{completed.stderr}\n{completed.stdout}".lower()
        if any(marker in diagnostic for marker in ("private", "sign in", "login", "cookies")):
            raise PermissionError("playlist_private")
        if "no module named yt_dlp" in diagnostic:
            raise RuntimeError("extractor_unavailable")
        raise LookupError("playlist_not_found")

    try:
        data = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise LookupError("playlist_not_found") from exc

    raw_entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(raw_entries, list):
        raise LookupError("playlist_not_found")

    playlist_title = clean_meta_value(str(data.get("title") or "YouTube 播放列表"))[:500]
    channel = clean_meta_value(str(data.get("channel") or data.get("uploader") or ""))[:200]
    source_playlist = playlist_title or playlist_id
    entries_by_id: dict[str, dict] = {}
    unavailable_count = 0
    available_count = 0

    for raw_entry in raw_entries[:YOUTUBE_PLAYLIST_IMPORT_MAX_ITEMS]:
        if not isinstance(raw_entry, dict):
            unavailable_count += 1
            continue
        video_id = str(raw_entry.get("id") or "").strip()
        title = clean_meta_value(str(raw_entry.get("title") or ""))[:500]
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id) or title.lower() in {
            "[private video]",
            "[deleted video]",
            "private video",
            "deleted video",
        }:
            unavailable_count += 1
            continue

        available_count += 1
        existing = entries_by_id.get(video_id)
        if existing:
            if source_playlist not in existing["sourcePlaylists"]:
                existing["sourcePlaylists"].append(source_playlist)
            continue

        entries_by_id[video_id] = {
            "youtubeVideoId": video_id,
            "title": title or f"YouTube 视频 · {video_id}",
            "author": clean_meta_value(
                str(raw_entry.get("channel") or raw_entry.get("uploader") or raw_entry.get("channel_name") or "")
            )[:200],
            "duration": raw_entry.get("duration") if isinstance(raw_entry.get("duration"), (int, float)) else "",
            "sourcePlaylists": [source_playlist],
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "thumbnailUrl": youtube_playlist_entry_thumbnail(raw_entry, video_id),
        }

    total_count_value = data.get("playlist_count") or data.get("n_entries") or len(raw_entries)
    try:
        total_count = max(len(raw_entries), int(total_count_value))
    except (TypeError, ValueError):
        total_count = len(raw_entries)
    return {
        "ok": True,
        "playlistId": playlist_id,
        "title": playlist_title,
        "channel": channel,
        "foundCount": total_count,
        "availableCount": available_count,
        "unavailableCount": unavailable_count,
        "entries": list(entries_by_id.values()),
        "truncated": total_count > len(raw_entries),
        "truncatedCount": max(0, total_count - len(raw_entries)),
    }


def fetch_video_meta(url: str) -> dict:
    url = extract_first_url(url)
    if url in VIDEO_META_CACHE:
        return VIDEO_META_CACHE[url]

    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return {"ok": False, "error": "invalid_url"}
    if not is_allowed_video_meta_url(url):
        return {"ok": False, "error": "unsupported_or_private_url"}

    platform = detect_video_platform(url)
    attempts: list[dict] = []
    meta: dict = {}

    for fetcher in platform_fetchers(platform):
        try:
            meta = fetcher(url)
        except Exception as exc:
            attempts.append({"source": fetcher.__name__, "error": str(exc)})
            meta = {}
        if meta.get("title") or meta.get("image") or meta.get("author"):
            break

    if not (meta.get("title") or meta.get("image") or meta.get("author")):
        try:
            final_url, html = fetch_page(url, mobile=platform == "douyin")
            meta = extract_video_meta(html, final_url)
        except Exception as exc:
            attempts.append({"source": "fetch_page", "error": str(exc)})

    final_url = meta.get("url") or url
    payload = {
        "ok": bool(meta.get("title") or meta.get("image") or meta.get("author")),
        "url": final_url,
        "platform": platform,
        "title": meta.get("title", ""),
        "author": meta.get("author", ""),
        "uploader": meta.get("author", ""),
        "authorUrl": meta.get("authorUrl", ""),
        "image": meta.get("image", ""),
        "thumbnailUrl": meta.get("image", ""),
        "source": meta.get("source", ""),
        "fetchedAt": int(time.time()),
    }
    if attempts and not payload["ok"]:
        payload["error"] = "; ".join(item["error"] for item in attempts if item.get("error")) or "metadata_not_found"
    elif not payload["ok"]:
        payload["error"] = "metadata_not_found"

    VIDEO_META_CACHE[url] = payload
    return payload


def fetch_page(url: str, mobile: bool = False) -> tuple[str, str]:
    if not is_allowed_video_meta_url(url):
        raise ValueError("unsupported_or_private_url")

    request = urllib.request.Request(
        url,
        headers=browser_headers(mobile=mobile),
    )

    with urllib.request.urlopen(request, timeout=8) as response:
        final_url = response.geturl()
        if not is_allowed_video_meta_url(final_url):
            raise ValueError("redirected_to_unsupported_or_private_url")
        charset = response.headers.get_content_charset() or "utf-8"
        raw = response.read(2_000_000)
        encoding = (response.headers.get("Content-Encoding") or "").lower()

    raw = decode_response_body(raw, encoding)
    return final_url, raw.decode(charset, errors="replace")


def fetch_json(url: str, mobile: bool = False) -> dict:
    if not is_allowed_video_meta_url(url):
        raise ValueError("unsupported_or_private_url")

    request = urllib.request.Request(
        url,
        headers={
            **browser_headers(mobile=mobile),
            "Accept": "application/json,text/plain,*/*",
        },
    )

    with urllib.request.urlopen(request, timeout=8) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        raw = response.read(2_000_000)
        encoding = (response.headers.get("Content-Encoding") or "").lower()

    raw = decode_response_body(raw, encoding)
    if not raw:
        return {}
    return json.loads(raw.decode(charset, errors="replace"))


def fetch_video_image(url: str) -> tuple[str, bytes]:
    url = extract_first_url(url)
    if not is_allowed_video_image_url(url):
        raise ValueError("unsupported_or_private_url")

    headers = {
        **browser_headers(),
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    }
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if "hdslb.com" in host or "bilibili.com" in host:
        headers["Referer"] = "https://www.bilibili.com/"
    elif "douyin" in host:
        headers["Referer"] = "https://www.douyin.com/"
    elif "ytimg.com" in host or "googleusercontent.com" in host:
        headers["Referer"] = "https://www.youtube.com/"

    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=8) as response:
        final_url = response.geturl()
        if not is_allowed_video_image_url(final_url):
            raise ValueError("redirected_to_unsupported_or_private_url")
        content_type = response.headers.get_content_type() or "application/octet-stream"
        if not content_type.startswith("image/"):
            raise ValueError("not_an_image")
        return content_type, response.read(6_000_000)


def browser_headers(mobile: bool = False) -> dict[str, str]:
    user_agent = (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
        if mobile
        else (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0 Safari/537.36"
        )
    )
    return {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }


def detect_video_platform(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc.lower()
    if "bilibili.com" in host or host.endswith("b23.tv"):
        return "bilibili"
    if "youtube.com" in host or host.endswith("youtu.be"):
        return "youtube"
    if "douyin.com" in host or host.endswith("iesdouyin.com"):
        return "douyin"
    return "other"


def is_allowed_video_meta_url(url: str) -> bool:
    return is_allowed_external_url(url, VIDEO_META_ALLOWED_HOSTS)


def is_allowed_video_image_url(url: str) -> bool:
    return is_allowed_external_url(url, VIDEO_IMAGE_ALLOWED_HOSTS)


def is_allowed_external_url(url: str, allowed_hosts: tuple[str, ...]) -> bool:
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return False

    if parsed.scheme not in {"http", "https"}:
        return False

    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        return False

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None

    if address is not None:
        return not (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
        )

    return any(host == allowed or host.endswith(f".{allowed}") for allowed in allowed_hosts)


def platform_fetchers(platform: str):
    if platform == "bilibili":
        return [fetch_bilibili_meta]
    if platform == "youtube":
        return [fetch_youtube_meta]
    if platform == "douyin":
        return [fetch_douyin_meta]
    return []


def fetch_bilibili_meta(url: str) -> dict:
    bvid = extract_bilibili_bvid(url)
    aid = extract_bilibili_aid(url)
    final_url = url

    if not bvid and not aid:
        final_url, _ = fetch_page(url)
        bvid = extract_bilibili_bvid(final_url)
        aid = extract_bilibili_aid(final_url)

    if not bvid and not aid:
        return {}

    query = urllib.parse.urlencode({"bvid": bvid} if bvid else {"aid": aid})
    data = fetch_json(f"https://api.bilibili.com/x/web-interface/view?{query}")
    item = data.get("data") if data.get("code") == 0 else {}
    owner = item.get("owner") or {}
    return {
        "url": f"https://www.bilibili.com/video/{item.get('bvid') or bvid or 'av' + aid}",
        "title": clean_meta_value(item.get("title", "")),
        "author": clean_meta_value(owner.get("name", "")),
        "authorUrl": f"https://space.bilibili.com/{owner.get('mid')}" if owner.get("mid") else "",
        "image": ensure_https(item.get("pic", "")),
        "source": "bilibili-api",
    }


def fetch_youtube_meta(url: str) -> dict:
    endpoint = "https://www.youtube.com/oembed?" + urllib.parse.urlencode({"format": "json", "url": url})
    data = fetch_json(endpoint)
    return {
        "url": url,
        "title": clean_meta_value(data.get("title", "")),
        "author": clean_meta_value(data.get("author_name", "")),
        "authorUrl": data.get("author_url", ""),
        "image": data.get("thumbnail_url", "") or youtube_thumbnail_from_url(url),
        "source": "youtube-oembed",
    }


def fetch_douyin_meta(url: str) -> dict:
    aweme_id = extract_douyin_id(url)
    final_url = url

    if not aweme_id:
        final_url, _ = fetch_page(url, mobile=True)
        aweme_id = extract_douyin_id(final_url)

    if not aweme_id:
        return {}

    share_url = f"https://www.iesdouyin.com/share/video/{aweme_id}/"
    final_url, html = fetch_page(share_url, mobile=True)
    data = extract_douyin_router_data(html)
    item = douyin_first_item(data)
    if not item:
        return extract_video_meta(html, final_url)

    author = item.get("author") or {}
    image = first_url_from_nested(
        item,
        [
            ["video", "cover", "url_list"],
            ["video", "origin_cover", "url_list"],
            ["video", "dynamic_cover", "url_list"],
        ],
    )
    return {
        "url": f"https://www.douyin.com/video/{item.get('aweme_id') or aweme_id}",
        "title": clean_meta_value(item.get("desc", "")),
        "author": clean_meta_value(author.get("nickname", "")),
        "authorUrl": f"https://www.douyin.com/user/{author.get('sec_uid')}" if author.get("sec_uid") else "",
        "image": ensure_https(image),
        "source": "douyin-share-ssr",
    }


def extract_video_meta(html: str, base_url: str) -> dict:
    tags = parse_meta_tags(html)

    title = first_value(
        tags,
        ["og:title", "twitter:title", "title", "name", "headline"],
    )
    if not title:
        title_match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
        title = clean_meta_value(title_match.group(1)) if title_match else ""

    image = first_value(
        tags,
        [
            "og:image",
            "og:image:url",
            "twitter:image",
            "twitter:image:src",
            "thumbnailUrl",
            "thumbnailurl",
            "image",
        ],
    )
    if not image:
        image_match = re.search(
            r'"thumbnailUrl"\s*:\s*(?:"([^"]+)"|\[\s*"([^"]+)")',
            html,
            flags=re.IGNORECASE,
        )
        image = clean_meta_value((image_match.group(1) or image_match.group(2)) if image_match else "")

    author = first_value(
        tags,
        [
            "og:video:actor",
            "twitter:creator",
            "author",
            "article:author",
            "name",
        ],
    )

    return {
        "title": title,
        "author": author,
        "image": urllib.parse.urljoin(base_url, image) if image else "",
        "url": base_url,
        "source": "page-meta",
    }


def extract_first_url(value: str) -> str:
    match = re.search(r"https?://[^\s\"'<>]+", value or "")
    return match.group(0) if match else (value or "").strip()


def ensure_https(value: str) -> str:
    if not value:
        return ""
    if value.startswith("//"):
        return f"https:{value}"
    if value.startswith("http://"):
        return f"https://{value[7:]}"
    return value


def extract_bilibili_bvid(url: str) -> str:
    match = re.search(r"\b(BV[0-9A-Za-z]{10,})\b", url)
    return match.group(1) if match else ""


def extract_bilibili_aid(url: str) -> str:
    match = re.search(r"/video/av(\d+)", url, flags=re.IGNORECASE)
    return match.group(1) if match else ""


def extract_youtube_id(url: str) -> str:
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return ""

    host = parsed.netloc.lower()
    if host.endswith("youtu.be"):
        return parsed.path.strip("/").split("/")[0]

    query_id = urllib.parse.parse_qs(parsed.query).get("v", [""])[0]
    if query_id:
        return query_id

    match = re.search(r"/(?:shorts|embed)/([^/?#]+)", parsed.path)
    return match.group(1) if match else ""


def youtube_thumbnail_from_url(url: str) -> str:
    video_id = extract_youtube_id(url)
    return f"https://img.youtube.com/vi/{urllib.parse.quote(video_id)}/hqdefault.jpg" if video_id else ""


def extract_douyin_id(url: str) -> str:
    match = re.search(r"/(?:video|note)/(\d+)", url)
    if match:
        return match.group(1)
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    for key in ("aweme_id", "item_id", "modal_id"):
        if query.get(key, [""])[0].isdigit():
            return query[key][0]
    digit_match = re.search(r"\b(\d{16,22})\b", url)
    return digit_match.group(1) if digit_match else ""


def extract_douyin_router_data(html: str) -> dict:
    match = re.search(r"window\._ROUTER_DATA\s*=\s*({.*?})\s*</script>", html, flags=re.DOTALL)
    if not match:
        return {}
    try:
        return json.loads(match.group(1))
    except Exception:
        return {}


def douyin_first_item(data: dict) -> dict:
    loader_data = data.get("loaderData") or {}
    for value in loader_data.values():
        video_info = value.get("videoInfoRes") if isinstance(value, dict) else None
        item_list = video_info.get("item_list") if isinstance(video_info, dict) else None
        if item_list:
            return item_list[0]
    return {}


def first_url_from_nested(data: dict, paths: list[list[str]]) -> str:
    for path in paths:
        value = data
        for key in path:
            if isinstance(value, dict):
                value = value.get(key)
            else:
                value = None
                break
        if isinstance(value, list) and value:
            return str(value[0])
        if isinstance(value, str):
            return value
    return ""


def decode_response_body(raw: bytes, encoding: str) -> bytes:
    try:
        if "gzip" in encoding:
            return gzip.decompress(raw)
        if "deflate" in encoding:
            return zlib.decompress(raw)
    except Exception:
        return raw
    return raw


def parse_meta_tags(html: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for match in re.finditer(r"<meta\b[^>]*>", html, flags=re.IGNORECASE):
        attrs = parse_attrs(match.group(0))
        key = attrs.get("property") or attrs.get("name") or attrs.get("itemprop")
        value = attrs.get("content")
        if key and value and key not in result:
            result[key] = clean_meta_value(value)
    return result


def parse_attrs(tag: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    pattern = r"""([:\w-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))"""
    for name, double_value, single_value, bare_value in re.findall(pattern, tag):
        attrs[name.lower()] = unescape(double_value or single_value or bare_value or "")
    return attrs


def first_value(values: dict[str, str], keys: list[str]) -> str:
    lowered = {key.lower(): value for key, value in values.items()}
    for key in keys:
        value = lowered.get(key.lower(), "")
        if value:
            return value
    return ""


def clean_meta_value(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", unescape(value or "")).strip()
    return cleaned[:500]


def chat_history_normalize_path(value) -> str:
    if not isinstance(value, str) or not value:
        return ""
    return os.path.normpath(value)


def chat_history_compact_text(value, maximum: int = 220) -> str:
    if value is None:
        return ""
    cleaned = re.sub(r"\s+", " ", str(value).replace("\r", "")).strip()
    if len(cleaned) <= maximum:
        return cleaned
    if maximum <= 3:
        return cleaned[:maximum]
    return f"{cleaned[: maximum - 3]}..."


def chat_history_plain_codex_content(content) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""

    parts = []
    for item in content:
        if not isinstance(item, dict):
            continue
        text = item.get("text") or item.get("input_text") or item.get("output_text")
        if text:
            parts.append(str(text))
    return "\n".join(parts)


def chat_history_plain_claude_content(content) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""

    parts = []
    for item in content:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "text" and item.get("text"):
            parts.append(str(item["text"]))
        elif item_type == "tool_use":
            parts.append(f"[tool] {item.get('name') or ''}".rstrip())
    return "\n".join(parts)


def chat_history_jsonl_items(path: Path):
    with path.open("r", encoding="utf-8", errors="replace") as source:
        for line in source:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except (TypeError, ValueError):
                continue
            if isinstance(item, dict):
                yield item


def chat_history_jsonl_files(root: Path) -> list[Path]:
    if not root.exists() or not root.is_dir():
        return []

    files = []
    for current, dirs, names in os.walk(root, topdown=True, onerror=lambda _error: None, followlinks=False):
        dirs[:] = [name for name in dirs if not (Path(current) / name).is_symlink()]
        for name in names:
            if name.endswith(".jsonl"):
                files.append(Path(current) / name)
    return sorted(files, key=lambda item: os.path.normcase(str(item)))


def chat_history_read_codex_title_index() -> dict[str, dict]:
    titles: dict[str, dict] = {}
    if not CODEX_SESSION_INDEX.exists():
        return titles

    try:
        for item in chat_history_jsonl_items(CODEX_SESSION_INDEX):
            session_id = item.get("id")
            if session_id:
                titles[str(session_id)] = {
                    "title": item.get("thread_name") or "",
                    "updatedAt": item.get("updated_at") or "",
                }
    except OSError:
        return {}
    return titles


def chat_history_empty_usage() -> dict[str, int]:
    return {key: 0 for key in CHAT_HISTORY_USAGE_KEYS}


def chat_history_numeric_token(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value or value in {float("inf"), float("-inf")}:
        return None
    return int(value)


def chat_history_add_usage(total: dict, usage) -> None:
    if not isinstance(usage, dict):
        return
    for key in CHAT_HISTORY_USAGE_KEYS:
        value = chat_history_numeric_token(usage.get(key))
        if value is not None:
            total[key] = int(total.get(key) or 0) + value


def chat_history_token_total(usage) -> int:
    if not isinstance(usage, dict):
        return 0
    explicit = chat_history_numeric_token(usage.get("total_tokens")) or 0
    if explicit > 0:
        return explicit
    return sum(
        chat_history_numeric_token(usage.get(key)) or 0
        for key in CHAT_HISTORY_USAGE_KEYS
        if key != "total_tokens"
    )


def chat_history_usage_summary(usage) -> dict:
    safe = chat_history_empty_usage()
    chat_history_add_usage(safe, usage)
    return {
        "input": safe["input_tokens"],
        "cached": (
            safe["cached_input_tokens"]
            + safe["cache_creation_input_tokens"]
            + safe["cache_read_input_tokens"]
        ),
        "output": safe["output_tokens"],
        "reasoning": safe["reasoning_output_tokens"],
        "total": chat_history_token_total(safe),
        "raw": safe,
    }


def chat_history_codex_total_from_info(info) -> dict | None:
    usage = info.get("total_token_usage") if isinstance(info, dict) else None
    if not isinstance(usage, dict):
        return None
    return {
        "input_tokens": chat_history_numeric_token(usage.get("input_tokens")) or 0,
        "cached_input_tokens": chat_history_numeric_token(usage.get("cached_input_tokens")) or 0,
        "output_tokens": chat_history_numeric_token(usage.get("output_tokens")) or 0,
        "reasoning_output_tokens": chat_history_numeric_token(usage.get("reasoning_output_tokens")) or 0,
        "total_tokens": chat_history_numeric_token(usage.get("total_tokens")) or 0,
    }


def chat_history_iso_from_timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def chat_history_parse_timestamp(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if normalized[-1:].lower() == "z":
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    try:
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def chat_history_timestamp_key(value) -> float:
    parsed = chat_history_parse_timestamp(value)
    if parsed is None:
        return 0.0
    try:
        return parsed.timestamp()
    except (ValueError, OverflowError):
        return 0.0


def chat_history_latest_timestamp(*values) -> str:
    latest_value = ""
    latest_time = None
    for value in values:
        parsed = chat_history_parse_timestamp(value)
        if parsed is not None and (latest_time is None or parsed > latest_time):
            latest_value = value.strip()
            latest_time = parsed
    return latest_value


def chat_history_session_key(source: str, path: Path) -> str:
    try:
        resolved = path.resolve(strict=False)
    except OSError:
        resolved = path.absolute()
    identity = f"{source}\0{os.path.normcase(str(resolved))}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def chat_history_codex_session_id(path: Path) -> str:
    match = re.search(
        r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
        path.name,
        flags=re.IGNORECASE,
    )
    return match.group(1) if match else path.stem


def chat_history_parse_codex_session(path: Path, title_index: dict[str, dict]) -> dict:
    stat = path.stat()
    resolved = path.resolve()
    file_updated_at = chat_history_iso_from_timestamp(stat.st_mtime)
    session = {
        "source": "codex",
        "id": chat_history_codex_session_id(path),
        "key": chat_history_session_key("codex", resolved),
        "title": "",
        "cwd": "",
        "file": str(resolved),
        "createdAt": "",
        "updatedAt": file_updated_at,
        "size": stat.st_size,
        "tokenUsage": {},
        "tokenTotal": 0,
        "userMessages": [],
        "assistantSnippets": [],
        "userTurnCount": 0,
        "assistantTurnCount": 0,
        "model": "",
        "version": "",
    }
    last_usage = None
    jsonl_updated_at = ""

    for item in chat_history_jsonl_items(path):
        timestamp = item.get("timestamp")
        if chat_history_parse_timestamp(timestamp) is not None:
            session["createdAt"] = session["createdAt"] or timestamp
            jsonl_updated_at = chat_history_latest_timestamp(jsonl_updated_at, timestamp)

        item_type = item.get("type")
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        if item_type == "session_meta":
            if payload.get("id"):
                session["id"] = str(payload["id"])
            session["cwd"] = chat_history_normalize_path(payload.get("cwd") or session["cwd"])
            payload_timestamp = payload.get("timestamp")
            if chat_history_parse_timestamp(payload_timestamp) is not None:
                session["createdAt"] = session["createdAt"] or payload_timestamp
                jsonl_updated_at = chat_history_latest_timestamp(
                    jsonl_updated_at,
                    payload_timestamp,
                )
            session["version"] = str(payload.get("cli_version") or session["version"])
            session["model"] = str(payload.get("model") or session["model"])
            continue

        if item_type == "turn_context":
            session["cwd"] = chat_history_normalize_path(payload.get("cwd") or session["cwd"])
            session["model"] = str(payload.get("model") or session["model"])
            continue

        if item_type == "event_msg":
            if payload.get("type") == "user_message":
                text = chat_history_compact_text(payload.get("message"), 320)
                if text:
                    session["userTurnCount"] += 1
                    if len(session["userMessages"]) < 12:
                        session["userMessages"].append(
                            {"role": "user", "text": text, "timestamp": timestamp or ""}
                        )
            elif payload.get("type") == "token_count":
                last_usage = chat_history_codex_total_from_info(payload.get("info")) or last_usage
            continue

        if item_type == "response_item" and payload.get("type") == "message":
            if payload.get("role") == "assistant":
                session["assistantTurnCount"] += 1
                text = chat_history_compact_text(
                    chat_history_plain_codex_content(payload.get("content")),
                    260,
                )
                if text and len(session["assistantSnippets"]) < 8:
                    session["assistantSnippets"].append(
                        {"role": "assistant", "text": text, "timestamp": timestamp or ""}
                    )

    indexed = title_index.get(session["id"], {})
    first_user_text = session["userMessages"][0]["text"] if session["userMessages"] else ""
    session["title"] = (
        chat_history_compact_text(indexed.get("title"), 120)
        or chat_history_compact_text(first_user_text, 80)
        or "Untitled Codex session"
    )
    session["updatedAt"] = chat_history_latest_timestamp(
        jsonl_updated_at,
        indexed.get("updatedAt"),
        file_updated_at,
    ) or file_updated_at
    session["tokenUsage"] = last_usage or {}
    session["tokenTotal"] = chat_history_token_total(session["tokenUsage"])
    session["cwd"] = session["cwd"] or "Unknown"
    return session


def chat_history_cwd_from_claude_project_name(name: str) -> str:
    parts = [part for part in name.split("--") if part]
    if not parts:
        return ""
    if len(parts) == 1 and re.fullmatch(r"[A-Za-z]", parts[0]):
        return f"{parts[0].upper()}:\\"
    drive = f"{parts[0].upper()}:" if len(parts[0]) == 1 else parts[0]
    return os.path.normpath(os.path.join(drive + os.sep, *parts[1:]))


def chat_history_claude_files() -> list[tuple[Path, str]]:
    root = CHAT_HISTORY_CLAUDE_HOME / "projects"
    if not root.exists() or not root.is_dir():
        return []

    result = []
    try:
        projects = sorted(root.iterdir(), key=lambda item: item.name.lower())
    except OSError:
        return []
    for project in projects:
        if not project.is_dir() or project.is_symlink():
            continue
        try:
            entries = sorted(project.iterdir(), key=lambda item: item.name.lower())
        except OSError:
            continue
        for entry in entries:
            if entry.is_file() and entry.name.endswith(".jsonl"):
                result.append((entry, project.name))
    return result


def chat_history_parse_claude_session(path: Path, project_name: str) -> dict:
    stat = path.stat()
    resolved = path.resolve()
    total_usage: dict[str, int] = {}
    session = {
        "source": "claude",
        "id": path.stem,
        "key": chat_history_session_key("claude", resolved),
        "title": "",
        "cwd": chat_history_cwd_from_claude_project_name(project_name),
        "file": str(resolved),
        "createdAt": "",
        "updatedAt": chat_history_iso_from_timestamp(stat.st_mtime),
        "size": stat.st_size,
        "tokenUsage": total_usage,
        "tokenTotal": 0,
        "userMessages": [],
        "assistantSnippets": [],
        "userTurnCount": 0,
        "assistantTurnCount": 0,
        "model": "",
        "version": "",
    }

    for item in chat_history_jsonl_items(path):
        timestamp = item.get("timestamp")
        if isinstance(timestamp, str) and timestamp:
            session["createdAt"] = session["createdAt"] or timestamp
            session["updatedAt"] = timestamp
        session["cwd"] = chat_history_normalize_path(item.get("cwd") or session["cwd"])
        session["version"] = str(item.get("version") or session["version"])

        message = item.get("message") if isinstance(item.get("message"), dict) else {}
        session["model"] = str(message.get("model") or session["model"])
        chat_history_add_usage(total_usage, message.get("usage"))

        item_type = item.get("type")
        role = message.get("role")
        if item_type == "user" and role == "user":
            text = chat_history_compact_text(
                chat_history_plain_claude_content(message.get("content")),
                320,
            )
            if text:
                session["userTurnCount"] += 1
                if len(session["userMessages"]) < 12:
                    session["userMessages"].append(
                        {"role": "user", "text": text, "timestamp": timestamp or ""}
                    )
        elif item_type == "assistant" and role == "assistant":
            session["assistantTurnCount"] += 1
            text = chat_history_compact_text(
                chat_history_plain_claude_content(message.get("content")),
                260,
            )
            if text and len(session["assistantSnippets"]) < 8:
                session["assistantSnippets"].append(
                    {"role": "assistant", "text": text, "timestamp": timestamp or ""}
                )

    first_user_text = session["userMessages"][0]["text"] if session["userMessages"] else ""
    session["title"] = chat_history_compact_text(first_user_text, 80) or "Untitled Claude session"
    session["tokenTotal"] = chat_history_token_total(total_usage)
    session["cwd"] = session["cwd"] or "Unknown"
    return session


def chat_history_build_uncached() -> tuple[dict, dict[str, dict], dict[str, dict]]:
    title_index = chat_history_read_codex_title_index()
    codex_files = []
    for root in (CODEX_HOME / "sessions", CODEX_HOME / "archived_sessions"):
        codex_files.extend(chat_history_jsonl_files(root))
    claude_files = chat_history_claude_files()

    sessions = []
    for path in codex_files:
        try:
            sessions.append(chat_history_parse_codex_session(path, title_index))
        except Exception as exc:
            print(f"Chat history: failed to parse Codex session {path}: {exc}", file=sys.stderr)
    for path, project_name in claude_files:
        try:
            sessions.append(chat_history_parse_claude_session(path, project_name))
        except Exception as exc:
            print(f"Chat history: failed to parse Claude session {path}: {exc}", file=sys.stderr)

    sessions.sort(
        key=lambda item: chat_history_timestamp_key(item.get("updatedAt")),
        reverse=True,
    )
    sessions_by_key = {session["key"]: session for session in sessions}
    folders_by_path: dict[str, dict] = {}
    source_totals = {
        "codex": {
            "sessions": 0,
            "folders": set(),
            "tokenTotal": 0,
            "tokenUsage": chat_history_empty_usage(),
        },
        "claude": {
            "sessions": 0,
            "folders": set(),
            "tokenTotal": 0,
            "tokenUsage": chat_history_empty_usage(),
        },
    }
    totals = {
        "sessions": len(sessions),
        "codexSessions": sum(session["source"] == "codex" for session in sessions),
        "claudeSessions": sum(session["source"] == "claude" for session in sessions),
        "folders": 0,
        "tokenTotal": 0,
        "knownTokenSessions": 0,
        "sources": {},
    }

    for session in sessions:
        source = session["source"]
        cwd = session["cwd"]
        tokens = int(session.get("tokenTotal") or 0)
        totals["tokenTotal"] += tokens
        totals["knownTokenSessions"] += int(tokens > 0)
        source_totals[source]["sessions"] += 1
        source_totals[source]["folders"].add(cwd)
        source_totals[source]["tokenTotal"] += tokens
        chat_history_add_usage(source_totals[source]["tokenUsage"], session.get("tokenUsage"))

        if cwd not in folders_by_path:
            folders_by_path[cwd] = {
                "cwd": cwd,
                "sessionCount": 0,
                "codexCount": 0,
                "claudeCount": 0,
                "tokenTotal": 0,
                "tokenBySource": {"codex": 0, "claude": 0},
                "updatedAt": session["updatedAt"],
            }
        folder = folders_by_path[cwd]
        folder["sessionCount"] += 1
        folder["codexCount"] += int(source == "codex")
        folder["claudeCount"] += int(source == "claude")
        folder["tokenTotal"] += tokens
        folder["tokenBySource"][source] += tokens
        if chat_history_timestamp_key(session["updatedAt"]) > chat_history_timestamp_key(folder["updatedAt"]):
            folder["updatedAt"] = session["updatedAt"]

    folders = sorted(
        folders_by_path.values(),
        key=lambda item: (
            item["sessionCount"],
            chat_history_timestamp_key(item.get("updatedAt")),
        ),
        reverse=True,
    )
    totals["folders"] = len(folders)
    totals["sources"] = {
        source: {
            "sessions": values["sessions"],
            "folders": len(values["folders"]),
            "tokenTotal": values["tokenTotal"],
            "tokenUsage": chat_history_usage_summary(values["tokenUsage"]),
        }
        for source, values in source_totals.items()
    }
    summary = {
        "generatedAt": datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z"),
        "roots": {
            "codex": str(CODEX_HOME),
            "claude": str(CHAT_HISTORY_CLAUDE_HOME),
        },
        "totals": totals,
        "folders": folders,
        "sessions": sessions,
    }
    return summary, sessions_by_key, folders_by_path


def build_chat_history_index(force: bool = False) -> dict:
    global CHAT_HISTORY_CACHE
    global CHAT_HISTORY_CACHE_BUILT_AT
    global CHAT_HISTORY_SESSIONS_BY_KEY
    global CHAT_HISTORY_FOLDERS_BY_PATH

    now = time.monotonic()
    if (
        not force
        and CHAT_HISTORY_CACHE is not None
        and now - CHAT_HISTORY_CACHE_BUILT_AT < CHAT_HISTORY_CACHE_TTL
    ):
        return CHAT_HISTORY_CACHE

    with CHAT_HISTORY_BUILD_LOCK:
        now = time.monotonic()
        if (
            not force
            and CHAT_HISTORY_CACHE is not None
            and now - CHAT_HISTORY_CACHE_BUILT_AT < CHAT_HISTORY_CACHE_TTL
        ):
            return CHAT_HISTORY_CACHE

        summary, sessions_by_key, folders_by_path = chat_history_build_uncached()
        CHAT_HISTORY_CACHE = summary
        CHAT_HISTORY_SESSIONS_BY_KEY = sessions_by_key
        CHAT_HISTORY_FOLDERS_BY_PATH = folders_by_path
        CHAT_HISTORY_CACHE_BUILT_AT = time.monotonic()
        return summary


def chat_history_transcript(session: dict) -> list[dict]:
    messages = []
    path = Path(session["file"])
    source = session.get("source")

    for item in chat_history_jsonl_items(path):
        if len(messages) >= 160:
            break
        timestamp = item.get("timestamp") or ""
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        if source == "codex":
            if item.get("type") == "event_msg" and payload.get("type") == "user_message":
                text = chat_history_compact_text(payload.get("message"), 1500)
                if text:
                    messages.append({"role": "user", "text": text, "timestamp": timestamp})
            elif (
                item.get("type") == "response_item"
                and payload.get("type") == "message"
                and payload.get("role") == "assistant"
            ):
                text = chat_history_compact_text(
                    chat_history_plain_codex_content(payload.get("content")),
                    1500,
                )
                if text:
                    messages.append({"role": "assistant", "text": text, "timestamp": timestamp})
            continue

        message = item.get("message") if isinstance(item.get("message"), dict) else {}
        role = message.get("role")
        if item.get("type") in {"user", "assistant"} and role in {"user", "assistant"}:
            text = chat_history_compact_text(
                chat_history_plain_claude_content(message.get("content")),
                1500,
            )
            if text:
                messages.append({"role": role, "text": text, "timestamp": timestamp})
    return messages


def chat_history_path_value(value) -> str:
    if not isinstance(value, str):
        return ""
    cleaned = value.strip()
    if re.fullmatch(r"[A-Za-z]:", cleaned):
        return f"{cleaned}\\"
    return cleaned


def chat_history_existing_directory(value) -> Path:
    cleaned = chat_history_path_value(value)
    if not cleaned or cleaned == "Unknown":
        raise ValueError("This session path is unavailable.")
    path = Path(cleaned).expanduser()
    if not path.is_absolute():
        raise ValueError("The terminal path must be absolute.")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ValueError("This session path does not exist on disk.") from exc
    if not resolved.is_dir():
        raise ValueError("The terminal path is not a directory.")
    return resolved


def chat_history_comparable_path(path: Path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def chat_history_is_indexed_folder_or_ancestor(path: Path) -> bool:
    target = chat_history_comparable_path(path)
    for value in CHAT_HISTORY_FOLDERS_BY_PATH:
        cleaned = chat_history_path_value(value)
        if not cleaned or cleaned == "Unknown":
            continue
        try:
            indexed_path = Path(cleaned).expanduser().resolve(strict=False)
            indexed = chat_history_comparable_path(indexed_path)
            if os.path.commonpath([target, indexed]) == target:
                return True
        except (OSError, ValueError):
            continue
    return False


def chat_history_open_terminal(cwd: Path, mode: str) -> None:
    if os.name != "nt":
        raise RuntimeError("Opening a terminal is currently supported on Windows only.")
    if mode not in CHAT_HISTORY_TERMINAL_MODES:
        raise ValueError("Unsupported terminal mode.")

    args = ["wt.exe", "new-tab", "-d", str(cwd)]
    commands = {
        "codex": "codex",
        "codex-resume": "codex resume",
        "claude": "claude",
        "claude-resume": "claude --resume",
    }
    if mode in commands:
        args.extend(["powershell.exe", "-NoExit", "-Command", commands[mode]])

    subprocess.Popen(
        args,
        cwd=str(cwd),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        shell=False,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )


def token_stats_nonnegative_int(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value or value in {float("inf"), float("-inf")}:
        return None
    return max(0, int(value))


def token_stats_known_usage_value(payload: dict, *fields: str) -> tuple[int, bool]:
    """Return a usage value and whether the source explicitly reported it."""
    if not isinstance(payload, dict):
        return 0, False
    for field in fields:
        if field not in payload:
            continue
        value = token_stats_nonnegative_int(payload.get(field))
        if value is not None:
            return value, True
    return 0, False


def token_stats_cache_coverage(known_count: int, request_count: int) -> str:
    if request_count > 0 and known_count == request_count:
        return "full"
    return "partial" if known_count > 0 else "none"


def token_stats_cache_read_known(row: dict) -> bool:
    if "cacheReadKnown" in row:
        return bool(row["cacheReadKnown"])
    # Compatibility for explicit values in old callers. Parsers always set the
    # flag, so their numeric placeholders cannot become falsely known zeros.
    return token_stats_known_usage_value(row, "cachedInputTokens", "cacheReadTokens")[1]


def token_stats_daily_cache_read_coverage(days: list[dict]) -> dict:
    known = sum(day.get("cacheReadKnownRequestCount", 0) for day in days)
    unknown = sum(day.get("cacheReadUnknownRequestCount", 0) for day in days)
    coverage = token_stats_cache_coverage(known, known + unknown)
    return {
        "cacheReadKnown": coverage == "full",
        "cacheReadKnownRequestCount": known,
        "cacheReadUnknownRequestCount": unknown,
        "cacheReadCoverage": coverage,
    }


def token_stats_normalize_service_tier(value) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower().replace("-", "_")
    aliases = {
        "standard": "standard",
        "default": "standard",
        "priority": "priority",
        "fast": "priority",
        "flex": "flex",
        "batch": "batch",
    }
    return aliases.get(normalized)


def token_stats_codex_config_tier(config_path: Path | None = None) -> dict:
    path = config_path or TOKEN_STATS_CODEX_CONFIG
    result = {"tier": "unknown", "requestedTier": "unknown", "source": "unknown"}
    try:
        with path.open("rb") as source:
            config = tomllib.load(source)
    except (OSError, UnicodeError, tomllib.TOMLDecodeError):
        return result
    if not isinstance(config, dict):
        return result

    configured_tier = token_stats_normalize_service_tier(config.get("service_tier"))
    if configured_tier is not None:
        return {
            "tier": configured_tier,
            "requestedTier": str(config.get("service_tier")).strip().lower(),
            "source": "config.service_tier",
        }

    return result


def token_stats_service_tier_from_log(container: dict, source: str) -> dict | None:
    if not isinstance(container, dict):
        return None
    if "service_tier" in container:
        value = container.get("service_tier")
    elif "serviceTier" in container:
        value = container.get("serviceTier")
    else:
        return None
    return {
        "tier": token_stats_normalize_service_tier(value) or "unknown",
        "requestedTier": str(value).strip().lower() if value is not None else "unknown",
        "source": source,
    }


def token_stats_shanghai_day(value) -> date | None:
    parsed = chat_history_parse_timestamp(value)
    if parsed is None:
        return None
    return parsed.astimezone(TOKEN_STATS_TZ).date()


def token_stats_local_iso(value: datetime | None) -> str | None:
    return value.astimezone(TOKEN_STATS_TZ).isoformat() if value is not None else None


def token_stats_record_error(coverage: dict, path: Path, error: Exception) -> None:
    coverage["filesFailed"] += 1
    if len(coverage["fileErrors"]) < 10:
        coverage["fileErrors"].append({"file": str(path), "error": str(error)})


def token_stats_jsonl_objects(path: Path, coverage: dict):
    try:
        with path.open("r", encoding="utf-8", errors="replace") as source:
            for line in source:
                coverage["lines"] += 1
                try:
                    item = json.loads(line)
                except (TypeError, ValueError):
                    coverage["invalidJsonLines"] += 1
                    coverage["badLines"] += 1
                    continue
                if not isinstance(item, dict):
                    coverage["nonObjectLines"] += 1
                    coverage["badLines"] += 1
                    continue
                coverage["objects"] += 1
                yield item
        coverage["filesRead"] += 1
    except OSError as exc:
        token_stats_record_error(coverage, path, exc)


def token_stats_decode_varint(data: bytes, offset: int = 0) -> tuple[int, int]:
    val = 0
    shift = 0
    i = offset
    length = len(data)
    while i < length:
        b = data[i]
        i += 1
        val |= (b & 0x7F) << shift
        shift += 7
        if not (b & 0x80):
            return val, i
    return val, i


def token_stats_parse_proto(data: bytes) -> dict[int, list[tuple[str, any]]]:
    res = {}
    i = 0
    length = len(data)
    while i < length:
        tag, i = token_stats_decode_varint(data, i)
        if i > length:
            break
        fnum = tag >> 3
        wtype = tag & 7
        if wtype == 0:
            val, i = token_stats_decode_varint(data, i)
            res.setdefault(fnum, []).append(("varint", val))
        elif wtype == 2:
            chunk_len, i = token_stats_decode_varint(data, i)
            if chunk_len < 0 or i + chunk_len > length:
                break
            val = data[i : i + chunk_len]
            i += chunk_len
            res.setdefault(fnum, []).append(("bytes", val))
        elif wtype == 1:
            i += 8
        elif wtype == 5:
            i += 4
        else:
            break
    return res


def token_stats_antigravity_files(root: Path | None = None) -> list[Path]:
    conversations_root = root or (TOKEN_STATS_ANTIGRAVITY_HOME / "conversations")
    if not conversations_root.exists() or not conversations_root.is_dir():
        return []
    files = []
    try:
        for entry in conversations_root.iterdir():
            if (
                entry.is_file()
                and entry.suffix.lower() == ".db"
                and not entry.name.endswith(("-shm", "-wal"))
            ):
                files.append(entry)
    except OSError:
        return []
    return sorted(files, key=lambda item: os.path.normcase(str(item)))


def token_stats_antigravity_summaries(db_path: Path | None = None) -> dict[str, dict]:
    path = db_path or (TOKEN_STATS_ANTIGRAVITY_HOME / "conversation_summaries.db")
    if not path.is_file():
        return {}
    summaries = {}
    connection = None
    try:
        uri = f"{path.resolve().as_uri()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=5)
        cur = connection.cursor()
        cur.execute(
            "SELECT conversation_id, title, preview, workspace_uris, last_modified_time FROM conversation_summaries"
        )
        for cid, title, preview, w_uris, last_mod in cur.fetchall():
            project = ""
            if w_uris:
                try:
                    uris = json.loads(w_uris) if isinstance(w_uris, str) else []
                    if uris and isinstance(uris[0], str):
                        unquoted = urllib.parse.unquote(uris[0])
                        project = unquoted.rstrip("/").split("/")[-1]
                except Exception:
                    pass
            summaries[str(cid)] = {
                "title": (title or preview or "").strip(),
                "preview": (preview or "").strip(),
                "project": project or "Antigravity",
                "lastModifiedTime": last_mod,
            }
    except (OSError, sqlite3.Error):
        pass
    finally:
        if connection is not None:
            try:
                connection.close()
            except sqlite3.Error:
                pass
    return summaries


def token_stats_antigravity_db_candidates(
    path: Path, summaries: dict[str, dict], coverage: dict
) -> list[dict]:
    session_id = path.stem
    session_info = summaries.get(session_id, {})
    candidates = []
    connection = None
    try:
        uri = f"{path.resolve().as_uri()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=5)
        cur = connection.cursor()

        gen_models = {}
        try:
            cur.execute("SELECT idx, data FROM gen_metadata")
            for _idx, data in cur.fetchall():
                if not data:
                    continue
                p = token_stats_parse_proto(data)
                uuid_val = None
                if 4 in p and p[4][0][0] == "bytes":
                    try:
                        uuid_val = p[4][0][1].decode("utf-8", errors="replace").strip()
                    except Exception:
                        pass
                model_name = ""
                if 1 in p and p[1][0][0] == "bytes":
                    sub = token_stats_parse_proto(p[1][0][1])
                    if 19 in sub and sub[19][0][0] == "bytes":
                        try:
                            model_name = sub[19][0][1].decode("utf-8", errors="replace").strip()
                        except Exception:
                            pass
                if uuid_val and model_name:
                    gen_models[uuid_val] = model_name
                    coverage["modelsFromMetadata"] += 1
        except Exception:
            pass

        cur.execute("SELECT idx, metadata FROM steps WHERE step_type = 15 ORDER BY idx ASC")
        rows = cur.fetchall()
        coverage["responseSteps"] += len(rows)

        for step_idx, meta in rows:
            if not meta:
                continue
            coverage["stepRows"] += 1
            p = token_stats_parse_proto(meta)

            ts = None
            if 1 in p and p[1][0][0] == "bytes":
                sub = token_stats_parse_proto(p[1][0][1])
                s = sub.get(1, [(None, 0)])[0][1]
                ns = sub.get(2, [(None, 0)])[0][1]
                if isinstance(s, int) and s > 0:
                    try:
                        ts = datetime.fromtimestamp(s + (ns or 0) / 1e9, tz=timezone.utc)
                    except (ValueError, OverflowError):
                        ts = None

            if ts is None:
                coverage["invalidTimestamps"] += 1
                try:
                    ts = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
                except OSError:
                    ts = datetime.now(timezone.utc)

            shanghai_dt = ts.astimezone(TOKEN_STATS_TZ)
            local_iso = shanghai_dt.isoformat()
            utc_iso = ts.isoformat().replace("+00:00", "Z")
            date_str = shanghai_dt.date().isoformat()

            uuid_val = None
            if 12 in p and p[12][0][0] == "bytes":
                try:
                    uuid_val = p[12][0][1].decode("utf-8", errors="replace").strip()
                except Exception:
                    pass

            model_name = gen_models.get(uuid_val) or "gemini-3.8-flash"

            usage_bytes = p.get(9, [(None, b"")])[0][1]
            u = token_stats_parse_proto(usage_bytes) if usage_bytes else {}
            input_tokens = token_stats_nonnegative_int(u.get(1, [(None, 0)])[0][1]) or 0
            cached_input = token_stats_nonnegative_int(u.get(2, [(None, 0)])[0][1]) or 0
            output_tokens = token_stats_nonnegative_int(u.get(3, [(None, 0)])[0][1]) or 0
            reasoning_tokens = token_stats_nonnegative_int(u.get(9, [(None, 0)])[0][1]) or 0
            total_tokens = input_tokens + cached_input + output_tokens

            coverage["usageCandidates"] += 1
            req_id = token_stats_request_id("antigravity", session_id, step_idx)

            candidates.append(
                {
                    "id": req_id,
                    "source": "antigravity",
                    "provider": "Antigravity",
                    "model": model_name,
                    "sessionId": session_id,
                    "sessionTitle": session_info.get("title") or session_info.get("preview") or "",
                    "project": session_info.get("project") or "Antigravity",
                    "timestamp": local_iso,
                    "timestampUtc": utc_iso,
                    "date": date_str,
                    "inputTokens": input_tokens,
                    "rawInputTokens": input_tokens + cached_input,
                    "cachedInputTokens": cached_input,
                    # Proto3 scalar omission represents zero inside a reported
                    # usage message; an absent message carries no cache data.
                    "cacheReadKnown": bool(usage_bytes),
                    "cacheCreationInputTokens": 0,
                    "cacheTokens": cached_input,
                    "cacheCreationKnown": False,
                    "outputTokens": output_tokens,
                    "reasoningTokens": reasoning_tokens,
                    "totalTokens": total_tokens,
                    "costUsd": 0.0,
                    "priceKnown": False,
                }
            )
        coverage["filesRead"] += 1
    except (OSError, sqlite3.Error) as exc:
        token_stats_record_error(coverage, path, exc)
    finally:
        if connection is not None:
            try:
                connection.close()
            except sqlite3.Error:
                pass
    return candidates


def token_stats_deepseek_files(root: Path | None = None) -> list[Path]:
    sessions_root = root or (TOKEN_STATS_DEEPSEEK_HOME / "sessions")
    if not sessions_root.exists() or not sessions_root.is_dir():
        return []

    files = []
    for current, dirs, names in os.walk(
        sessions_root,
        topdown=True,
        onerror=lambda _error: None,
        followlinks=False,
    ):
        dirs[:] = [
            name for name in dirs if not (Path(current) / name).is_symlink()
        ]
        for name in names:
            if name in {"session.jsonl.zstd", "session.v3.jsonl.zstd", "session.v4.jsonl.zstd"}:
                files.append(Path(current) / name)
    return sorted(files, key=lambda item: os.path.normcase(str(item)))


def token_stats_workbuddy_files(root: Path | None = None) -> list[Path]:
    projects_root = root or (TOKEN_STATS_WORKBUDDY_HOME / "projects")
    if not projects_root.exists() or not projects_root.is_dir():
        return []
    files = []
    for current, dirs, names in os.walk(
        projects_root,
        topdown=True,
        onerror=lambda _error: None,
        followlinks=False,
    ):
        dirs[:] = [
            name for name in dirs if not (Path(current) / name).is_symlink()
        ]
        for name in names:
            path = Path(current) / name
            if name.lower().endswith(".jsonl") and not path.is_symlink():
                files.append(path)
    return sorted(files, key=lambda item: os.path.normcase(str(item)))


def token_stats_cline_task_files(root: Path) -> list[Path]:
    tasks_root = root / "tasks"
    if not tasks_root.exists() or not tasks_root.is_dir():
        return []
    files = []
    try:
        task_dirs = list(tasks_root.iterdir())
    except OSError:
        return []
    for task_dir in task_dirs:
        if not task_dir.is_dir() or task_dir.is_symlink():
            continue
        path = task_dir / "ui_messages.json"
        if path.is_file() and not path.is_symlink():
            files.append(path)
    return sorted(files, key=lambda item: os.path.normcase(str(item)))


def token_stats_zstd_jsonl_objects(path: Path, coverage: dict):
    if zstandard is None:
        raise RuntimeError(
            "Python package 'zstandard' is unavailable; DeepSeek token logs were not read."
        )
    try:
        with path.open("rb") as source:
            decompressor = zstandard.ZstdDecompressor()
            with decompressor.stream_reader(source, read_across_frames=True) as reader:
                with io.TextIOWrapper(
                    reader, encoding="utf-8", errors="replace"
                ) as text_source:
                    for line in text_source:
                        coverage["lines"] += 1
                        try:
                            item = json.loads(line)
                        except (TypeError, ValueError):
                            coverage["invalidJsonLines"] += 1
                            coverage["badLines"] += 1
                            continue
                        if not isinstance(item, dict):
                            coverage["nonObjectLines"] += 1
                            coverage["badLines"] += 1
                            continue
                        coverage["objects"] += 1
                        yield item
        coverage["filesRead"] += 1
    except Exception as exc:
        token_stats_record_error(coverage, path, exc)


def token_stats_deepseek_timestamp(value) -> datetime | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value != value or value in {float("inf"), float("-inf")}:
            return None
        seconds = value / 1000 if abs(value) >= 100_000_000_000 else value
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            return None
    return chat_history_parse_timestamp(value)


def token_stats_calendar_bounds(daily: dict, today: date) -> tuple[date, date]:
    recent_start = today - timedelta(days=365)
    activity_days = list(daily)
    start = min(min(activity_days), recent_start) if activity_days else recent_start
    end = max(max(activity_days), today) if activity_days else today
    calendar_start = start - timedelta(days=start.weekday())
    calendar_end = end + timedelta(days=6 - end.weekday())
    return calendar_start, calendar_end


def token_stats_streak_fields(active_days: set[date], today: date) -> dict:
    current_days = []
    cursor = today
    while cursor in active_days:
        current_days.append(cursor)
        cursor -= timedelta(days=1)
    current_days.reverse()

    longest_days = []
    run = []
    previous = None
    for active_day in sorted(active_days):
        if previous is None or active_day == previous + timedelta(days=1):
            run.append(active_day)
        else:
            if len(run) > len(longest_days):
                longest_days = list(run)
            run = [active_day]
        previous = active_day
    if len(run) > len(longest_days):
        longest_days = list(run)

    return {
        "currentStreak": len(current_days),
        "currentStreakStart": current_days[0].isoformat() if current_days else None,
        "currentStreakEnd": current_days[-1].isoformat() if current_days else None,
        "longestStreak": len(longest_days),
        "longestStreakStart": longest_days[0].isoformat() if longest_days else None,
        "longestStreakEnd": longest_days[-1].isoformat() if longest_days else None,
    }


def token_stats_codex_daily_record() -> dict:
    return {
        "tokens": 0,
        "tokenEvents": 0,
        "sessions": set(),
        "userMessages": 0,
        "assistantMessages": 0,
        "toolCalls": 0,
    }


def token_stats_codex_file_day_record() -> dict:
    return {
        "tokens": 0,
        "tokenEvents": 0,
        "eventUserMessages": 0,
        "responseUserMessages": 0,
        "assistantMessages": 0,
        "toolCalls": 0,
    }


def token_stats_source_name(value) -> str:
    if not isinstance(value, str):
        return "unknown"
    cleaned = value.strip()
    if not cleaned:
        return "unknown"
    try:
        parsed = json.loads(cleaned)
    except (TypeError, ValueError):
        parsed = None
    if isinstance(parsed, dict) and "subagent" in parsed:
        return "subagent"
    return cleaned


def token_stats_load_codex_state(coverage: dict) -> tuple[dict[str, int], int, list[dict]]:
    state_sessions: dict[str, int] = {}
    state_total = 0
    source_totals: dict[str, dict] = defaultdict(lambda: {"threads": 0, "tokens": 0})
    db_path = TOKEN_STATS_CODEX_STATE_DB
    coverage["stateDb"] = str(db_path)
    coverage["stateDbExists"] = db_path.exists()
    coverage["stateRows"] = 0
    coverage["stateError"] = None
    if not db_path.exists():
        return state_sessions, state_total, []

    connection = None
    try:
        uri = f"{db_path.resolve().as_uri()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        rows = connection.execute(
            "select id, tokens_used, source, title, created_at, updated_at, cwd from threads"
        )
        for session_id, tokens, source, _title, _created, _updated, _cwd in rows:
            token_value = token_stats_nonnegative_int(tokens) or 0
            coverage["stateRows"] += 1
            state_total += token_value
            source_name = token_stats_source_name(source)
            source_totals[source_name]["threads"] += 1
            source_totals[source_name]["tokens"] += token_value
            if session_id:
                key = str(session_id)
                state_sessions[key] = max(state_sessions.get(key, 0), token_value)
    except (OSError, sqlite3.Error) as exc:
        coverage["stateError"] = str(exc)
    finally:
        if connection is not None:
            connection.close()

    sources = [
        {
            "source": source,
            "threads": values["threads"],
            "tokens": values["tokens"],
        }
        for source, values in source_totals.items()
    ]
    sources.sort(key=lambda item: (-item["tokens"], item["source"].lower()))
    return state_sessions, state_total, sources


def token_stats_process_codex_file(
    path: Path,
    coverage: dict,
) -> dict | None:
    session_id = ""
    previous_total = None
    lifetime_total = 0
    file_days: dict[date, dict] = defaultdict(token_stats_codex_file_day_record)
    failures_before = coverage["filesFailed"]

    for item in token_stats_jsonl_objects(path, coverage):
        item_type = item.get("type")
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        if item_type == "session_meta" and not session_id:
            raw_id = payload.get("id") or payload.get("session_id")
            if raw_id:
                session_id = str(raw_id)

        day = token_stats_shanghai_day(item.get("timestamp"))
        if item.get("timestamp") and day is None:
            coverage["invalidTimestamps"] += 1

        if item_type == "response_item":
            response_type = payload.get("type")
            if day is not None and response_type == "message" and payload.get("role") == "user":
                file_days[day]["responseUserMessages"] += 1
                coverage["responseUserMessages"] += 1
            elif day is not None and response_type == "message" and payload.get("role") == "assistant":
                file_days[day]["assistantMessages"] += 1
            elif day is not None and isinstance(response_type, str) and (
                response_type == "tool_call" or response_type.endswith("_call")
            ):
                file_days[day]["toolCalls"] += 1

        if item_type == "event_msg" and payload.get("type") == "user_message" and day is not None:
            file_days[day]["eventUserMessages"] += 1
            coverage["eventUserMessages"] += 1

        if item_type != "event_msg" or payload.get("type") != "token_count":
            continue

        coverage["tokenEvents"] += 1
        if day is not None:
            file_days[day]["tokenEvents"] += 1
        info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
        total_usage = (
            info.get("total_token_usage")
            if isinstance(info.get("total_token_usage"), dict)
            else {}
        )
        total = token_stats_nonnegative_int(total_usage.get("total_tokens"))
        if total is None:
            coverage["invalidTokenTotals"] += 1
            continue
        coverage["validTokenTotals"] += 1

        if previous_total is None:
            delta = total
        elif total >= previous_total:
            delta = total - previous_total
        else:
            coverage["counterResets"] += 1
            delta = total
        previous_total = total
        lifetime_total += delta
        if day is not None and delta:
            file_days[day]["tokens"] += delta

    if coverage["filesFailed"] > failures_before:
        return None

    session_key = session_id or f"file:{chat_history_session_key('codex-token', path)}"
    if not session_id:
        coverage["filesWithoutSessionId"] += 1

    normalized_days = {}
    user_message_total = 0
    for day, file_record in file_days.items():
        user_messages = (
            file_record["eventUserMessages"]
            if file_record["eventUserMessages"]
            else file_record["responseUserMessages"]
        )
        user_message_total += user_messages
        normalized_days[day] = {
            "tokens": file_record["tokens"],
            "tokenEvents": file_record["tokenEvents"],
            "userMessages": user_messages,
            "assistantMessages": file_record["assistantMessages"],
            "toolCalls": file_record["toolCalls"],
        }

    try:
        resolved_path = path.resolve(strict=False)
    except OSError:
        resolved_path = path.absolute()
    return {
        "sessionKey": session_key,
        "path": str(resolved_path),
        "pathKey": os.path.normcase(str(resolved_path)),
        "lifetimeTotal": lifetime_total,
        "days": normalized_days,
        "userMessages": user_message_total,
    }


def token_stats_codex_summary_file_coverage() -> dict:
    return {
        "filesRead": 0,
        "filesFailed": 0,
        "fileErrors": [],
        "lines": 0,
        "objects": 0,
        "invalidJsonLines": 0,
        "nonObjectLines": 0,
        "badLines": 0,
        "invalidTimestamps": 0,
        "tokenEvents": 0,
        "validTokenTotals": 0,
        "invalidTokenTotals": 0,
        "counterResets": 0,
        "filesWithoutSessionId": 0,
        "eventUserMessages": 0,
        "responseUserMessages": 0,
    }


def token_stats_codex_summary_file_incremental(
    path: Path,
    descriptor: dict,
    coverage: dict,
    cached: dict | None,
) -> dict | None:
    """Parse one Codex log while preserving state for append-only refreshes."""
    state = cached.get("parserState", {}) if isinstance(cached, dict) else {}
    contribution = cached.get("contribution", {}) if isinstance(cached, dict) else {}
    session_id = str(state.get("sessionId") or "")
    previous_total = token_stats_nonnegative_int(state.get("previousTotal"))
    lifetime_total = token_stats_nonnegative_int(contribution.get("lifetimeTotal")) or 0
    raw_days = {
        str(day): {
            "tokens": token_stats_nonnegative_int(record.get("tokens")) or 0,
            "tokenEvents": token_stats_nonnegative_int(record.get("tokenEvents")) or 0,
            "eventUserMessages": token_stats_nonnegative_int(
                record.get("eventUserMessages")
            )
            or 0,
            "responseUserMessages": token_stats_nonnegative_int(
                record.get("responseUserMessages")
            )
            or 0,
            "assistantMessages": token_stats_nonnegative_int(
                record.get("assistantMessages")
            )
            or 0,
            "toolCalls": token_stats_nonnegative_int(record.get("toolCalls")) or 0,
        }
        for day, record in contribution.get("rawDays", {}).items()
        if isinstance(day, str) and isinstance(record, dict)
    }
    start_offset = token_stats_nonnegative_int((cached or {}).get("offset")) or 0
    progress = {"offset": start_offset}
    failures_before = coverage["filesFailed"]

    for item in token_stats_incremental_jsonl_objects(
        path,
        coverage,
        start_offset,
        descriptor["size"],
        progress,
        count_file_read=not bool(cached),
    ):
        item_type = item.get("type")
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        if item_type == "session_meta" and not session_id:
            raw_id = payload.get("id") or payload.get("session_id")
            if raw_id:
                session_id = str(raw_id)

        day = token_stats_shanghai_day(item.get("timestamp"))
        if item.get("timestamp") and day is None:
            coverage["invalidTimestamps"] += 1
        day_key = day.isoformat() if day is not None else None
        response_type = payload.get("type") if item_type == "response_item" else None
        has_daily_event = bool(
            (
                item_type == "response_item"
                and (
                    (response_type == "message" and payload.get("role") in {"user", "assistant"})
                    or (
                        isinstance(response_type, str)
                        and (response_type == "tool_call" or response_type.endswith("_call"))
                    )
                )
            )
            or (
                item_type == "event_msg"
                and payload.get("type") in {"user_message", "token_count"}
            )
        )
        day_record = None
        if day_key is not None and has_daily_event:
            day_record = raw_days.setdefault(
                day_key,
                {
                    "tokens": 0,
                    "tokenEvents": 0,
                    "eventUserMessages": 0,
                    "responseUserMessages": 0,
                    "assistantMessages": 0,
                    "toolCalls": 0,
                },
            )

        if item_type == "response_item" and day_record is not None:
            if response_type == "message" and payload.get("role") == "user":
                day_record["responseUserMessages"] += 1
                coverage["responseUserMessages"] += 1
            elif response_type == "message" and payload.get("role") == "assistant":
                day_record["assistantMessages"] += 1
            elif isinstance(response_type, str) and (
                response_type == "tool_call" or response_type.endswith("_call")
            ):
                day_record["toolCalls"] += 1

        if (
            item_type == "event_msg"
            and payload.get("type") == "user_message"
            and day_record is not None
        ):
            day_record["eventUserMessages"] += 1
            coverage["eventUserMessages"] += 1

        if item_type != "event_msg" or payload.get("type") != "token_count":
            continue
        coverage["tokenEvents"] += 1
        if day_record is not None:
            day_record["tokenEvents"] += 1
        info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
        total_usage = (
            info.get("total_token_usage")
            if isinstance(info.get("total_token_usage"), dict)
            else {}
        )
        total = token_stats_nonnegative_int(total_usage.get("total_tokens"))
        if total is None:
            coverage["invalidTokenTotals"] += 1
            continue
        coverage["validTokenTotals"] += 1
        if previous_total is None:
            delta = total
        elif total >= previous_total:
            delta = total - previous_total
        else:
            coverage["counterResets"] += 1
            delta = total
        previous_total = total
        lifetime_total += delta
        if day_record is not None and delta:
            day_record["tokens"] += delta

    if coverage["filesFailed"] > failures_before:
        return None
    resolved_session = session_id or f"file:{chat_history_session_key('codex-token', path)}"
    if not session_id and not cached:
        coverage["filesWithoutSessionId"] += 1
    try:
        resolved_path = path.resolve(strict=False)
    except OSError:
        resolved_path = path.absolute()
    return {
        "signature": {
            "size": descriptor["size"],
            "mtimeNs": descriptor["mtimeNs"],
            "contextKey": descriptor.get("contextKey") or "",
        },
        "anchor": token_stats_file_anchor(path, descriptor["size"]),
        "offset": progress["offset"],
        "parserState": {
            "sessionId": session_id,
            "previousTotal": previous_total,
        },
        "contribution": {
            "sessionKey": resolved_session,
            "path": str(resolved_path),
            "pathKey": os.path.normcase(str(resolved_path)),
            "lifetimeTotal": lifetime_total,
            "rawDays": raw_days,
        },
        "coverage": coverage,
    }


def token_stats_codex_days(daily: dict, coverage: dict) -> tuple[list[dict], list[dict], dict]:
    today = datetime.now(TOKEN_STATS_TZ).date()
    calendar_start, calendar_end = token_stats_calendar_bounds(daily, today)
    coverage["calendarStart"] = calendar_start.isoformat()
    coverage["calendarEnd"] = calendar_end.isoformat()
    coverage["activityStart"] = min(daily).isoformat() if daily else None
    coverage["activityEnd"] = max(daily).isoformat() if daily else None

    days = []
    cursor = calendar_start
    while cursor <= calendar_end:
        record = daily.get(cursor) or token_stats_codex_daily_record()
        days.append(
            {
                "date": cursor.isoformat(),
                "tokens": record["tokens"],
                "tokenEvents": record["tokenEvents"],
                "sessions": len(record["sessions"]),
                "userMessages": record["userMessages"],
                "assistantMessages": record["assistantMessages"],
                "toolCalls": record["toolCalls"],
            }
        )
        cursor += timedelta(days=1)

    active_days = {day for day, record in daily.items() if record["tokens"] > 0}
    top_days = sorted(
        (item for item in days if item["tokens"] > 0),
        key=lambda item: (item["tokens"], item["date"]),
        reverse=True,
    )[:12]
    peak = top_days[0] if top_days else None
    metrics = {
        "tokenDays": len(active_days),
        "peakTokens": peak["tokens"] if peak else 0,
        "peakDate": peak["date"] if peak else None,
        **token_stats_streak_fields(active_days, today),
    }
    return days, top_days, metrics


def token_stats_codex_summary_contributions(
    descriptors: list[dict],
    connection: sqlite3.Connection | None,
    coverage: dict,
) -> tuple[dict[str, dict], dict[str, int]]:
    contributions: dict[str, dict] = {}
    session_file_counts: dict[str, int] = defaultdict(int)
    for original_descriptor in descriptors:
        # Summary counting does not depend on service tier, so config changes must not
        # invalidate gigabytes of otherwise unchanged summary cache entries.
        descriptor = {**original_descriptor, "contextKey": ""}
        path = Path(descriptor["path"])
        cached = token_stats_details_cache_load(
            connection, TOKEN_STATS_CODEX_SUMMARY_CACHE_SOURCE, descriptor["pathKey"]
        )
        cached_signature = cached.get("signature", {}) if isinstance(cached, dict) else {}
        comparison_descriptor = {
            **descriptor,
            "contextKey": str(cached_signature.get("contextKey") or ""),
        }
        cache_exact = token_stats_cache_is_exact(cached, comparison_descriptor)
        payload = None
        if cache_exact:
            payload = cached
            coverage["fileCacheHits"] += 1
        else:
            incremental_cached = None
            file_coverage = token_stats_codex_summary_file_coverage()
            start_offset = 0
            if token_stats_cache_is_append(cached, path, comparison_descriptor):
                incremental_cached = cached
                file_coverage = copy.deepcopy(cached.get("coverage", file_coverage))
                start_offset = token_stats_nonnegative_int(cached.get("offset")) or 0
                coverage["filesParsedIncrementally"] += 1
            coverage["filesParsed"] += 1
            coverage["bytesParsed"] += max(0, descriptor["size"] - start_offset)
            failed_before = file_coverage["filesFailed"]
            try:
                payload = token_stats_codex_summary_file_incremental(
                    path,
                    descriptor,
                    file_coverage,
                    incremental_cached,
                )
            except Exception as exc:
                if file_coverage["filesFailed"] == failed_before:
                    token_stats_record_error(file_coverage, path, exc)
                payload = None
            # Preserve failure and line coverage even when no cacheable payload exists.
            token_stats_merge_file_coverage(coverage, file_coverage)
            if payload is not None:
                token_stats_details_cache_save(
                    connection, TOKEN_STATS_CODEX_SUMMARY_CACHE_SOURCE, descriptor, payload
                )
                if connection is not None:
                    try:
                        connection.commit()
                    except (OSError, sqlite3.Error):
                        pass
        if payload is None:
            continue
        if cache_exact:
            token_stats_merge_file_coverage(coverage, payload.get("coverage", {}))
        contribution = payload.get("contribution")
        if not isinstance(contribution, dict):
            continue
        normalized_days = {}
        user_message_total = 0
        for day_text, raw_record in contribution.get("rawDays", {}).items():
            try:
                day = date.fromisoformat(day_text)
            except (TypeError, ValueError):
                continue
            user_messages = (
                raw_record.get("eventUserMessages", 0)
                if raw_record.get("eventUserMessages", 0)
                else raw_record.get("responseUserMessages", 0)
            )
            user_message_total += user_messages
            normalized_days[day] = {
                "tokens": token_stats_nonnegative_int(raw_record.get("tokens")) or 0,
                "tokenEvents": token_stats_nonnegative_int(raw_record.get("tokenEvents")) or 0,
                "userMessages": token_stats_nonnegative_int(user_messages) or 0,
                "assistantMessages": token_stats_nonnegative_int(
                    raw_record.get("assistantMessages")
                )
                or 0,
                "toolCalls": token_stats_nonnegative_int(raw_record.get("toolCalls")) or 0,
            }
        contribution = {
            **contribution,
            "days": normalized_days,
            "userMessages": user_message_total,
        }
        session_key = contribution["sessionKey"]
        session_file_counts[session_key] += 1
        coverage["rawDailyTokens"] += sum(
            record["tokens"] for record in contribution["days"].values()
        )
        coverage["rawDeduplicatedUserMessages"] += contribution["userMessages"]
        selected = contributions.get(session_key)
        if (
            selected is None
            or contribution["lifetimeTotal"] > selected["lifetimeTotal"]
            or (
                contribution["lifetimeTotal"] == selected["lifetimeTotal"]
                and contribution["pathKey"] < selected["pathKey"]
            )
        ):
            contributions[session_key] = contribution
    return contributions, session_file_counts


def token_stats_build_codex(
    descriptors: list[dict] | None = None,
    connection: sqlite3.Connection | None = None,
) -> dict:
    coverage = {
        "jsonlFiles": 0,
        "filesRead": 0,
        "filesFailed": 0,
        "fileErrors": [],
        "lines": 0,
        "objects": 0,
        "invalidJsonLines": 0,
        "nonObjectLines": 0,
        "badLines": 0,
        "invalidTimestamps": 0,
        "tokenEvents": 0,
        "validTokenTotals": 0,
        "invalidTokenTotals": 0,
        "counterResets": 0,
        "filesWithoutSessionId": 0,
        "eventUserMessages": 0,
        "responseUserMessages": 0,
        "deduplicatedUserMessages": 0,
        "rawDeduplicatedUserMessages": 0,
        "duplicateSessions": 0,
        "duplicateSessionFiles": 0,
        "selectedJsonlFiles": 0,
        "rawDailyTokens": 0,
        "deduplicatedDailyTokens": 0,
        "fileCacheHits": 0,
        "filesParsed": 0,
        "filesParsedIncrementally": 0,
        "bytesParsed": 0,
        "canonicalFileRule": "highest lifetimeTotal, then normalized absolute path ascending",
    }
    state_sessions, state_total, source_totals = token_stats_load_codex_state(coverage)
    owns_connection = connection is None
    if descriptors is None:
        descriptors = token_stats_details_manifest()["files"]["codex"]
    if connection is None:
        connection = token_stats_details_cache_connection()
    coverage["jsonlFiles"] = len(descriptors)

    live_path_keys = {descriptor["pathKey"] for descriptor in descriptors}
    try:
        contributions, session_file_counts = token_stats_codex_summary_contributions(
            descriptors, connection, coverage
        )
        token_stats_details_cache_cleanup(
            connection, TOKEN_STATS_CODEX_SUMMARY_CACHE_SOURCE, live_path_keys
        )
    finally:
        if connection is not None:
            try:
                connection.commit()
            except (OSError, sqlite3.Error):
                pass
        if owns_connection and connection is not None:
            try:
                connection.close()
            except sqlite3.Error:
                pass

    daily: dict[date, dict] = defaultdict(token_stats_codex_daily_record)
    session_lifetimes: dict[str, int] = {}
    for session_key, contribution in contributions.items():
        session_lifetimes[session_key] = contribution["lifetimeTotal"]
        coverage["deduplicatedUserMessages"] += contribution["userMessages"]
        for day, contribution_day in contribution["days"].items():
            day_record = daily[day]
            day_record["tokens"] += contribution_day["tokens"]
            day_record["tokenEvents"] += contribution_day["tokenEvents"]
            day_record["userMessages"] += contribution_day["userMessages"]
            day_record["assistantMessages"] += contribution_day["assistantMessages"]
            day_record["toolCalls"] += contribution_day["toolCalls"]
            if contribution_day["tokens"] > 0 or contribution_day["userMessages"] > 0:
                day_record["sessions"].add(session_key)

    coverage["duplicateSessions"] = sum(
        count > 1 for count in session_file_counts.values()
    )
    coverage["duplicateSessionFiles"] = sum(
        max(0, count - 1) for count in session_file_counts.values()
    )
    coverage["selectedJsonlFiles"] = len(contributions)

    canonical_sessions = set(state_sessions) | set(session_lifetimes)
    canonical_total = sum(
        max(state_sessions.get(session_id, 0), session_lifetimes.get(session_id, 0))
        for session_id in canonical_sessions
    )
    coverage["stateThreads"] = len(state_sessions)
    coverage["jsonlSessions"] = len(session_lifetimes)
    coverage["canonicalThreads"] = len(canonical_sessions)
    coverage["jsonlLifetimeTokens"] = sum(session_lifetimes.values())
    coverage["dailyTokenTotal"] = sum(record["tokens"] for record in daily.values())
    coverage["deduplicatedDailyTokens"] = max(
        0,
        coverage["rawDailyTokens"] - coverage["dailyTokenTotal"],
    )

    days, top_days, metrics = token_stats_codex_days(daily, coverage)
    return {
        "stateTotal": state_total,
        "canonicalTotal": canonical_total,
        "totalThreads": len(canonical_sessions),
        **metrics,
        "sourceTotals": source_totals,
        "days": days,
        "topDays": top_days,
        "coverage": coverage,
    }


def token_stats_claude_daily_record() -> dict:
    return {
        "sessions": set(),
        "authoritative_sessions": set(),
        "authoritative_token_sessions": set(),
        "restored_sessions": set(),
        "fallback_sources": set(),
        "userMessages": 0,
        "assistantMessages": 0,
        "restoredUserMessages": 0,
        "restoredMessages": 0,
        "restoredToolCalls": 0,
        "restored_token_total": 0,
        "restored_token_buckets": 0,
        "usageRows": 0,
        "nonzeroUsageRows": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_known_rows": 0,
        "cache_creation_unknown_rows": 0,
        "cache_read_known_rows": 0,
        "cache_read_unknown_rows": 0,
    }


def token_stats_claude_project_record() -> dict:
    return {
        "files": set(),
        "sessions": set(),
        "oldest": None,
        "newest": None,
        "userMessages": 0,
        "assistantMessages": 0,
        "usageRows": 0,
        "nonzeroUsageRows": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_known_rows": 0,
        "cache_creation_unknown_rows": 0,
        "cache_read_known_rows": 0,
        "cache_read_unknown_rows": 0,
        "restored_token_total": 0,
    }


def token_stats_add_claude_usage(record: dict, usage: dict) -> int:
    row_total = 0
    for field in TOKEN_STATS_CLAUDE_USAGE_FIELDS:
        value = token_stats_nonnegative_int(usage.get(field)) or 0
        record[field] += value
        row_total += value
    record["usageRows"] += 1
    if bool(usage.get("cacheReadKnown")):
        record["cache_read_known_rows"] += 1
    else:
        record["cache_read_unknown_rows"] += 1
    if bool(usage.get("cacheCreationKnown")):
        record["cache_creation_known_rows"] += 1
    else:
        record["cache_creation_unknown_rows"] += 1
    if row_total:
        record["nonzeroUsageRows"] += 1
    return row_total


def token_stats_claude_totals(record: dict) -> tuple[int, int]:
    restored = token_stats_nonnegative_int(record.get("restored_token_total")) or 0
    total = sum(record[field] for field in TOKEN_STATS_CLAUDE_USAGE_FIELDS) + restored
    without_cache_read = (
        record["input_tokens"]
        + record["output_tokens"]
        + record["cache_creation_input_tokens"]
        + restored
    )
    return total, without_cache_read


def token_stats_claude_legacy_files(
    claude_home: Path | None = None,
    projects_root: Path | None = None,
) -> list[Path]:
    home = claude_home or CHAT_HISTORY_CLAUDE_HOME
    root = projects_root or (home / "projects")
    files = [home.parent / ".claude.json", home / "stats-cache.json", home / "history.jsonl"]
    if root.exists() and root.is_dir():
        for current, dirs, names in os.walk(
            root, topdown=True, onerror=lambda _error: None, followlinks=False
        ):
            dirs[:] = [
                name for name in dirs if not (Path(current) / name).is_symlink()
            ]
            if "sessions-index.json" in names:
                files.append(Path(current) / "sessions-index.json")
    return sorted(
        (path for path in files if path.is_file() and not path.is_symlink()),
        key=lambda path: os.path.normcase(str(path)),
    )


def token_stats_claude_legacy_day(value) -> date | None:
    if isinstance(value, str):
        text = value.strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            try:
                return date.fromisoformat(text)
            except ValueError:
                return None
    parsed = token_stats_deepseek_timestamp(value)
    return parsed.astimezone(TOKEN_STATS_TZ).date() if parsed is not None else None


def token_stats_claude_legacy_coverage() -> dict:
    return {
        "firstStart": {
            "exists": False,
            "read": False,
            "error": None,
            "timestamp": None,
            "date": None,
            "installMethod": None,
        },
        "statsCache": {
            "exists": False,
            "read": False,
            "error": None,
            "dailyActivityRows": 0,
            "dailyTokenRows": 0,
            "tokenBucketsAdded": 0,
            "tokenBucketsSkipped": 0,
            "unallocatedCacheReadTokens": 0,
            "unallocatedCacheCreationTokens": 0,
            "totalSessions": 0,
            "totalMessages": 0,
            "firstSessionDate": None,
            "lastComputedDate": None,
        },
        "history": {
            "exists": False,
            "read": False,
            "error": None,
            "records": 0,
            "sessions": 0,
            "missingJsonlSessions": 0,
        },
        "sessionsIndex": {
            "files": 0,
            "filesRead": 0,
            "filesFailed": 0,
            "entries": 0,
            "sessions": 0,
            "missingJsonlSessions": 0,
        },
    }


def token_stats_claude_legacy_fallback(
    authoritative_sessions: set[str],
    authoritative_session_days: dict[str, set[date]],
    authoritative_token_buckets: set[tuple[date, str]],
    claude_home: Path | None = None,
    projects_root: Path | None = None,
) -> dict:
    """Read Claude's lossy local indexes without replacing authoritative JSONL data."""
    home = claude_home or CHAT_HISTORY_CLAUDE_HOME
    root = projects_root or (home / "projects")
    legacy_coverage = token_stats_claude_legacy_coverage()
    activities: dict[tuple[str, date], dict] = {}
    token_buckets: list[dict] = []
    daily_activity: dict[date, dict] = {}
    missing_sessions: set[str] = set()
    indexed_sessions: set[str] = set()
    history_sessions: set[str] = set()

    first_start_path = home.parent / ".claude.json"
    first_start_info = legacy_coverage["firstStart"]
    first_start_info["exists"] = first_start_path.is_file()
    if first_start_info["exists"]:
        try:
            first_start_payload = json.loads(
                first_start_path.read_text(encoding="utf-8", errors="replace")
            )
            if not isinstance(first_start_payload, dict):
                raise ValueError("Claude config must be an object")
            first_start_info["read"] = True
            raw_first_start = first_start_payload.get("firstStartTime")
            parsed_first_start = token_stats_deepseek_timestamp(raw_first_start)
            if parsed_first_start is not None:
                first_start_info["timestamp"] = parsed_first_start.isoformat().replace(
                    "+00:00", "Z"
                )
                first_start_info["date"] = parsed_first_start.astimezone(
                    TOKEN_STATS_TZ
                ).date().isoformat()
            install_method = first_start_payload.get("installMethod")
            if isinstance(install_method, str) and install_method.strip():
                first_start_info["installMethod"] = install_method.strip()
        except (OSError, TypeError, ValueError) as exc:
            first_start_info["error"] = str(exc)

    def add_activity(
        session_id: str,
        day: date | None,
        source: str,
        project: str = "",
        user_messages: int = 0,
    ) -> None:
        if not session_id or day is None or session_id in authoritative_sessions:
            return
        missing_sessions.add(session_id)
        key = (session_id, day)
        row = activities.setdefault(
            key,
            {
                "sessionId": session_id,
                "date": day,
                "sources": set(),
                "project": project,
                "userMessages": 0,
                "anonymous": False,
            },
        )
        row["sources"].add(source)
        if project and not row["project"]:
            row["project"] = project
        row["userMessages"] += max(0, user_messages)

    index_files = [
        path
        for path in token_stats_claude_legacy_files(home, root)
        if path.name == "sessions-index.json"
    ]
    legacy_coverage["sessionsIndex"]["files"] = len(index_files)
    for path in index_files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            entries = payload.get("entries") if isinstance(payload, dict) else None
            if not isinstance(entries, list):
                raise ValueError("sessions-index entries must be an array")
            legacy_coverage["sessionsIndex"]["filesRead"] += 1
        except (OSError, TypeError, ValueError):
            legacy_coverage["sessionsIndex"]["filesFailed"] += 1
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            session_id = str(entry.get("sessionId") or entry.get("session_id") or "")
            if not session_id:
                continue
            indexed_sessions.add(session_id)
            legacy_coverage["sessionsIndex"]["entries"] += 1
            project = str(entry.get("projectPath") or entry.get("project") or "")
            created_day = token_stats_claude_legacy_day(entry.get("created"))
            modified_day = token_stats_claude_legacy_day(
                entry.get("modified") or entry.get("fileMtime")
            )
            add_activity(session_id, created_day, "sessions-index", project)
            if modified_day != created_day:
                add_activity(session_id, modified_day, "sessions-index", project)

    history_path = home / "history.jsonl"
    history_info = legacy_coverage["history"]
    history_info["exists"] = history_path.is_file()
    if history_info["exists"]:
        try:
            for item in chat_history_jsonl_items(history_path):
                history_info["records"] += 1
                session_id = str(item.get("sessionId") or item.get("session_id") or "")
                if not session_id:
                    continue
                history_sessions.add(session_id)
                add_activity(
                    session_id,
                    token_stats_claude_legacy_day(item.get("timestamp") or item.get("ts")),
                    "history",
                    str(item.get("project") or ""),
                    1,
                )
            history_info["read"] = True
        except OSError as exc:
            history_info["error"] = str(exc)

    stats_path = home / "stats-cache.json"
    stats_info = legacy_coverage["statsCache"]
    stats_info["exists"] = stats_path.is_file()
    if stats_info["exists"]:
        try:
            payload = json.loads(stats_path.read_text(encoding="utf-8", errors="replace"))
            if not isinstance(payload, dict):
                raise ValueError("stats cache must be an object")
            stats_info["read"] = True
            stats_info["totalSessions"] = token_stats_nonnegative_int(
                payload.get("totalSessions")
            ) or 0
            stats_info["totalMessages"] = token_stats_nonnegative_int(
                payload.get("totalMessages")
            ) or 0
            first_day = token_stats_claude_legacy_day(payload.get("firstSessionDate"))
            stats_info["firstSessionDate"] = first_day.isoformat() if first_day else None
            last_day = token_stats_claude_legacy_day(payload.get("lastComputedDate"))
            stats_info["lastComputedDate"] = last_day.isoformat() if last_day else None
            activity_rows = payload.get("dailyActivity")
            for item in activity_rows if isinstance(activity_rows, list) else []:
                if not isinstance(item, dict):
                    continue
                day = token_stats_claude_legacy_day(item.get("date"))
                if day is None:
                    continue
                stats_info["dailyActivityRows"] += 1
                daily_activity[day] = {
                    "sessions": token_stats_nonnegative_int(item.get("sessionCount")) or 0,
                    "messages": token_stats_nonnegative_int(item.get("messageCount")) or 0,
                    "toolCalls": token_stats_nonnegative_int(item.get("toolCallCount")) or 0,
                }
            token_rows = payload.get("dailyModelTokens")
            for item in token_rows if isinstance(token_rows, list) else []:
                if not isinstance(item, dict):
                    continue
                day = token_stats_claude_legacy_day(item.get("date"))
                by_model = item.get("tokensByModel")
                if day is None or not isinstance(by_model, dict):
                    continue
                stats_info["dailyTokenRows"] += 1
                for model, raw_tokens in by_model.items():
                    tokens = token_stats_nonnegative_int(raw_tokens)
                    if tokens is None or tokens <= 0:
                        continue
                    bucket_key = (day, str(model or "unknown").strip().casefold())
                    if bucket_key in authoritative_token_buckets:
                        stats_info["tokenBucketsSkipped"] += 1
                        continue
                    token_buckets.append(
                        {"date": day, "model": str(model or "unknown"), "tokens": tokens}
                    )
                    stats_info["tokenBucketsAdded"] += 1
            model_usage = payload.get("modelUsage")
            for usage in model_usage.values() if isinstance(model_usage, dict) else []:
                if not isinstance(usage, dict):
                    continue
                stats_info["unallocatedCacheReadTokens"] += (
                    token_stats_nonnegative_int(
                        usage.get("cacheReadInputTokens")
                        or usage.get("cache_read_input_tokens")
                    )
                    or 0
                )
                stats_info["unallocatedCacheCreationTokens"] += (
                    token_stats_nonnegative_int(
                        usage.get("cacheCreationInputTokens")
                        or usage.get("cache_creation_input_tokens")
                    )
                    or 0
                )
        except (OSError, TypeError, ValueError) as exc:
            stats_info["error"] = str(exc)

    known_by_day: dict[date, set[str]] = defaultdict(set)
    for session_id, days in authoritative_session_days.items():
        for day in days:
            known_by_day[day].add(session_id)
    for session_id, day in activities:
        known_by_day[day].add(session_id)
    anonymous_activity_rows = 0
    for day, reported in daily_activity.items():
        deficit = max(0, reported["sessions"] - len(known_by_day[day]))
        for index in range(deficit):
            session_id = f"legacy-stats:{day.isoformat()}:{index + 1}"
            activities[(session_id, day)] = {
                "sessionId": session_id,
                "date": day,
                "sources": {"stats-cache"},
                "project": "",
                "userMessages": 0,
                "anonymous": True,
            }
            anonymous_activity_rows += 1

    history_info["sessions"] = len(history_sessions)
    history_info["missingJsonlSessions"] = len(history_sessions - authoritative_sessions)
    legacy_coverage["sessionsIndex"]["sessions"] = len(indexed_sessions)
    legacy_coverage["sessionsIndex"]["missingJsonlSessions"] = len(
        indexed_sessions - authoritative_sessions
    )
    normalized_activities = []
    for row in activities.values():
        normalized_activities.append(
            {
                **row,
                "date": row["date"].isoformat(),
                "sources": sorted(row["sources"]),
            }
        )
    normalized_activities.sort(key=lambda row: (row["date"], row["sessionId"]))
    normalized_tokens = [
        {**row, "date": row["date"].isoformat()} for row in token_buckets
    ]
    normalized_tokens.sort(key=lambda row: (row["date"], row["model"]))
    normalized_daily = {
        day.isoformat(): values for day, values in sorted(daily_activity.items())
    }
    return {
        "activities": normalized_activities,
        "tokenBuckets": normalized_tokens,
        "dailyActivity": normalized_daily,
        "missingSessionIds": sorted(missing_sessions),
        "anonymousActivitySessionDays": anonymous_activity_rows,
        "coverage": legacy_coverage,
    }


def token_stats_update_bounds(record: dict, timestamp) -> datetime | None:
    parsed = chat_history_parse_timestamp(timestamp)
    if parsed is None:
        return None
    if record["oldest"] is None or parsed < record["oldest"]:
        record["oldest"] = parsed
    if record["newest"] is None or parsed > record["newest"]:
        record["newest"] = parsed
    return parsed


def token_stats_claude_project_name(path: Path, projects_root: Path) -> str:
    try:
        parts = path.relative_to(projects_root).parts
    except (OSError, ValueError):
        return "(root)"
    return parts[0] if parts else "(root)"


def token_stats_process_claude_file(
    path: Path,
    projects_root: Path,
    daily: dict,
    projects: dict,
    coverage: dict,
    authoritative_sessions: set[str] | None = None,
    authoritative_session_days: dict[str, set[date]] | None = None,
    authoritative_token_buckets: set[tuple[date, str]] | None = None,
    usage_candidates: dict[str, dict] | None = None,
    message_candidates: dict[str, dict] | None = None,
) -> None:
    project_name = token_stats_claude_project_name(path, projects_root)
    project_record = projects[project_name]
    project_record["files"].add(str(path))
    fallback_session = f"file:{chat_history_session_key('claude-token', path)}"
    explicit_session_ids = set()
    fallback_days = set()

    for line_index, item in enumerate(token_stats_jsonl_objects(path, coverage), start=1):
        timestamp = item.get("timestamp")
        parsed_timestamp = token_stats_update_bounds(project_record, timestamp)
        day = (
            parsed_timestamp.astimezone(TOKEN_STATS_TZ).date()
            if parsed_timestamp is not None
            else None
        )
        if timestamp and day is None:
            coverage["invalidTimestamps"] += 1

        raw_session_id = item.get("sessionId") or item.get("session_id")
        session_id = str(raw_session_id) if raw_session_id else fallback_session
        if raw_session_id:
            explicit_session_ids.add(session_id)
        else:
            coverage["recordsUsingFileSessionFallback"] += 1
            if day is not None:
                fallback_days.add(day)
        project_record["sessions"].add(session_id)
        if authoritative_sessions is not None:
            authoritative_sessions.add(session_id)
        if day is not None:
            daily[day]["sessions"].add(session_id)
            daily[day]["authoritative_sessions"].add(session_id)
            if authoritative_session_days is not None:
                authoritative_session_days[session_id].add(day)

        message = item.get("message") if isinstance(item.get("message"), dict) else {}
        role = message.get("role")
        if role in {"user", "assistant"}:
            if message_candidates is None:
                project_record[f"{role}Messages"] += 1
                if day is not None:
                    daily[day][f"{role}Messages"] += 1
            else:
                message_id = message.get("id") or item.get("uuid")
                message_key = (
                    f"{session_id}:message:{message_id}"
                    if message_id
                    else f"{session_id}:fallback:{os.path.normcase(str(path))}:{line_index}"
                )
                candidate = {
                    "role": role,
                    "day": day,
                    "project": project_name,
                    "timestamp": (
                        parsed_timestamp.isoformat() if parsed_timestamp is not None else ""
                    ),
                }
                selected = message_candidates.get(message_key)
                if selected is None or candidate["timestamp"] > selected["timestamp"]:
                    message_candidates[message_key] = candidate

        usage = message.get("usage")
        if not isinstance(usage, dict):
            continue
        coverage["usageRows"] += 1
        normalized_usage = {
            field: token_stats_nonnegative_int(usage.get(field)) or 0
            for field in TOKEN_STATS_CLAUDE_USAGE_FIELDS
        }
        cache_creation, cache_creation_known = token_stats_known_usage_value(
            usage,
            "cache_creation_input_tokens",
            "cache_write_input_tokens",
        )
        normalized_usage["cache_creation_input_tokens"] = cache_creation
        normalized_usage["cacheCreationKnown"] = cache_creation_known
        normalized_usage["cacheReadKnown"] = token_stats_known_usage_value(
            usage, "cache_read_input_tokens"
        )[1]
        row_total = sum(
            normalized_usage[field] for field in TOKEN_STATS_CLAUDE_USAGE_FIELDS
        )
        coverage["nonzeroUsageRows"] += int(row_total > 0)
        if row_total <= 0:
            continue
        if usage_candidates is None:
            token_stats_add_claude_usage(project_record, normalized_usage)
            if day is not None:
                token_stats_add_claude_usage(daily[day], normalized_usage)
                if authoritative_token_buckets is not None:
                    model = str(message.get("model") or "unknown").strip().casefold()
                    authoritative_token_buckets.add((day, model))
            else:
                coverage["usageRowsWithoutValidDate"] += 1
            continue
        message_id = message.get("id")
        if message_id:
            dedupe_key = f"{session_id}:message:{message_id}"
        else:
            fallback_id = item.get("uuid") or f"{os.path.normcase(str(path))}:{line_index}"
            dedupe_key = f"{session_id}:fallback:{fallback_id}"
        candidate = {
            "usage": normalized_usage,
            "total": row_total,
            "timestamp": parsed_timestamp.isoformat() if parsed_timestamp is not None else "",
            "day": day,
            "sessionId": session_id,
            "project": project_name,
            "model": str(message.get("model") or "unknown").strip().casefold(),
        }
        selected = usage_candidates.get(dedupe_key)
        if selected is None or (candidate["total"], candidate["timestamp"]) > (
            selected["total"],
            selected["timestamp"],
        ):
            usage_candidates[dedupe_key] = candidate

    if len(explicit_session_ids) == 1 and fallback_session in project_record["sessions"]:
        resolved_session = next(iter(explicit_session_ids))
        project_record["sessions"].discard(fallback_session)
        project_record["sessions"].add(resolved_session)
        if authoritative_sessions is not None:
            authoritative_sessions.discard(fallback_session)
            authoritative_sessions.add(resolved_session)
        if authoritative_session_days is not None:
            authoritative_session_days[resolved_session].update(
                authoritative_session_days.pop(fallback_session, set())
            )
        for fallback_day in fallback_days:
            daily[fallback_day]["sessions"].discard(fallback_session)
            daily[fallback_day]["sessions"].add(resolved_session)
            daily[fallback_day]["authoritative_sessions"].discard(fallback_session)
            daily[fallback_day]["authoritative_sessions"].add(resolved_session)


def token_stats_claude_days(daily: dict, coverage: dict) -> tuple[list[dict], list[dict], dict]:
    today = datetime.now(TOKEN_STATS_TZ).date()
    calendar_start, calendar_end = token_stats_calendar_bounds(daily, today)
    coverage["calendarStart"] = calendar_start.isoformat()
    coverage["calendarEnd"] = calendar_end.isoformat()
    coverage["activityStart"] = min(daily).isoformat() if daily else None
    coverage["activityEnd"] = max(daily).isoformat() if daily else None

    days = []
    cursor = calendar_start
    while cursor <= calendar_end:
        record = daily.get(cursor) or token_stats_claude_daily_record()
        tokens, tokens_without_cache_read = token_stats_claude_totals(record)
        authoritative_session_ids = record["authoritative_sessions"]
        authoritative_sessions = len(authoritative_session_ids)
        session_count = len(record["sessions"])
        restored_sessions = max(0, session_count - authoritative_sessions)
        unknown_authoritative_sessions = len(
            authoritative_session_ids - record["authoritative_token_sessions"]
        )
        unknown_token_sessions = restored_sessions + unknown_authoritative_sessions
        days.append(
            {
                "date": cursor.isoformat(),
                "tokens": tokens,
                "tokensWithoutCacheRead": tokens_without_cache_read,
                "sessions": session_count,
                "authoritativeSessions": authoritative_sessions,
                "restoredSessions": restored_sessions,
                "activityOnlySessions": restored_sessions,
                "unknownTokenSessions": unknown_token_sessions,
                "restoredTokens": record["restored_token_total"],
                "restoredTokenBuckets": record["restored_token_buckets"],
                "fallbackSources": sorted(record["fallback_sources"]),
                "hasUnknownTokenActivity": unknown_token_sessions > 0,
                "userMessages": record["userMessages"],
                "assistantMessages": record["assistantMessages"],
                "restoredUserMessages": record["restoredUserMessages"],
                "restoredMessages": record["restoredMessages"],
                "restoredToolCalls": record["restoredToolCalls"],
                "usageRows": record["usageRows"],
                "inputTokens": record["input_tokens"],
                "outputTokens": record["output_tokens"],
                "cacheCreationTokens": record["cache_creation_input_tokens"],
                "cacheReadTokens": record["cache_read_input_tokens"],
                "cacheReadKnownRequestCount": record["cache_read_known_rows"],
                "cacheReadUnknownRequestCount": record["cache_read_unknown_rows"],
                "cacheReadCoverage": token_stats_cache_coverage(
                    record["cache_read_known_rows"], record["usageRows"]
                ),
                "cacheCreationKnownRequestCount": record[
                    "cache_creation_known_rows"
                ],
                "cacheCreationUnknownRequestCount": record[
                    "cache_creation_unknown_rows"
                ],
                "cacheCreationCoverage": (
                    "full"
                    if record["usageRows"] > 0
                    and record["cache_creation_known_rows"] == record["usageRows"]
                    else "partial"
                    if record["cache_creation_known_rows"] > 0
                    else "none"
                ),
            }
        )
        cursor += timedelta(days=1)

    token_days = {
        day
        for day, record in daily.items()
        if token_stats_claude_totals(record)[0] > 0
    }
    active_days = {day for day, record in daily.items() if record["sessions"]}
    top_days = sorted(
        (item for item in days if item["tokens"] > 0),
        key=lambda item: (item["tokens"], item["date"]),
        reverse=True,
    )[:12]
    peak = top_days[0] if top_days else None
    metrics = {
        "tokenDays": len(token_days),
        "activityDays": len(active_days),
        "peakTokens": peak["tokens"] if peak else 0,
        "peakDate": peak["date"] if peak else None,
        **token_stats_streak_fields(active_days, today),
    }
    metrics.update(token_stats_daily_cache_read_coverage(days))
    return days, top_days, metrics


def token_stats_build_claude() -> dict:
    coverage = {
        "jsonlFiles": 0,
        "filesRead": 0,
        "filesFailed": 0,
        "fileErrors": [],
        "lines": 0,
        "objects": 0,
        "invalidJsonLines": 0,
        "nonObjectLines": 0,
        "badLines": 0,
        "invalidTimestamps": 0,
        "recordsUsingFileSessionFallback": 0,
        "usageRows": 0,
        "nonzeroUsageRows": 0,
        "usageRowsWithoutValidDate": 0,
    }
    projects_root = CHAT_HISTORY_CLAUDE_HOME / "projects"
    files = chat_history_jsonl_files(projects_root)
    coverage["jsonlFiles"] = len(files)
    daily: dict[date, dict] = defaultdict(token_stats_claude_daily_record)
    projects: dict[str, dict] = defaultdict(token_stats_claude_project_record)
    authoritative_sessions: set[str] = set()
    authoritative_session_days: dict[str, set[date]] = defaultdict(set)
    authoritative_token_buckets: set[tuple[date, str]] = set()
    usage_candidates: dict[str, dict] = {}
    message_candidates: dict[str, dict] = {}

    for path in files:
        failed_before = coverage["filesFailed"]
        try:
            token_stats_process_claude_file(
                path,
                projects_root,
                daily,
                projects,
                coverage,
                authoritative_sessions,
                authoritative_session_days,
                authoritative_token_buckets,
                usage_candidates,
                message_candidates,
            )
        except Exception as exc:
            if coverage["filesFailed"] == failed_before:
                token_stats_record_error(coverage, path, exc)

    for candidate in message_candidates.values():
        field = f"{candidate['role']}Messages"
        projects[candidate["project"]][field] += 1
        if candidate["day"] is not None:
            daily[candidate["day"]][field] += 1
    for candidate in usage_candidates.values():
        token_stats_add_claude_usage(
            projects[candidate["project"]], candidate["usage"]
        )
        if candidate["day"] is not None:
            token_stats_add_claude_usage(daily[candidate["day"]], candidate["usage"])
            daily[candidate["day"]]["authoritative_token_sessions"].add(
                candidate["sessionId"]
            )
            authoritative_token_buckets.add(
                (candidate["day"], candidate["model"])
            )
        else:
            coverage["usageRowsWithoutValidDate"] += 1
    coverage["canonicalUsageRows"] = len(usage_candidates)
    coverage["duplicateUsageRows"] = max(
        0, coverage["nonzeroUsageRows"] - len(usage_candidates)
    )
    coverage["canonicalMessageRows"] = len(message_candidates)

    fallback = token_stats_claude_legacy_fallback(
        authoritative_sessions,
        authoritative_session_days,
        authoritative_token_buckets,
        CHAT_HISTORY_CLAUDE_HOME,
        projects_root,
    )
    fallback_session_projects: dict[str, str] = {}
    for activity in fallback["activities"]:
        day = date.fromisoformat(activity["date"])
        session_id = activity["sessionId"]
        record = daily[day]
        record["sessions"].add(session_id)
        record["restored_sessions"].add(session_id)
        record["fallback_sources"].update(activity["sources"])
        record["userMessages"] += activity["userMessages"]
        record["restoredUserMessages"] += activity["userMessages"]
        if activity.get("project"):
            fallback_session_projects.setdefault(session_id, activity["project"])
    for day_text, activity in fallback["dailyActivity"].items():
        record = daily[date.fromisoformat(day_text)]
        record["fallback_sources"].add("stats-cache")
        known_messages = record["userMessages"] + record["assistantMessages"]
        record["restoredMessages"] = max(
            record["restoredMessages"], activity["messages"] - known_messages, 0
        )
        record["restoredToolCalls"] = max(
            record["restoredToolCalls"], activity["toolCalls"]
        )
    restored_token_total = 0
    for bucket in fallback["tokenBuckets"]:
        record = daily[date.fromisoformat(bucket["date"])]
        record["restored_token_total"] += bucket["tokens"]
        record["restored_token_buckets"] += 1
        record["fallback_sources"].add("stats-cache")
        restored_token_total += bucket["tokens"]

    for session_id, project_path in fallback_session_projects.items():
        project_name = chat_history_normalize_path(project_path) or "(legacy)"
        projects[project_name]["sessions"].add(session_id)
    if restored_token_total:
        legacy_project = projects["(legacy stats cache; project unknown)"]
        legacy_project["restored_token_total"] = restored_token_total

    project_rows = []
    all_sessions = set()
    for project_name, record in projects.items():
        tokens, tokens_without_cache_read = token_stats_claude_totals(record)
        all_sessions.update(record["sessions"])
        project_rows.append(
            {
                "project": project_name,
                "files": len(record["files"]),
                "sessions": len(record["sessions"]),
                "userMessages": record["userMessages"],
                "assistantMessages": record["assistantMessages"],
                "usageRows": record["usageRows"],
                "inputTokens": record["input_tokens"],
                "outputTokens": record["output_tokens"],
                "cacheCreationTokens": record["cache_creation_input_tokens"],
                "cacheReadTokens": record["cache_read_input_tokens"],
                "tokens": tokens,
                "tokensWithoutCacheRead": tokens_without_cache_read,
                "restoredTokens": record["restored_token_total"],
                "fallback": bool(record["restored_token_total"]),
                "oldest": token_stats_local_iso(record["oldest"]),
                "newest": token_stats_local_iso(record["newest"]),
            }
        )
    project_rows.sort(key=lambda item: (-item["tokens"], item["project"].lower()))

    project_total_with_cache_read = sum(project["tokens"] for project in project_rows)
    project_total_without_cache_read = sum(
        project["tokensWithoutCacheRead"] for project in project_rows
    )
    all_sessions.update(fallback["missingSessionIds"])
    daily_token_total = sum(
        token_stats_claude_totals(record)[0] for record in daily.values()
    )
    daily_without_cache_read = sum(
        token_stats_claude_totals(record)[1] for record in daily.values()
    )
    total_with_cache_read = daily_token_total
    total_without_cache_read = daily_without_cache_read
    coverage["projectTokenTotal"] = project_total_with_cache_read
    coverage["projectTokenTotalWithoutCacheRead"] = project_total_without_cache_read
    coverage["dailyTokenTotal"] = daily_token_total
    coverage["undatedAuthoritativeTokens"] = max(
        0, project_total_with_cache_read - daily_token_total
    )
    coverage["projects"] = len(project_rows)
    coverage["sessions"] = len(all_sessions)
    coverage["authoritativeSessions"] = len(authoritative_sessions)
    coverage["legacyFallback"] = fallback["coverage"]
    coverage["fallbackSessionsAdded"] = len(fallback["missingSessionIds"])
    coverage["fallbackActivityDays"] = len(
        {activity["date"] for activity in fallback["activities"]}
        | set(fallback["dailyActivity"])
    )
    coverage["fallbackTokenBucketsAdded"] = len(fallback["tokenBuckets"])
    coverage["fallbackTokenTotal"] = restored_token_total
    coverage["activityOnlySessions"] = len(fallback["missingSessionIds"])
    coverage["missingTranscriptSessions"] = len(fallback["missingSessionIds"])
    coverage["anonymousActivitySessionDays"] = fallback[
        "anonymousActivitySessionDays"
    ]
    coverage["countingRule"] = (
        "projects/**/*.jsonl is authoritative; legacy sessions are added only when their "
        "sessionId is absent; stats-cache date+model token buckets are added only when the "
        "same authoritative JSONL bucket is absent; aggregate activity never invents tokens"
    )
    coverage["restoredTokenBreakdownUnavailable"] = restored_token_total > 0
    coverage["unallocatedCacheReadTokens"] = fallback["coverage"]["statsCache"][
        "unallocatedCacheReadTokens"
    ]
    coverage["unallocatedCacheCreationTokens"] = fallback["coverage"]["statsCache"][
        "unallocatedCacheCreationTokens"
    ]

    days, top_days, metrics = token_stats_claude_days(daily, coverage)
    return {
        "canonicalTotal": total_with_cache_read,
        "totalWithCacheRead": total_with_cache_read,
        "totalWithoutCacheRead": total_without_cache_read,
        "totalSessions": len(all_sessions),
        "totalProjects": len(project_rows),
        **metrics,
        "days": days,
        "topDays": top_days,
        "projects": project_rows,
        "coverage": coverage,
    }


def token_stats_deepseek_request_usage(usage: dict) -> dict | None:
    if not isinstance(usage, dict):
        return None
    input_tokens = token_stats_nonnegative_int(usage.get("inputTokens")) or 0
    output_tokens = token_stats_nonnegative_int(usage.get("outputTokens")) or 0
    cache_read, cache_read_known = token_stats_known_usage_value(usage, "cacheReadTokens")
    cache_creation, cache_creation_known = token_stats_known_usage_value(
        usage,
        "cacheWriteTokens",
        "cacheCreationTokens",
        "cacheCreationInputTokens",
    )
    reasoning = token_stats_nonnegative_int(usage.get("reasoningTokens")) or 0
    total = input_tokens + cache_read + cache_creation + output_tokens
    if not any((input_tokens, output_tokens, cache_read, cache_creation, reasoning)):
        return None
    return {
        "inputTokens": input_tokens,
        "rawInputTokens": input_tokens + cache_read + cache_creation,
        "outputTokens": output_tokens,
        "cachedInputTokens": cache_read,
        "cacheReadKnown": cache_read_known,
        "cacheCreationInputTokens": cache_creation,
        "cacheWriteTokens": cache_creation,
        "cacheCreationKnown": cache_creation_known,
        "cacheWriteKnown": cache_creation_known,
        "cacheTokens": cache_read + cache_creation,
        "reasoningTokens": reasoning,
        "totalTokens": total,
    }


def token_stats_deepseek_file_candidates(path: Path, coverage: dict) -> list[dict]:
    fallback_session = f"file:{chat_history_session_key('deepseek-token', path)}"
    session_id = ""
    header_provider = ""
    header_model = ""
    line_index = 0
    selected_rows: dict[str, dict] = {}

    for item in token_stats_zstd_jsonl_objects(path, coverage):
        line_index += 1
        item_type = item.get("type")
        data = item.get("data") if isinstance(item.get("data"), dict) else {}
        if item_type == "session" and not session_id:
            raw_session_id = item.get("id")
            if raw_session_id:
                session_id = str(raw_session_id)
        if item_type == "request/header":
            header = data.get("header") if isinstance(data.get("header"), dict) else {}
            config = header.get("config") if isinstance(header.get("config"), dict) else {}
            if isinstance(config.get("provider"), str) and config["provider"].strip():
                header_provider = config["provider"].strip()
            if isinstance(config.get("model"), str) and config["model"].strip():
                header_model = config["model"].strip()
        if item_type != "assistant/message":
            continue

        request_usage = token_stats_deepseek_request_usage(data.get("usage"))
        if request_usage is None:
            continue
        coverage["usageCandidates"] += 1
        message = data.get("message") if isinstance(data.get("message"), dict) else {}
        source = (
            message.get("source")
            if isinstance(message.get("source"), dict)
            else {}
        )
        current_session = session_id or fallback_session
        message_id = message.get("id")
        if message_id:
            dedupe_key = f"{current_session}:message:{message_id}"
        else:
            coverage["usageRowsWithoutMessageId"] += 1
            fallback_id = item.get("seq") or item.get("time") or line_index
            dedupe_key = (
                f"{current_session}:fallback:"
                f"{os.path.normcase(str(path))}:{fallback_id}"
            )

        raw_timestamp = item.get("time")
        if raw_timestamp is None:
            raw_timestamp = item.get("time0")
        parsed_timestamp = token_stats_deepseek_timestamp(raw_timestamp)
        local_timestamp = None
        local_date = None
        if parsed_timestamp is not None:
            local = parsed_timestamp.astimezone(TOKEN_STATS_TZ)
            local_timestamp = local.isoformat()
            local_date = local.date().isoformat()
        else:
            if raw_timestamp is not None:
                coverage["invalidTimestamps"] += 1
            coverage["usageRowsWithoutValidDate"] += 1

        provider = source.get("provider") or header_provider or "DeepSeek"
        model = source.get("model") or header_model or "unknown"
        provider = provider.strip() if isinstance(provider, str) and provider.strip() else "DeepSeek"
        model = model.strip() if isinstance(model, str) and model.strip() else "unknown"
        row = {
            "id": token_stats_request_id("deepseek", dedupe_key),
            "timestamp": local_timestamp,
            "timestampUtc": (
                parsed_timestamp.isoformat().replace("+00:00", "Z")
                if parsed_timestamp is not None
                else None
            ),
            "date": local_date,
            "provider": provider,
            "modelProvider": provider,
            "model": model,
            "tier": "unknown",
            "requestedTier": "unknown",
            "tierSource": "unknown",
            "source": "deepseek",
            "threadSource": "deepseek-client",
            "sessionLogVersion": (
                int(match.group(1))
                if (match := re.fullmatch(r"session\.v(\d+)\.jsonl\.zstd", path.name))
                else 0
            ),
            "sessionId": current_session,
            "project": "DeepSeek client",
            **request_usage,
            "costUsd": 0,
            "cost": None,
            "priceKnown": False,
        }
        selected = selected_rows.get(dedupe_key)
        candidate_key = token_stats_deepseek_candidate_key(row)
        selected_key = (
            token_stats_deepseek_candidate_key(selected) if selected else None
        )
        if selected is None or candidate_key > selected_key:
            selected_rows[dedupe_key] = row

    if session_id:
        normalized_rows = {}
        fallback_prefix = f"{fallback_session}:"
        for key, row in selected_rows.items():
            normalized_key = (
                f"{session_id}:{key[len(fallback_prefix):]}"
                if key.startswith(fallback_prefix)
                else key
            )
            row["sessionId"] = session_id
            row["id"] = token_stats_request_id("deepseek", normalized_key)
            normalized_rows[normalized_key] = row
        selected_rows = normalized_rows

    return [
        {"key": key, "row": row}
        for key, row in selected_rows.items()
    ]


def token_stats_deepseek_candidate_key(row: dict) -> tuple:
    # Repeated migrated messages carry the same id, timestamp and total. Prefer
    # a complete usage breakdown rather than whichever log was scanned first.
    known_fields = int(token_stats_cache_read_known(row)) + int(
        bool(row.get("cacheCreationKnown", row.get("cacheWriteKnown", False)))
    )
    version = row.get("sessionLogVersion", 0)
    return (row["totalTokens"], row.get("timestampUtc") or "", known_fields, version)


def token_stats_cline_request_usage(payload: dict) -> dict | None:
    if not isinstance(payload, dict):
        return None
    fields = ("tokensIn", "tokensOut", "cacheReads", "cacheWrites")
    if not any(field in payload for field in fields):
        return None
    input_tokens = token_stats_nonnegative_int(payload.get("tokensIn")) or 0
    output_tokens = token_stats_nonnegative_int(payload.get("tokensOut")) or 0
    cache_read, cache_read_known = token_stats_known_usage_value(payload, "cacheReads")
    cache_creation, cache_creation_known = token_stats_known_usage_value(
        payload, "cacheWrites"
    )
    total = input_tokens + output_tokens + cache_read + cache_creation
    if total <= 0:
        return None
    return {
        "inputTokens": input_tokens,
        "rawInputTokens": input_tokens + cache_read + cache_creation,
        "outputTokens": output_tokens,
        "cachedInputTokens": cache_read,
        "cacheReadKnown": cache_read_known,
        "cacheCreationInputTokens": cache_creation,
        "cacheWriteTokens": cache_creation,
        "cacheCreationKnown": cache_creation_known,
        "cacheWriteKnown": cache_creation_known,
        "cacheTokens": cache_read + cache_creation,
        "reasoningTokens": 0,
        "totalTokens": total,
    }


def token_stats_cline_is_deepseek(provider, model) -> bool:
    provider_name = str(provider or "").strip().casefold()
    model_name = str(model or "").strip().casefold()
    return "deepseek" in provider_name or "deepseek" in model_name


def token_stats_cline_model_events(path: Path, coverage: dict) -> list[dict]:
    metadata_path = path.parent / "task_metadata.json"
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8", errors="replace"))
    except FileNotFoundError:
        coverage["metadataFilesMissing"] += 1
        return []
    except (OSError, TypeError, ValueError) as exc:
        coverage["metadataFilesFailed"] += 1
        if len(coverage["fileErrors"]) < 10:
            coverage["fileErrors"].append({"file": str(metadata_path), "error": str(exc)})
        return []
    usage = payload.get("model_usage") if isinstance(payload, dict) else None
    events = []
    for item in usage if isinstance(usage, list) else []:
        if not isinstance(item, dict):
            continue
        parsed = token_stats_deepseek_timestamp(item.get("ts"))
        if parsed is None:
            coverage["invalidModelTimestamps"] += 1
            continue
        provider = item.get("model_provider_id") or item.get("providerId")
        model = item.get("model_id") or item.get("modelId")
        if not isinstance(provider, str) or not provider.strip():
            provider = "unknown"
        if not isinstance(model, str) or not model.strip():
            model = "unknown"
        events.append(
            {
                "timestamp": parsed.timestamp(),
                "provider": provider.strip(),
                "model": model.strip(),
            }
        )
    events.sort(key=lambda item: item["timestamp"])
    coverage["modelEvents"] += len(events)
    coverage["metadataFilesRead"] += 1
    return events


def token_stats_cline_file_candidates(
    path: Path, provider_label: str, coverage: dict
) -> list[dict]:
    task_id = path.parent.name
    model_events = token_stats_cline_model_events(path, coverage)
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, TypeError, ValueError) as exc:
        token_stats_record_error(coverage, path, exc)
        return []
    if not isinstance(payload, list):
        coverage["nonArrayFiles"] += 1
        coverage["badLines"] += 1
        return []
    coverage["filesRead"] += 1
    coverage["objects"] += sum(isinstance(item, dict) for item in payload)
    coverage["nonObjectLines"] += sum(not isinstance(item, dict) for item in payload)
    selected_rows: dict[str, dict] = {}
    for item_index, item in enumerate(payload):
        if not isinstance(item, dict):
            continue
        if item.get("say") != "api_req_started":
            continue
        coverage["apiRequestRows"] += 1
        raw_text = item.get("text")
        if not isinstance(raw_text, str):
            coverage["apiRequestRowsWithoutUsage"] += 1
            continue
        try:
            request_payload = json.loads(raw_text)
        except (TypeError, ValueError):
            coverage["apiRequestRowsWithInvalidPayload"] += 1
            coverage["apiRequestRowsWithoutUsage"] += 1
            continue
        usage_fields = ("tokensIn", "tokensOut", "cacheReads", "cacheWrites")
        if not isinstance(request_payload, dict) or not any(
            field in request_payload for field in usage_fields
        ):
            coverage["apiRequestRowsWithoutUsage"] += 1
            continue
        request_usage = token_stats_cline_request_usage(request_payload)
        if request_usage is None:
            coverage["apiRequestRowsWithZeroUsage"] += 1
            continue
        coverage["usageCandidates"] += 1

        raw_timestamp = item.get("ts")
        parsed_timestamp = token_stats_deepseek_timestamp(raw_timestamp)
        local_timestamp = None
        local_date = None
        if parsed_timestamp is not None:
            local = parsed_timestamp.astimezone(TOKEN_STATS_TZ)
            local_timestamp = local.isoformat()
            local_date = local.date().isoformat()
        else:
            coverage["invalidTimestamps"] += 1
            coverage["usageRowsWithoutValidDate"] += 1

        model_info = item.get("modelInfo") if isinstance(item.get("modelInfo"), dict) else {}
        model_provider = model_info.get("providerId") or model_info.get("provider_id")
        model = model_info.get("modelId") or model_info.get("model_id")
        if not model or not model_provider:
            request_epoch = parsed_timestamp.timestamp() if parsed_timestamp is not None else None
            previous = [
                event
                for event in model_events
                if request_epoch is not None and event["timestamp"] <= request_epoch
            ]
            if previous:
                event = previous[-1]
                model_provider = model_provider or event["provider"]
                model = model or event["model"]
                coverage["modelsFromMetadata"] += 1
        model_provider = (
            model_provider.strip()
            if isinstance(model_provider, str) and model_provider.strip()
            else "unknown"
        )
        model = model.strip() if isinstance(model, str) and model.strip() else "unknown"
        if not token_stats_cline_is_deepseek(model_provider, model):
            coverage["nonDeepSeekUsageRows"] += 1
            continue

        history_index = item.get("conversationHistoryIndex")
        if history_index is None:
            coverage["usageRowsWithoutMessageId"] += 1
            history_index = item_index
        dedupe_key = f"cline:{provider_label}:{task_id}:{raw_timestamp}:{history_index}"
        cost_value = request_payload.get("cost")
        price_known = (
            isinstance(cost_value, (int, float))
            and not isinstance(cost_value, bool)
            and math.isfinite(float(cost_value))
            and float(cost_value) >= 0
        )
        cost_usd = float(cost_value) if price_known else 0
        row = {
            "id": token_stats_request_id("deepseek", dedupe_key),
            "timestamp": local_timestamp,
            "timestampUtc": (
                parsed_timestamp.isoformat().replace("+00:00", "Z")
                if parsed_timestamp is not None
                else None
            ),
            "date": local_date,
            "provider": provider_label,
            "modelProvider": model_provider,
            "model": model,
            "tier": "unknown",
            "requestedTier": "unknown",
            "tierSource": "unknown",
            "source": "deepseek",
            "threadSource": (
                "cline-chinese" if provider_label == "Cline Chinese" else "cline"
            ),
            "sessionId": f"cline:{provider_label}:{task_id}",
            "project": provider_label,
            **request_usage,
            "costUsd": cost_usd,
            "cost": cost_usd if price_known else None,
            "priceKnown": price_known,
        }
        selected = selected_rows.get(dedupe_key)
        if selected is None or row["totalTokens"] > selected["totalTokens"]:
            selected_rows[dedupe_key] = row
    return [{"key": key, "row": row} for key, row in selected_rows.items()]


def token_stats_deepseek_cross_source_fingerprint(row: dict) -> str | None:
    timestamp = row.get("timestampUtc")
    if not timestamp:
        return None
    values = (
        timestamp,
        str(row.get("model") or "unknown").casefold(),
        row.get("inputTokens", 0),
        row.get("outputTokens", 0),
        row.get("cachedInputTokens", 0),
        row.get("cacheCreationInputTokens", 0),
        row.get("totalTokens", 0),
    )
    return token_stats_request_id("deepseek-cross-source", *values)


def token_stats_deepseek_daily_record() -> dict:
    return {
        "sessions": set(),
        "usageRows": 0,
        "inputTokens": 0,
        "outputTokens": 0,
        "cacheReadTokens": 0,
        "cacheReadKnownRequestCount": 0,
        "cacheReadUnknownRequestCount": 0,
        "cacheCreationTokens": 0,
        "cacheCreationKnownRequestCount": 0,
        "cacheCreationUnknownRequestCount": 0,
        "reasoningTokens": 0,
        "tokens": 0,
    }


def token_stats_deepseek_days(
    daily: dict, coverage: dict
) -> tuple[list[dict], list[dict], dict]:
    today = datetime.now(TOKEN_STATS_TZ).date()
    calendar_start, calendar_end = token_stats_calendar_bounds(daily, today)
    coverage["calendarStart"] = calendar_start.isoformat()
    coverage["calendarEnd"] = calendar_end.isoformat()
    coverage["activityStart"] = min(daily).isoformat() if daily else None
    coverage["activityEnd"] = max(daily).isoformat() if daily else None
    days = []
    cursor = calendar_start
    while cursor <= calendar_end:
        record = daily.get(cursor) or token_stats_deepseek_daily_record()
        days.append(
            {
                "date": cursor.isoformat(),
                "tokens": record["tokens"],
                "sessions": len(record["sessions"]),
                "usageRows": record["usageRows"],
                "assistantMessages": record["usageRows"],
                "inputTokens": record["inputTokens"],
                "outputTokens": record["outputTokens"],
                "cacheReadTokens": record["cacheReadTokens"],
                "cacheReadKnownRequestCount": record["cacheReadKnownRequestCount"],
                "cacheReadUnknownRequestCount": record["cacheReadUnknownRequestCount"],
                "cacheReadCoverage": token_stats_cache_coverage(
                    record["cacheReadKnownRequestCount"], record["usageRows"]
                ),
                "cacheCreationTokens": record["cacheCreationTokens"],
                "cacheCreationKnownRequestCount": record[
                    "cacheCreationKnownRequestCount"
                ],
                "cacheCreationUnknownRequestCount": record[
                    "cacheCreationUnknownRequestCount"
                ],
                "cacheCreationCoverage": (
                    "full"
                    if record["usageRows"] > 0
                    and record["cacheCreationKnownRequestCount"]
                    == record["usageRows"]
                    else "partial"
                    if record["cacheCreationKnownRequestCount"] > 0
                    else "none"
                ),
                "reasoningTokens": record["reasoningTokens"],
            }
        )
        cursor += timedelta(days=1)
    active_days = {day for day, record in daily.items() if record["tokens"] > 0}
    top_days = sorted(
        (item for item in days if item["tokens"] > 0),
        key=lambda item: (item["tokens"], item["date"]),
        reverse=True,
    )[:12]
    peak = top_days[0] if top_days else None
    metrics = {
        "tokenDays": len(active_days),
        "peakTokens": peak["tokens"] if peak else 0,
        "peakDate": peak["date"] if peak else None,
        **token_stats_streak_fields(active_days, today),
    }
    metrics.update(token_stats_daily_cache_read_coverage(days))
    return days, top_days, metrics


def token_stats_build_deepseek() -> dict:
    sessions_root = TOKEN_STATS_DEEPSEEK_HOME / "sessions"
    files = token_stats_deepseek_files(sessions_root)
    coverage = {
        **token_stats_details_source_coverage("deepseek"),
        "zstdFiles": len(files),
        "dependencyAvailable": zstandard is not None,
        "dependencyError": None,
        "duplicateUsageRows": 0,
        "requestRows": 0,
        "countingRule": (
            "only assistant/message data.usage; assistant/chunk usage is excluded; "
            "reasoningTokens is an output detail and is not added again"
        ),
    }
    coverage["jsonlFiles"] = len(files)
    coverage["sourceFormats"] = dict(Counter(path.name for path in files))
    coverage["sessionsRoot"] = str(sessions_root)
    selected_rows: dict[str, dict] = {}
    if zstandard is None:
        error = (
            "Python package 'zstandard' is unavailable; "
            "DeepSeek token logs were not read."
        )
        coverage["dependencyError"] = error
        coverage["filesFailed"] = len(files)
        coverage["fileErrors"] = (
            [{"file": str(sessions_root), "error": error}] if files else []
        )
    else:
        for path in files:
            failed_before = coverage["filesFailed"]
            candidates = token_stats_deepseek_file_candidates(path, coverage)
            if coverage["filesFailed"] > failed_before:
                continue
            for candidate in candidates:
                key = candidate["key"]
                row = candidate["row"]
                selected = selected_rows.get(key)
                candidate_key = token_stats_deepseek_candidate_key(row)
                selected_key = (
                    token_stats_deepseek_candidate_key(selected)
                    if selected
                    else None
                )
                if selected is None or candidate_key > selected_key:
                    selected_rows[key] = row

    rows = list(selected_rows.values())
    coverage["duplicateUsageRows"] = max(
        0, coverage["usageCandidates"] - len(rows)
    )
    coverage["requestRows"] = len(rows)
    daily: dict[date, dict] = defaultdict(token_stats_deepseek_daily_record)
    sessions = set()
    for row in rows:
        if row.get("sessionId"):
            sessions.add(row["sessionId"])
        if not row.get("date"):
            continue
        try:
            day = date.fromisoformat(row["date"])
        except (TypeError, ValueError):
            continue
        record = daily[day]
        record["usageRows"] += 1
        record["tokens"] += row["totalTokens"]
        record["inputTokens"] += row["inputTokens"]
        record["outputTokens"] += row["outputTokens"]
        record["cacheReadTokens"] += row["cachedInputTokens"]
        if token_stats_cache_read_known(row):
            record["cacheReadKnownRequestCount"] += 1
        else:
            record["cacheReadUnknownRequestCount"] += 1
        record["cacheCreationTokens"] += row["cacheCreationInputTokens"]
        if bool(row.get("cacheCreationKnown", row.get("cacheWriteKnown", False))):
            record["cacheCreationKnownRequestCount"] += 1
        else:
            record["cacheCreationUnknownRequestCount"] += 1
        record["reasoningTokens"] += row["reasoningTokens"]
        if row.get("sessionId"):
            record["sessions"].add(row["sessionId"])

    coverage["sessions"] = len(sessions)
    coverage["dailyTokenTotal"] = sum(
        record["tokens"] for record in daily.values()
    )
    days, top_days, metrics = token_stats_deepseek_days(daily, coverage)
    total = sum(row["totalTokens"] for row in rows)
    return {
        "canonicalTotal": total,
        "totalWithCacheRead": total,
        "totalWithoutCacheRead": sum(
            row["inputTokens"] + row["outputTokens"] for row in rows
        ),
        "totalSessions": len(sessions),
        "totalProviders": len({row["provider"] for row in rows}),
        "totalModels": len({row["model"] for row in rows}),
        **metrics,
        "days": days,
        "topDays": top_days,
        "coverage": coverage,
    }


def token_stats_antigravity_daily_record() -> dict:
    return {
        "date": None,
        "tokens": 0,
        "inputTokens": 0,
        "outputTokens": 0,
        "cachedInputTokens": 0,
        "cacheCreationTokens": 0,
        "cacheReadKnownRequestCount": 0,
        "cacheReadUnknownRequestCount": 0,
        "cacheCreationKnownRequestCount": 0,
        "cacheCreationUnknownRequestCount": 0,
        "reasoningTokens": 0,
        "usageRows": 0,
        "sessions": set(),
    }


def token_stats_antigravity_days(
    daily: dict[date, dict], coverage: dict
) -> tuple[list[dict], list[dict], dict]:
    today = datetime.now(TOKEN_STATS_TZ).date()
    start = min(daily.keys()) if daily else today
    day_count = (today - start).days + 1
    days = []
    top_days = []
    active_days = set()
    for offset in range(day_count):
        cursor = start + timedelta(days=offset)
        record = daily.get(cursor) or token_stats_antigravity_daily_record()
        tokens = record["tokens"]
        requests = record["usageRows"]
        if tokens > 0 or requests > 0:
            active_days.add(cursor)
        day_entry = {
            "date": cursor.isoformat(),
            "day": cursor.isoformat(),
            "tokens": tokens,
            "requests": requests,
            "usageRows": requests,
            "inputTokens": record["inputTokens"],
            "outputTokens": record["outputTokens"],
            "cacheReadTokens": record["cachedInputTokens"],
            "cacheReadKnownRequestCount": record["cacheReadKnownRequestCount"],
            "cacheReadUnknownRequestCount": record["cacheReadUnknownRequestCount"],
            "cacheReadCoverage": token_stats_cache_coverage(
                record["cacheReadKnownRequestCount"], requests
            ),
            "cacheCreationTokens": record["cacheCreationTokens"],
            "cacheCreationKnownRequestCount": record["cacheCreationKnownRequestCount"],
            "cacheCreationUnknownRequestCount": record["cacheCreationUnknownRequestCount"],
            "cacheCreationCoverage": token_stats_cache_coverage(
                record["cacheCreationKnownRequestCount"], requests
            ),
            "reasoningTokens": record["reasoningTokens"],
            "sessions": len(record["sessions"]),
        }
        days.append(day_entry)
        if tokens > 0:
            top_days.append(day_entry)
    top_days.sort(key=lambda item: (-item["tokens"], item["date"]))
    peak = top_days[0] if top_days else None
    metrics = {
        "tokenDays": len(active_days),
        "peakTokens": peak["tokens"] if peak else 0,
        "peakDate": peak["date"] if peak else None,
        **token_stats_streak_fields(active_days, today),
    }
    metrics.update(token_stats_daily_cache_read_coverage(days))
    return days, top_days, metrics


def token_stats_build_antigravity(descriptors: list[dict] | None = None) -> dict:
    if descriptors is None:
        descriptors = token_stats_details_manifest()["files"]["antigravity"]
    summaries = token_stats_antigravity_summaries()
    coverage = {
        **token_stats_details_source_coverage("antigravity"),
        "sqliteDbFiles": len(descriptors),
    }
    rows = []
    for descriptor in descriptors:
        rows.extend(
            token_stats_antigravity_db_candidates(
                Path(descriptor["path"]), summaries, coverage
            )
        )

    daily: dict[date, dict] = defaultdict(token_stats_antigravity_daily_record)
    sessions = set()
    for row in rows:
        if row.get("sessionId"):
            sessions.add(row["sessionId"])
        try:
            day = date.fromisoformat(row["date"]) if row.get("date") else None
        except (TypeError, ValueError):
            day = None
        if day is None:
            continue
        record = daily[day]
        record["usageRows"] += 1
        record["tokens"] += row["totalTokens"]
        record["inputTokens"] += row["inputTokens"]
        record["outputTokens"] += row["outputTokens"]
        record["cachedInputTokens"] += row["cachedInputTokens"]
        if token_stats_cache_read_known(row):
            record["cacheReadKnownRequestCount"] += 1
        else:
            record["cacheReadUnknownRequestCount"] += 1
        record["reasoningTokens"] += row["reasoningTokens"]
        if bool(row.get("cacheCreationKnown")):
            record["cacheCreationKnownRequestCount"] += 1
        else:
            record["cacheCreationUnknownRequestCount"] += 1
        if row.get("sessionId"):
            record["sessions"].add(row["sessionId"])

    coverage["sessions"] = len(sessions)
    coverage["dailyTokenTotal"] = sum(record["tokens"] for record in daily.values())
    coverage["requestRows"] = len(rows)
    days, top_days, metrics = token_stats_antigravity_days(daily, coverage)
    total = sum(row["totalTokens"] for row in rows)
    return {
        "canonicalTotal": total,
        "totalWithCacheRead": total,
        "totalWithoutCacheRead": sum(
            row["inputTokens"] + row["outputTokens"] for row in rows
        ),
        "totalSessions": len(sessions),
        "totalProviders": len({row["provider"] for row in rows}),
        "totalModels": len({row["model"] for row in rows}),
        **metrics,
        "days": days,
        "topDays": top_days,
        "providers": token_stats_group_requests(rows, "provider"),
        "models": token_stats_group_models(rows),
        "coverage": coverage,
    }


def token_stats_build_uncached() -> dict:
    manifest = token_stats_details_manifest()
    codex = token_stats_build_codex(manifest["files"]["codex"])
    claude = token_stats_build_claude()
    deepseek = token_stats_build_deepseek_combined(manifest)
    workbuddy = token_stats_build_workbuddy(manifest["files"]["workbuddy"])
    antigravity = token_stats_build_antigravity(manifest["files"]["antigravity"])
    return {
        "ok": True,
        "generatedAt": datetime.now(TOKEN_STATS_TZ).isoformat(timespec="seconds"),
        "timezone": TOKEN_STATS_TIMEZONE,
        "codex": codex,
        "claude": claude,
        "deepseek": deepseek,
        "workbuddy": workbuddy,
        "antigravity": antigravity,
    }


def build_token_stats(force: bool = False) -> dict:
    global TOKEN_STATS_CACHE
    global TOKEN_STATS_CACHE_BUILT_AT

    now = time.monotonic()
    if (
        not force
        and TOKEN_STATS_CACHE is not None
        and now - TOKEN_STATS_CACHE_BUILT_AT < TOKEN_STATS_CACHE_TTL
    ):
        return TOKEN_STATS_CACHE

    with TOKEN_STATS_BUILD_LOCK:
        now = time.monotonic()
        if (
            not force
            and TOKEN_STATS_CACHE is not None
            and now - TOKEN_STATS_CACHE_BUILT_AT < TOKEN_STATS_CACHE_TTL
        ):
            return TOKEN_STATS_CACHE

        built = token_stats_build_uncached()
        TOKEN_STATS_CACHE = built
        TOKEN_STATS_CACHE_BUILT_AT = time.monotonic()
        return built


def token_stats_details_coverage() -> dict:
    return {
        "jsonlFiles": 0,
        "filesRead": 0,
        "filesFailed": 0,
        "fileErrors": [],
        "lines": 0,
        "objects": 0,
        "invalidJsonLines": 0,
        "nonObjectLines": 0,
        "badLines": 0,
        "invalidTimestamps": 0,
    }


def token_stats_details_source_coverage(source: str) -> dict:
    coverage = token_stats_details_coverage()
    if source == "codex":
        coverage.update(
            {
                "tokenEvents": 0,
                "counterResets": 0,
                "rowsFromLastTokenUsage": 0,
                "rowsFromCumulativeDelta": 0,
                "tokenEventsWithoutCumulativeTotal": 0,
                "duplicateTokenEventsSkipped": 0,
                "lastUsageTotalMismatches": 0,
                "breakdownTotalMismatches": 0,
                "tokenEventsWithoutUsableRequest": 0,
            }
        )
    else:
        coverage.update(
            {
                "usageCandidates": 0,
                "usageRowsWithoutValidDate": 0,
                "usageRowsWithoutMessageId": 0,
            }
        )
    if source == "antigravity":
        coverage.update(
            {
                "sqliteDbFiles": 0,
                "stepRows": 0,
                "responseSteps": 0,
                "modelsFromMetadata": 0,
            }
        )
    if source == "deepseek":
        coverage.update(
            {
                "dependencyAvailable": zstandard is not None,
                "dependencyError": None,
                "apiRequestRows": 0,
                "apiRequestRowsWithoutUsage": 0,
                "apiRequestRowsWithZeroUsage": 0,
                "apiRequestRowsWithInvalidPayload": 0,
                "nonDeepSeekUsageRows": 0,
                "metadataFilesRead": 0,
                "metadataFilesMissing": 0,
                "metadataFilesFailed": 0,
                "invalidModelTimestamps": 0,
                "modelEvents": 0,
                "modelsFromMetadata": 0,
                "nonArrayFiles": 0,
            }
        )
    if source == "workbuddy":
        coverage.update(
            {
                "usageCandidates": 0,
                "usageRowsWithoutValidDate": 0,
                "usageRowsWithoutStableId": 0,
                "duplicateUsageRowsSkipped": 0,
                "filesParsed": 0,
                "fileCacheHits": 0,
                "filesAppended": 0,
            }
        )
    return coverage


def token_stats_merge_file_coverage(target: dict, source: dict) -> None:
    for key, value in source.items():
        if key == "jsonlFiles":
            continue
        if key == "fileErrors":
            room = max(0, 10 - len(target.setdefault(key, [])))
            if room:
                target[key].extend(list(value or [])[:room])
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            target[key] = target.get(key, 0) + value


def token_stats_details_manifest() -> dict:
    codex_tier_fallback = token_stats_codex_config_tier()
    codex_context_key = hashlib.sha256(
        json.dumps(codex_tier_fallback, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    codex_files = []
    for root in (CODEX_HOME / "sessions", CODEX_HOME / "archived_sessions"):
        codex_files.extend(chat_history_jsonl_files(root))
    codex_files = sorted(set(codex_files), key=lambda item: os.path.normcase(str(item)))
    claude_root = CHAT_HISTORY_CLAUDE_HOME / "projects"
    claude_files = chat_history_jsonl_files(claude_root)
    claude_legacy_files = token_stats_claude_legacy_files(
        CHAT_HISTORY_CLAUDE_HOME, claude_root
    )
    deepseek_root = TOKEN_STATS_DEEPSEEK_HOME / "sessions"
    deepseek_files = token_stats_deepseek_files(deepseek_root)
    workbuddy_root = TOKEN_STATS_WORKBUDDY_HOME / "projects"
    workbuddy_files = token_stats_workbuddy_files(workbuddy_root)
    antigravity_root = TOKEN_STATS_ANTIGRAVITY_HOME / "conversations"
    antigravity_files = token_stats_antigravity_files(antigravity_root)
    cline_sources = (
        ("Cline Chinese", TOKEN_STATS_CLINE_CHINESE_HOME),
        ("Cline", TOKEN_STATS_CLINE_HOME),
    )
    cline_files = [
        (provider, path)
        for provider, root in cline_sources
        for path in token_stats_cline_task_files(root)
    ]
    files = {
        "codex": [],
        "claude": [],
        "claudeLegacy": [],
        "deepseek": [],
        "deepseekCline": [],
        "workbuddy": [],
        "antigravity": [],
    }
    digest = hashlib.sha256(TOKEN_STATS_DETAILS_FILE_CACHE_VERSION.encode("ascii"))
    for source, paths in (
        ("codex", codex_files),
        ("claude", claude_files),
        ("claudeLegacy", claude_legacy_files),
        ("deepseek", deepseek_files),
        ("workbuddy", workbuddy_files),
        ("antigravity", antigravity_files),
    ):
        for path in paths:
            try:
                stat = path.stat()
            except OSError:
                continue
            try:
                resolved = path.resolve(strict=False)
            except OSError:
                resolved = path.absolute()
            path_text = str(resolved)
            descriptor = {
                "path": path_text,
                "pathKey": os.path.normcase(path_text),
                "size": int(stat.st_size),
                "mtimeNs": int(stat.st_mtime_ns),
            }
            if source == "codex":
                descriptor["contextKey"] = codex_context_key
                descriptor["tierFallback"] = codex_tier_fallback
            elif source == "antigravity":
                wal_path = Path(f"{path}-wal")
                try:
                    wal_stat = wal_path.stat()
                    descriptor["contextKey"] = (
                        f"wal:present:{int(wal_stat.st_size)}:{int(wal_stat.st_mtime_ns)}"
                    )
                except OSError:
                    descriptor["contextKey"] = "wal:missing"
            files[source].append(descriptor)
            digest.update(source.encode("ascii"))
            digest.update(b"\0")
            digest.update(descriptor["pathKey"].encode("utf-8", errors="replace"))
            digest.update(b"\0")
            digest.update(str(descriptor["size"]).encode("ascii"))
            digest.update(b":")
            digest.update(str(descriptor["mtimeNs"]).encode("ascii"))
            digest.update(b"\n")
            digest.update(b":")
            digest.update(str(descriptor.get("contextKey") or "").encode("ascii"))
    for provider, path in cline_files:
        try:
            stat = path.stat()
        except OSError:
            continue
        metadata_path = path.parent / "task_metadata.json"
        try:
            metadata_stat = metadata_path.stat()
            metadata_signature = f"{metadata_stat.st_size}:{metadata_stat.st_mtime_ns}"
        except OSError:
            metadata_signature = "missing"
        try:
            resolved = path.resolve(strict=False)
            resolved_metadata = metadata_path.resolve(strict=False)
        except OSError:
            resolved = path.absolute()
            resolved_metadata = metadata_path.absolute()
        path_text = str(resolved)
        descriptor = {
            "path": path_text,
            "pathKey": os.path.normcase(path_text),
            "size": int(stat.st_size),
            "mtimeNs": int(stat.st_mtime_ns),
            "contextKey": metadata_signature,
            "metadataPath": str(resolved_metadata),
            "provider": provider,
        }
        files["deepseekCline"].append(descriptor)
        digest.update(b"deepseek-cline\0")
        digest.update(descriptor["pathKey"].encode("utf-8", errors="replace"))
        digest.update(b"\0")
        digest.update(str(descriptor["size"]).encode("ascii"))
        digest.update(b":")
        digest.update(str(descriptor["mtimeNs"]).encode("ascii"))
        digest.update(b":")
        digest.update(metadata_signature.encode("ascii"))
        digest.update(b":")
        digest.update(provider.encode("utf-8"))
    return {
        "signature": digest.hexdigest(),
        "files": files,
        "claudeRoot": claude_root,
        "deepseekRoot": deepseek_root,
        "workbuddyRoot": workbuddy_root,
        "clineRoots": {provider: root for provider, root in cline_sources},
        "codexTierFallback": codex_tier_fallback,
    }


def token_stats_details_cache_connection() -> sqlite3.Connection | None:
    connection = None
    try:
        TOKEN_STATS_DETAILS_FILE_CACHE_DB.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(TOKEN_STATS_DETAILS_FILE_CACHE_DB, timeout=10)
        connection.execute("pragma journal_mode = wal")
        connection.execute("pragma synchronous = normal")
        connection.execute(
            """
            create table if not exists file_cache (
                version text not null,
                source text not null,
                path_key text not null,
                size integer not null,
                mtime_ns integer not null,
                payload blob not null,
                primary key (version, source, path_key)
            )
            """
        )
        return connection
    except (OSError, sqlite3.Error):
        if connection is not None:
            try:
                connection.close()
            except sqlite3.Error:
                pass
        return None


def token_stats_details_payload_cache_key(source: str, path_key: str) -> tuple[str, str, str, str]:
    db_key = os.path.normcase(os.path.abspath(os.fspath(TOKEN_STATS_DETAILS_FILE_CACHE_DB)))
    return (db_key, TOKEN_STATS_DETAILS_FILE_CACHE_VERSION, source, path_key)


def token_stats_details_cache_load(
    connection: sqlite3.Connection | None, source: str, path_key: str
) -> dict | None:
    if connection is None:
        return None
    cache_key = token_stats_details_payload_cache_key(source, path_key)
    with TOKEN_STATS_DETAILS_PAYLOAD_CACHE_LOCK:
        cached = TOKEN_STATS_DETAILS_PAYLOAD_CACHE.get(cache_key)
    if cached is not None:
        return cached
    try:
        row = connection.execute(
            "select payload from file_cache where version = ? and source = ? and path_key = ?",
            (TOKEN_STATS_DETAILS_FILE_CACHE_VERSION, source, path_key),
        ).fetchone()
        if not row:
            return None
        payload = json.loads(zlib.decompress(row[0]).decode("utf-8"))
        if not isinstance(payload, dict):
            return None
        with TOKEN_STATS_DETAILS_PAYLOAD_CACHE_LOCK:
            return TOKEN_STATS_DETAILS_PAYLOAD_CACHE.setdefault(cache_key, payload)
    except (OSError, sqlite3.Error, TypeError, ValueError, UnicodeError, zlib.error):
        return None


def token_stats_details_cache_save(
    connection: sqlite3.Connection | None,
    source: str,
    descriptor: dict,
    payload: dict,
) -> None:
    if connection is None:
        return
    try:
        serialized = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        packed = zlib.compress(serialized, 3)
        connection.execute(
            """
            insert into file_cache(version, source, path_key, size, mtime_ns, payload)
            values (?, ?, ?, ?, ?, ?)
            on conflict(version, source, path_key) do update set
                size = excluded.size,
                mtime_ns = excluded.mtime_ns,
                payload = excluded.payload
            """,
            (
                TOKEN_STATS_DETAILS_FILE_CACHE_VERSION,
                source,
                descriptor["pathKey"],
                descriptor["size"],
                descriptor["mtimeNs"],
                packed,
            ),
        )
        cache_key = token_stats_details_payload_cache_key(
            source, descriptor["pathKey"]
        )
        with TOKEN_STATS_DETAILS_PAYLOAD_CACHE_LOCK:
            TOKEN_STATS_DETAILS_PAYLOAD_CACHE[cache_key] = payload
    except Exception:
        return


def token_stats_details_cache_cleanup(
    connection: sqlite3.Connection | None, source: str, live_path_keys: set[str]
) -> None:
    if connection is None:
        return
    try:
        rows = connection.execute(
            "select path_key from file_cache where version = ? and source = ?",
            (TOKEN_STATS_DETAILS_FILE_CACHE_VERSION, source),
        ).fetchall()
        stale = [
            (TOKEN_STATS_DETAILS_FILE_CACHE_VERSION, source, row[0])
            for row in rows
            if row[0] not in live_path_keys
        ]
        if stale:
            connection.executemany(
                "delete from file_cache where version = ? and source = ? and path_key = ?",
                stale,
            )
        db_key = os.path.normcase(
            os.path.abspath(os.fspath(TOKEN_STATS_DETAILS_FILE_CACHE_DB))
        )
        with TOKEN_STATS_DETAILS_PAYLOAD_CACHE_LOCK:
            stale_memory_keys = [
                key
                for key in TOKEN_STATS_DETAILS_PAYLOAD_CACHE
                if key[0] == db_key
                and key[1] == TOKEN_STATS_DETAILS_FILE_CACHE_VERSION
                and key[2] == source
                and key[3] not in live_path_keys
            ]
            for key in stale_memory_keys:
                TOKEN_STATS_DETAILS_PAYLOAD_CACHE.pop(key, None)
    except Exception:
        return


def token_stats_file_anchor(path: Path, size: int) -> dict:
    length = min(max(0, int(size)), 4096)
    offset = max(0, int(size) - length)
    try:
        with path.open("rb") as source:
            source.seek(offset)
            content = source.read(length)
    except OSError:
        content = b""
    return {
        "offset": offset,
        "length": length,
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def token_stats_cache_is_exact(payload: dict | None, descriptor: dict) -> bool:
    signature = payload.get("signature") if isinstance(payload, dict) else None
    return bool(
        isinstance(signature, dict)
        and signature.get("size") == descriptor["size"]
        and signature.get("mtimeNs") == descriptor["mtimeNs"]
        and str(signature.get("contextKey") or "")
        == str(descriptor.get("contextKey") or "")
    )


def token_stats_cache_is_append(payload: dict | None, path: Path, descriptor: dict) -> bool:
    if not isinstance(payload, dict):
        return False
    signature = payload.get("signature")
    anchor = payload.get("anchor")
    if not isinstance(signature, dict) or not isinstance(anchor, dict):
        return False
    if str(signature.get("contextKey") or "") != str(descriptor.get("contextKey") or ""):
        return False
    previous_size = token_stats_nonnegative_int(signature.get("size"))
    if previous_size is None or descriptor["size"] <= previous_size:
        return False
    try:
        with path.open("rb") as source:
            source.seek(int(anchor.get("offset") or 0))
            content = source.read(int(anchor.get("length") or 0))
    except OSError:
        return False
    return hashlib.sha256(content).hexdigest() == anchor.get("sha256")


def token_stats_incremental_jsonl_objects(
    path: Path,
    coverage: dict,
    start_offset: int,
    end_offset: int,
    progress: dict,
    count_file_read: bool,
):
    try:
        with path.open("rb") as source:
            source.seek(max(0, start_offset))
            progress["offset"] = source.tell()
            while source.tell() < end_offset:
                line_start = source.tell()
                raw_line = source.readline(end_offset - line_start)
                if not raw_line:
                    break
                complete_tail = not raw_line.endswith(b"\n") and source.tell() >= end_offset
                try:
                    item = json.loads(raw_line.decode("utf-8", errors="replace"))
                except (TypeError, ValueError):
                    if complete_tail:
                        progress["offset"] = line_start
                        break
                    progress["offset"] = source.tell()
                    coverage["lines"] += 1
                    coverage["invalidJsonLines"] += 1
                    coverage["badLines"] += 1
                    continue
                progress["offset"] = source.tell()
                coverage["lines"] += 1
                if not isinstance(item, dict):
                    coverage["nonObjectLines"] += 1
                    coverage["badLines"] += 1
                    continue
                coverage["objects"] += 1
                yield item
        if count_file_read:
            coverage["filesRead"] += 1
    except OSError as exc:
        token_stats_record_error(coverage, path, exc)


def token_stats_codex_request_usage(usage: dict) -> dict | None:
    if not isinstance(usage, dict):
        return None
    raw_input = token_stats_nonnegative_int(usage.get("input_tokens")) or 0
    cached_input, cache_read_known = token_stats_known_usage_value(usage, "cached_input_tokens")
    cache_creation, _cache_creation_placeholder_present = token_stats_known_usage_value(
        usage,
        "cache_write_input_tokens",
        "cache_creation_input_tokens",
    )
    # Codex rollout usage currently emits placeholder cache-write keys but does not
    # report cache writes separately. A placeholder zero is therefore not a known zero.
    cache_creation_known = False
    output = token_stats_nonnegative_int(usage.get("output_tokens")) or 0
    reasoning = token_stats_nonnegative_int(usage.get("reasoning_output_tokens")) or 0
    explicit_total = token_stats_nonnegative_int(usage.get("total_tokens"))
    total = explicit_total if explicit_total is not None else raw_input + output
    if not any((raw_input, cached_input, cache_creation, output, reasoning, total)):
        return None
    return {
        "inputTokens": max(0, raw_input - cached_input),
        "rawInputTokens": raw_input,
        "outputTokens": output,
        "cachedInputTokens": cached_input,
        "cacheReadKnown": cache_read_known,
        "cacheCreationInputTokens": cache_creation,
        "cacheWriteTokens": cache_creation,
        "cacheCreationKnown": cache_creation_known,
        "cacheWriteKnown": cache_creation_known,
        "cacheTokens": cached_input + cache_creation,
        "reasoningTokens": reasoning,
        "totalTokens": total,
    }


def token_stats_claude_request_usage(usage: dict) -> dict | None:
    if not isinstance(usage, dict):
        return None
    input_tokens = token_stats_nonnegative_int(usage.get("input_tokens")) or 0
    output_tokens = token_stats_nonnegative_int(usage.get("output_tokens")) or 0
    cache_creation, cache_creation_known = token_stats_known_usage_value(
        usage, "cache_creation_input_tokens", "cache_write_input_tokens"
    )
    cache_read, cache_read_known = token_stats_known_usage_value(usage, "cache_read_input_tokens")
    reasoning = (
        token_stats_nonnegative_int(usage.get("reasoning_output_tokens"))
        or token_stats_nonnegative_int(usage.get("reasoning_tokens"))
        or 0
    )
    total = input_tokens + output_tokens + cache_creation + cache_read
    if not any((input_tokens, output_tokens, cache_creation, cache_read, reasoning)):
        return None
    return {
        "inputTokens": input_tokens,
        "rawInputTokens": input_tokens + cache_creation + cache_read,
        "outputTokens": output_tokens,
        "cachedInputTokens": cache_read,
        "cacheReadKnown": cache_read_known,
        "cacheCreationInputTokens": cache_creation,
        "cacheWriteTokens": cache_creation,
        "cacheCreationKnown": cache_creation_known,
        "cacheWriteKnown": cache_creation_known,
        "cacheTokens": cache_read + cache_creation,
        "reasoningTokens": reasoning,
        "totalTokens": total,
    }


def token_stats_usage_delta(current: dict, previous: dict | None) -> dict:
    result = {}
    for field in (
        "input_tokens",
        "cached_input_tokens",
        "cache_write_input_tokens",
        "cache_creation_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
        "total_tokens",
    ):
        if field in {"cached_input_tokens", "cache_write_input_tokens", "cache_creation_input_tokens"} and field not in current:
            continue
        current_value = token_stats_nonnegative_int(current.get(field)) or 0
        previous_value = (
            token_stats_nonnegative_int(previous.get(field)) or 0
            if isinstance(previous, dict)
            else 0
        )
        result[field] = (
            current_value - previous_value
            if current_value >= previous_value
            else current_value
        )
    return result


def token_stats_request_timestamp(value) -> tuple[datetime | None, str | None, str | None]:
    parsed = chat_history_parse_timestamp(value)
    if parsed is None:
        return None, None, None
    local = parsed.astimezone(TOKEN_STATS_TZ)
    return parsed, local.isoformat(), local.date().isoformat()


def token_stats_request_id(*values) -> str:
    raw = "\x1f".join(str(value or "") for value in values)
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:24]


def token_stats_workbuddy_request_usage(item: dict) -> dict | None:
    if not isinstance(item, dict):
        return None
    provider_data = item.get("providerData")
    provider_data = provider_data if isinstance(provider_data, dict) else {}
    message = item.get("message")
    message = message if isinstance(message, dict) else {}
    usage_sources = [
        provider_data.get("rawUsage"),
        provider_data.get("usage"),
        message.get("usage"),
    ]
    usage_sources = [usage for usage in usage_sources if isinstance(usage, dict)]
    if not usage_sources:
        return None

    def first_value(*fields: str) -> int | None:
        for usage in usage_sources:
            for field in fields:
                value = token_stats_nonnegative_int(usage.get(field))
                if value is not None:
                    return value
        return None

    raw_input = first_value("prompt_tokens", "input_tokens", "inputTokens")
    output = first_value("completion_tokens", "output_tokens", "outputTokens")
    explicit_total = first_value("total_tokens", "totalTokens")
    if raw_input is None and output is None and explicit_total is None:
        return None
    raw_input = raw_input or 0
    output = output or 0

    cached_input = first_value(
        "prompt_cache_hit_tokens",
        "cache_read_input_tokens",
        "cached_input_tokens",
        "cacheReadTokens",
        "cachedInputTokens",
    )
    if cached_input is None:
        for usage in usage_sources:
            details = usage.get("prompt_tokens_details")
            if isinstance(details, dict):
                cached_input = token_stats_nonnegative_int(details.get("cached_tokens"))
            if cached_input is not None:
                break
            details = usage.get("inputTokensDetails")
            if isinstance(details, list):
                cached_values = [
                    token_stats_nonnegative_int(detail.get("cached_tokens"))
                    for detail in details
                    if isinstance(detail, dict)
                ]
                cached_values = [value for value in cached_values if value is not None]
                if cached_values:
                    cached_input = sum(cached_values)
                    break
    cache_read_known = cached_input is not None
    cached_input = min(raw_input, cached_input or 0)

    reasoning = first_value("reasoning_output_tokens", "reasoning_tokens")
    if reasoning is None:
        for usage in usage_sources:
            details = usage.get("completion_tokens_details")
            if isinstance(details, dict):
                reasoning = token_stats_nonnegative_int(details.get("reasoning_tokens"))
            if reasoning is not None:
                break
            details = usage.get("outputTokensDetails")
            if isinstance(details, list):
                reasoning_values = [
                    token_stats_nonnegative_int(detail.get("reasoning_tokens"))
                    for detail in details
                    if isinstance(detail, dict)
                ]
                reasoning_values = [value for value in reasoning_values if value is not None]
                if reasoning_values:
                    reasoning = sum(reasoning_values)
                    break
    reasoning = reasoning or 0
    total = explicit_total if explicit_total is not None else raw_input + output
    if not any((raw_input, output, reasoning, total)):
        return None
    return {
        "inputTokens": max(0, raw_input - cached_input),
        "rawInputTokens": raw_input,
        "outputTokens": output,
        "cachedInputTokens": cached_input,
        "cacheReadKnown": cache_read_known,
        "cacheCreationInputTokens": 0,
        "cacheWriteTokens": 0,
        "cacheCreationKnown": False,
        "cacheWriteKnown": False,
        "cacheTokens": cached_input,
        "reasoningTokens": reasoning,
        "totalTokens": total,
    }


def token_stats_workbuddy_project_name(item: dict, path: Path) -> str:
    provider_data = item.get("providerData")
    provider_data = provider_data if isinstance(provider_data, dict) else {}
    cwd = item.get("cwd") or provider_data.get("cwd")
    if isinstance(cwd, str) and cwd.strip():
        normalized = cwd.strip().rstrip("\\/")
        project = re.split(r"[\\/]", normalized)[-1].strip()
        if project:
            return project
    parent = path.parent.name.strip()
    return parent or "WorkBuddy"


def token_stats_workbuddy_candidate(
    item: dict, path: Path, line_index: int, coverage: dict
) -> dict | None:
    usage = token_stats_workbuddy_request_usage(item)
    if usage is None:
        return None
    coverage["usageCandidates"] += 1
    parsed = token_stats_deepseek_timestamp(
        item.get("timestamp") or item.get("createdAt") or item.get("created_at")
    )
    if parsed is None:
        coverage["invalidTimestamps"] += 1
        coverage["usageRowsWithoutValidDate"] += 1
        return None
    provider_data = item.get("providerData")
    provider_data = provider_data if isinstance(provider_data, dict) else {}
    message_id = provider_data.get("messageId") or provider_data.get("message_id")
    record_id = item.get("id")
    stable_id = message_id or record_id
    if not stable_id:
        coverage["usageRowsWithoutStableId"] += 1
        stable_id = f"{os.path.normcase(str(path))}:{line_index}:{item.get('timestamp') or ''}"
    session_id = str(item.get("sessionId") or item.get("session_id") or path.stem)
    model = str(
        provider_data.get("requestModelName")
        or provider_data.get("requestModelId")
        or provider_data.get("model")
        or "unknown"
    )
    local = parsed.astimezone(TOKEN_STATS_TZ)
    return {
        "id": token_stats_request_id("workbuddy", session_id, stable_id),
        "source": "workbuddy",
        "provider": "WorkBuddy",
        "model": model,
        "modelProvider": str(provider_data.get("model") or "WorkBuddy"),
        "threadSource": "workbuddy",
        "sessionId": session_id,
        "sessionTitle": "",
        "project": token_stats_workbuddy_project_name(item, path),
        "timestamp": local.isoformat(),
        "timestampUtc": parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "date": local.date().isoformat(),
        "tier": "unknown",
        "tierSource": "unknown",
        **usage,
        "costUsd": 0.0,
        "priceKnown": False,
    }


def token_stats_parse_workbuddy_segment(
    path: Path,
    coverage: dict,
    start_offset: int,
    end_offset: int,
) -> tuple[list[dict], int]:
    rows = []
    progress = {"offset": max(0, start_offset)}
    objects = token_stats_incremental_jsonl_objects(
        path,
        coverage,
        max(0, start_offset),
        max(0, end_offset),
        progress,
        True,
    )
    for line_index, item in enumerate(objects, start=1):
        candidate = token_stats_workbuddy_candidate(item, path, line_index, coverage)
        if candidate is not None:
            rows.append(candidate)
    return rows, int(progress.get("offset") or 0)


def token_stats_detail_codex_file(path: Path, coverage: dict) -> dict | None:
    payload = token_stats_detail_codex_file_incremental(path, coverage, None, None)
    return payload.get("contribution") if payload is not None else None


def token_stats_detail_codex_file_incremental(
    path: Path,
    coverage: dict,
    descriptor: dict | None,
    cached: dict | None,
) -> dict | None:
    state = cached.get("parserState", {}) if isinstance(cached, dict) else {}
    contribution = cached.get("contribution", {}) if isinstance(cached, dict) else {}
    tier_fallback = (
        descriptor.get("tierFallback")
        if isinstance(descriptor, dict) and isinstance(descriptor.get("tierFallback"), dict)
        else token_stats_codex_config_tier()
    )
    session_id = str(state.get("sessionId") or "")
    model = str(state.get("model") or "unknown")
    model_provider = str(state.get("modelProvider") or "unknown")
    thread_source = str(state.get("threadSource") or "unknown")
    service_tier = str(state.get("serviceTier") or tier_fallback["tier"])
    requested_tier = str(
        state.get("requestedTier") or tier_fallback.get("requestedTier") or "unknown"
    )
    service_tier_source = str(
        state.get("serviceTierSource") or tier_fallback["source"]
    )
    previous_total_usage = state.get("previousTotalUsage")
    previous_total = token_stats_nonnegative_int(state.get("previousTotal"))
    lifetime_total = token_stats_nonnegative_int(contribution.get("lifetimeTotal")) or 0
    rows = copy.deepcopy(contribution.get("rows", []))
    event_index = token_stats_nonnegative_int(state.get("eventIndex")) or 0
    start_offset = token_stats_nonnegative_int(cached.get("offset")) if cached else 0
    start_offset = start_offset or 0
    if descriptor is None:
        try:
            stat = path.stat()
            descriptor = {
                "path": str(path),
                "pathKey": os.path.normcase(str(path)),
                "size": int(stat.st_size),
                "mtimeNs": int(stat.st_mtime_ns),
                "contextKey": hashlib.sha256(
                    json.dumps(tier_fallback, sort_keys=True, separators=(",", ":")).encode("utf-8")
                ).hexdigest(),
                "tierFallback": tier_fallback,
            }
        except OSError as exc:
            token_stats_record_error(coverage, path, exc)
            return None
    progress = {"offset": start_offset}
    failures_before = coverage["filesFailed"]

    for item in token_stats_incremental_jsonl_objects(
        path,
        coverage,
        start_offset,
        descriptor["size"],
        progress,
        count_file_read=not bool(cached),
    ):
        item_type = item.get("type")
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        if item_type == "session_meta":
            if not session_id:
                raw_id = payload.get("id") or payload.get("session_id")
                if raw_id:
                    session_id = str(raw_id)
            raw_provider = payload.get("model_provider")
            if (
                model_provider == "unknown"
                and isinstance(raw_provider, str)
                and raw_provider.strip()
            ):
                model_provider = raw_provider.strip()
            raw_thread_source = payload.get("thread_source")
            if (
                thread_source == "unknown"
                and isinstance(raw_thread_source, str)
                and raw_thread_source.strip()
            ):
                thread_source = raw_thread_source.strip()
            elif (
                thread_source == "unknown"
                and isinstance(payload.get("source"), dict)
                and "subagent" in payload["source"]
            ):
                thread_source = "subagent"
            thread_settings = (
                payload.get("thread_settings")
                if isinstance(payload.get("thread_settings"), dict)
                else {}
            )
            explicit_tier = token_stats_service_tier_from_log(
                thread_settings, "log.session_meta.thread_settings"
            )
            if explicit_tier is not None:
                service_tier = explicit_tier["tier"]
                requested_tier = explicit_tier["requestedTier"]
                service_tier_source = explicit_tier["source"]
        elif item_type == "turn_context":
            raw_model = payload.get("model")
            if isinstance(raw_model, str) and raw_model.strip():
                model = raw_model.strip()
            raw_provider = payload.get("model_provider")
            if isinstance(raw_provider, str) and raw_provider.strip():
                model_provider = raw_provider.strip()
            explicit_tier = token_stats_service_tier_from_log(
                payload, "log.turn_context"
            )
            if explicit_tier is not None:
                service_tier = explicit_tier["tier"]
                requested_tier = explicit_tier["requestedTier"]
                service_tier_source = explicit_tier["source"]
        elif item_type == "event_msg" and payload.get("type") == "thread_settings_applied":
            thread_settings = (
                payload.get("thread_settings")
                if isinstance(payload.get("thread_settings"), dict)
                else {}
            )
            explicit_tier = token_stats_service_tier_from_log(
                thread_settings, "log.thread_settings_applied"
            )
            if explicit_tier is not None:
                service_tier = explicit_tier["tier"]
                requested_tier = explicit_tier["requestedTier"]
                service_tier_source = explicit_tier["source"]

        if item_type != "event_msg" or payload.get("type") != "token_count":
            continue

        event_index += 1
        coverage["tokenEvents"] += 1
        parsed_timestamp, local_timestamp, local_date = token_stats_request_timestamp(
            item.get("timestamp")
        )
        if item.get("timestamp") and parsed_timestamp is None:
            coverage["invalidTimestamps"] += 1

        info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
        total_usage = (
            info.get("total_token_usage")
            if isinstance(info.get("total_token_usage"), dict)
            else {}
        )
        cumulative_delta_usage = (
            token_stats_usage_delta(total_usage, previous_total_usage)
            if total_usage
            else {}
        )
        current_total = token_stats_nonnegative_int(total_usage.get("total_tokens"))
        if current_total is None:
            coverage["tokenEventsWithoutCumulativeTotal"] += 1
            previous_total_usage = total_usage or previous_total_usage
            continue
        if previous_total is None:
            delta_total = current_total
        elif current_total >= previous_total:
            delta_total = current_total - previous_total
        else:
            coverage["counterResets"] += 1
            delta_total = current_total
        previous_total = current_total
        previous_total_usage = total_usage
        lifetime_total += delta_total
        if delta_total <= 0:
            coverage["duplicateTokenEventsSkipped"] += 1
            continue

        last_usage = (
            info.get("last_token_usage")
            if isinstance(info.get("last_token_usage"), dict)
            else None
        )
        request_usage = token_stats_codex_request_usage(last_usage or {})
        if request_usage is not None and request_usage["totalTokens"] == delta_total:
            coverage["rowsFromLastTokenUsage"] += 1
        else:
            if request_usage is not None:
                coverage["lastUsageTotalMismatches"] += 1
            request_usage = token_stats_codex_request_usage(cumulative_delta_usage)
            if request_usage is not None:
                coverage["rowsFromCumulativeDelta"] += 1
        if request_usage is None:
            coverage["tokenEventsWithoutUsableRequest"] += 1
            continue
        if request_usage["totalTokens"] != delta_total:
            coverage["breakdownTotalMismatches"] += 1
        request_usage["totalTokens"] = delta_total

        resolved_session = session_id or f"file:{chat_history_session_key('codex-detail', path)}"
        cost = estimate_model_cost(
            model,
            service_tier,
            {
                "inputTokens": request_usage["rawInputTokens"],
                "cachedInputTokens": request_usage["cachedInputTokens"],
                "outputTokens": request_usage["outputTokens"],
            },
        )
        rows.append(
            {
                "id": token_stats_request_id(
                    "codex", resolved_session, str(path), event_index, item.get("timestamp")
                ),
                "timestamp": local_timestamp,
                "timestampUtc": (
                    parsed_timestamp.isoformat().replace("+00:00", "Z")
                    if parsed_timestamp is not None
                    else None
                ),
                "date": local_date,
                "provider": "Codex (Session)",
                "modelProvider": model_provider,
                "model": model,
                "tier": service_tier,
                "requestedTier": requested_tier,
                "tierSource": service_tier_source,
                "source": "codex",
                "threadSource": thread_source,
                "sessionId": resolved_session,
                "project": "",
                **request_usage,
                "costUsd": cost["costUsd"],
                "cost": cost["costUsd"] if cost["priceKnown"] else None,
                "priceKnown": cost["priceKnown"],
            }
        )

    if coverage["filesFailed"] > failures_before:
        return None
    resolved_session = session_id or f"file:{chat_history_session_key('codex-detail', path)}"
    try:
        resolved_path = path.resolve(strict=False)
    except OSError:
        resolved_path = path.absolute()
    return {
        "signature": {
            "size": descriptor["size"],
            "mtimeNs": descriptor["mtimeNs"],
            "contextKey": descriptor.get("contextKey") or "",
        },
        "anchor": token_stats_file_anchor(path, descriptor["size"]),
        "offset": progress["offset"],
        "parserState": {
            "sessionId": session_id,
            "model": model,
            "modelProvider": model_provider,
            "threadSource": thread_source,
            "serviceTier": service_tier,
            "requestedTier": requested_tier,
            "serviceTierSource": service_tier_source,
            "previousTotalUsage": previous_total_usage,
            "previousTotal": previous_total,
            "eventIndex": event_index,
        },
        "contribution": {
            "sessionKey": resolved_session,
            "pathKey": os.path.normcase(str(resolved_path)),
            "lifetimeTotal": lifetime_total,
            "rows": rows,
        },
        "coverage": coverage,
    }


def token_stats_build_codex_request_rows(
    descriptors: list[dict] | None = None,
    connection: sqlite3.Connection | None = None,
) -> tuple[list[dict], dict]:
    coverage = {
        **token_stats_details_source_coverage("codex"),
        "duplicateSessions": 0,
        "duplicateSessionFiles": 0,
        "selectedJsonlFiles": 0,
        "fileCacheHits": 0,
        "filesParsed": 0,
        "filesParsedIncrementally": 0,
        "bytesParsed": 0,
        "canonicalFileRule": "highest lifetimeTotal, then normalized absolute path ascending",
    }
    if descriptors is None:
        descriptors = token_stats_details_manifest()["files"]["codex"]
    coverage["jsonlFiles"] = len(descriptors)
    coverage["tierFallback"] = copy.deepcopy(
        descriptors[0].get("tierFallback")
        if descriptors and isinstance(descriptors[0].get("tierFallback"), dict)
        else token_stats_codex_config_tier()
    )
    contributions = {}
    session_file_counts: dict[str, int] = defaultdict(int)
    live_path_keys = {descriptor["pathKey"] for descriptor in descriptors}
    for descriptor in descriptors:
        path = Path(descriptor["path"])
        cached = token_stats_details_cache_load(
            connection, "codex", descriptor["pathKey"]
        )
        payload = None
        parse_mode = "full"
        if token_stats_cache_is_exact(cached, descriptor):
            payload = cached
            coverage["fileCacheHits"] += 1
        else:
            file_coverage = token_stats_details_source_coverage("codex")
            incremental_cached = None
            if token_stats_cache_is_append(cached, path, descriptor):
                incremental_cached = cached
                file_coverage = copy.deepcopy(cached.get("coverage", file_coverage))
                parse_mode = "incremental"
            start_offset = (
                token_stats_nonnegative_int(incremental_cached.get("offset")) or 0
                if incremental_cached
                else 0
            )
            failed_before = file_coverage["filesFailed"]
            try:
                payload = token_stats_detail_codex_file_incremental(
                    path,
                    file_coverage,
                    descriptor,
                    incremental_cached,
                )
            except Exception as exc:
                if file_coverage["filesFailed"] == failed_before:
                    token_stats_record_error(file_coverage, path, exc)
                payload = None
            coverage["filesParsed"] += 1
            coverage["bytesParsed"] += max(0, descriptor["size"] - start_offset)
            if parse_mode == "incremental":
                coverage["filesParsedIncrementally"] += 1
            if payload is not None:
                token_stats_details_cache_save(
                    connection, "codex", descriptor, payload
                )
        if payload is None:
            continue
        token_stats_merge_file_coverage(coverage, payload.get("coverage", {}))
        contribution = payload.get("contribution")
        if not isinstance(contribution, dict):
            continue
        session_key = contribution["sessionKey"]
        session_file_counts[session_key] += 1
        selected = contributions.get(session_key)
        if (
            selected is None
            or contribution["lifetimeTotal"] > selected["lifetimeTotal"]
            or (
                contribution["lifetimeTotal"] == selected["lifetimeTotal"]
                and contribution["pathKey"] < selected["pathKey"]
            )
        ):
            contributions[session_key] = contribution

    coverage["duplicateSessions"] = sum(count > 1 for count in session_file_counts.values())
    coverage["duplicateSessionFiles"] = sum(
        max(0, count - 1) for count in session_file_counts.values()
    )
    coverage["selectedJsonlFiles"] = len(contributions)
    rows = [
        row
        for contribution in contributions.values()
        for row in contribution["rows"]
    ]
    coverage["tierCounts"] = dict(
        sorted(Counter(str(row.get("tier") or "unknown") for row in rows).items())
    )
    coverage["tierSourceCounts"] = dict(
        sorted(Counter(str(row.get("tierSource") or "unknown") for row in rows).items())
    )
    coverage["requestRows"] = len(rows)
    token_stats_details_cache_cleanup(connection, "codex", live_path_keys)
    return rows, coverage


def token_stats_detail_claude_file_incremental(
    path: Path,
    projects_root: Path,
    descriptor: dict,
    coverage: dict,
    cached: dict | None,
) -> dict | None:
    contribution = cached.get("contribution", {}) if isinstance(cached, dict) else {}
    selected_rows = {
        item["key"]: item["row"]
        for item in contribution.get("candidates", [])
        if isinstance(item, dict) and isinstance(item.get("row"), dict) and item.get("key")
    }
    session_days = {
        (str(item.get("sessionId") or ""), str(item.get("date") or ""))
        for item in contribution.get("sessionDays", [])
        if isinstance(item, dict) and item.get("sessionId") and item.get("date")
    }
    authoritative_token_buckets = {
        (str(item.get("date") or ""), str(item.get("model") or "unknown").casefold())
        for item in contribution.get("authoritativeTokenBuckets", [])
        if isinstance(item, dict) and item.get("date")
    }
    line_index = token_stats_nonnegative_int(
        (cached or {}).get("parserState", {}).get("lineIndex")
    ) or 0
    start_offset = token_stats_nonnegative_int((cached or {}).get("offset")) or 0
    progress = {"offset": start_offset}
    project_name = token_stats_claude_project_name(path, projects_root)
    fallback_session = f"file:{chat_history_session_key('claude-detail', path)}"
    failures_before = coverage["filesFailed"]
    try:
        for item in token_stats_incremental_jsonl_objects(
            path,
            coverage,
            start_offset,
            descriptor["size"],
            progress,
            count_file_read=not bool(cached),
        ):
            line_index += 1
            message = item.get("message") if isinstance(item.get("message"), dict) else {}
            raw_session_id = item.get("sessionId") or item.get("session_id")
            session_id = str(raw_session_id) if raw_session_id else fallback_session
            parsed_timestamp, local_timestamp, local_date = token_stats_request_timestamp(
                item.get("timestamp")
            )
            if local_date:
                session_days.add((session_id, local_date))
            usage = message.get("usage")
            request_usage = token_stats_claude_request_usage(usage)
            if request_usage is None:
                continue
            coverage["usageCandidates"] += 1
            if local_date:
                authoritative_token_buckets.add(
                    (local_date, str(message.get("model") or "unknown").strip().casefold())
                )
            message_id = message.get("id")
            if message_id:
                dedupe_key = f"{session_id}:message:{message_id}"
            else:
                coverage["usageRowsWithoutMessageId"] += 1
                fallback_id = item.get("uuid") or f"{os.path.normcase(str(path))}:{line_index}"
                dedupe_key = f"{session_id}:fallback:{fallback_id}"

            if item.get("timestamp") and parsed_timestamp is None:
                coverage["invalidTimestamps"] += 1
            if parsed_timestamp is None:
                coverage["usageRowsWithoutValidDate"] += 1
            model = message.get("model")
            if not isinstance(model, str) or not model.strip():
                model = "unknown"
            requested_tier = usage.get("service_tier")
            service_tier = token_stats_normalize_service_tier(requested_tier) or "unknown"
            service_tier_source = (
                "log.claude.usage"
                if isinstance(requested_tier, str) and requested_tier.strip()
                else "unknown"
            )
            row = {
                "id": token_stats_request_id("claude", dedupe_key),
                "timestamp": local_timestamp,
                "timestampUtc": (
                    parsed_timestamp.isoformat().replace("+00:00", "Z")
                    if parsed_timestamp is not None
                    else None
                ),
                "date": local_date,
                "provider": "Claude Code",
                "modelProvider": "unknown",
                "model": model.strip() if isinstance(model, str) else "unknown",
                "tier": service_tier,
                "requestedTier": (
                    requested_tier.strip().lower()
                    if isinstance(requested_tier, str) and requested_tier.strip()
                    else "unknown"
                ),
                "tierSource": service_tier_source,
                "source": "claude",
                "threadSource": "claude-code",
                "sessionId": session_id,
                "project": project_name,
                **request_usage,
                "costUsd": 0,
                "cost": None,
                "priceKnown": False,
            }
            selected = selected_rows.get(dedupe_key)
            candidate_key = (row["totalTokens"], row["timestampUtc"] or "")
            selected_key = (
                (selected["totalTokens"], selected["timestampUtc"] or "")
                if selected
                else None
            )
            if selected is None or candidate_key > selected_key:
                selected_rows[dedupe_key] = row
    except Exception as exc:
        if coverage["filesFailed"] == failures_before:
            token_stats_record_error(coverage, path, exc)
        return None
    return {
        "signature": {
            "size": descriptor["size"],
            "mtimeNs": descriptor["mtimeNs"],
        },
        "anchor": token_stats_file_anchor(path, descriptor["size"]),
        "offset": progress["offset"],
        "parserState": {"lineIndex": line_index},
        "contribution": {
            "candidates": [
                {"key": key, "row": row} for key, row in selected_rows.items()
            ],
            "sessionDays": [
                {"sessionId": session_id, "date": day}
                for session_id, day in sorted(session_days)
            ],
            "authoritativeTokenBuckets": [
                {"date": day, "model": model}
                for day, model in sorted(authoritative_token_buckets)
            ],
        },
        "coverage": coverage,
    }


def token_stats_build_claude_request_rows(
    descriptors: list[dict] | None = None,
    projects_root: Path | None = None,
    connection: sqlite3.Connection | None = None,
) -> tuple[list[dict], dict]:
    coverage = {
        **token_stats_details_source_coverage("claude"),
        "duplicateUsageRows": 0,
        "fileCacheHits": 0,
        "filesParsed": 0,
        "filesParsedIncrementally": 0,
        "bytesParsed": 0,
        "deduplicationRule": "sessionId + message.id; keep maximum total, then latest timestamp",
    }
    projects_root = projects_root or (CHAT_HISTORY_CLAUDE_HOME / "projects")
    if descriptors is None:
        descriptors = token_stats_details_manifest()["files"]["claude"]
    coverage["jsonlFiles"] = len(descriptors)
    selected_rows: dict[str, dict] = {}
    authoritative_sessions: set[str] = set()
    authoritative_session_days: dict[str, set[date]] = defaultdict(set)
    authoritative_token_buckets: set[tuple[date, str]] = set()
    live_path_keys = {descriptor["pathKey"] for descriptor in descriptors}
    for descriptor in descriptors:
        path = Path(descriptor["path"])
        cached = token_stats_details_cache_load(
            connection, "claude", descriptor["pathKey"]
        )
        payload = None
        if token_stats_cache_is_exact(cached, descriptor):
            payload = cached
            coverage["fileCacheHits"] += 1
        else:
            file_coverage = token_stats_details_source_coverage("claude")
            incremental_cached = None
            if token_stats_cache_is_append(cached, path, descriptor):
                incremental_cached = cached
                file_coverage = copy.deepcopy(cached.get("coverage", file_coverage))
            start_offset = (
                token_stats_nonnegative_int(incremental_cached.get("offset")) or 0
                if incremental_cached
                else 0
            )
            payload = token_stats_detail_claude_file_incremental(
                path,
                projects_root,
                descriptor,
                file_coverage,
                incremental_cached,
            )
            coverage["filesParsed"] += 1
            coverage["bytesParsed"] += max(0, descriptor["size"] - start_offset)
            if incremental_cached:
                coverage["filesParsedIncrementally"] += 1
            if payload is not None:
                token_stats_details_cache_save(
                    connection, "claude", descriptor, payload
                )
        if payload is None:
            continue
        token_stats_merge_file_coverage(coverage, payload.get("coverage", {}))
        contribution = payload.get("contribution", {})
        for session_day in contribution.get("sessionDays", []):
            if not isinstance(session_day, dict):
                continue
            session_id = str(session_day.get("sessionId") or "")
            try:
                day = date.fromisoformat(str(session_day.get("date") or ""))
            except ValueError:
                continue
            if session_id:
                authoritative_sessions.add(session_id)
                authoritative_session_days[session_id].add(day)
        for token_bucket in contribution.get("authoritativeTokenBuckets", []):
            if not isinstance(token_bucket, dict):
                continue
            try:
                bucket_day = date.fromisoformat(str(token_bucket.get("date") or ""))
            except ValueError:
                continue
            bucket_model = str(token_bucket.get("model") or "unknown").casefold()
            authoritative_token_buckets.add((bucket_day, bucket_model))
        for candidate in contribution.get("candidates", []):
            if not isinstance(candidate, dict):
                continue
            dedupe_key = candidate.get("key")
            row = candidate.get("row")
            if not dedupe_key or not isinstance(row, dict):
                continue
            selected = selected_rows.get(dedupe_key)
            candidate_key = (row["totalTokens"], row["timestampUtc"] or "")
            selected_key = (
                (selected["totalTokens"], selected["timestampUtc"] or "")
                if selected
                else None
            )
            if selected is None or candidate_key > selected_key:
                selected_rows[dedupe_key] = row

    coverage["duplicateUsageRows"] = max(0, coverage["usageCandidates"] - len(selected_rows))
    coverage["requestRows"] = len(selected_rows)
    claude_home = projects_root.parent
    fallback = token_stats_claude_legacy_fallback(
        authoritative_sessions,
        authoritative_session_days,
        authoritative_token_buckets,
        claude_home,
        projects_root,
    )
    fallback_token_total = sum(row["tokens"] for row in fallback["tokenBuckets"])
    coverage["authoritativeSessions"] = len(authoritative_sessions)
    coverage["legacyFallback"] = fallback["coverage"]
    coverage["fallbackSessionsAdded"] = len(fallback["missingSessionIds"])
    coverage["fallbackActivityDays"] = len(
        {activity["date"] for activity in fallback["activities"]}
        | set(fallback["dailyActivity"])
    )
    coverage["fallbackTokenBucketsAdded"] = len(fallback["tokenBuckets"])
    coverage["fallbackTokenTotal"] = fallback_token_total
    coverage["activityOnlySessions"] = len(fallback["missingSessionIds"])
    coverage["missingTranscriptSessions"] = len(fallback["missingSessionIds"])
    coverage["anonymousActivitySessionDays"] = fallback[
        "anonymousActivitySessionDays"
    ]
    coverage["unallocatedCacheReadTokens"] = fallback["coverage"]["statsCache"][
        "unallocatedCacheReadTokens"
    ]
    coverage["unallocatedCacheCreationTokens"] = fallback["coverage"]["statsCache"][
        "unallocatedCacheCreationTokens"
    ]
    coverage["countingRule"] = (
        "projects/**/*.jsonl is authoritative; legacy sessions are added only when their "
        "sessionId is absent; stats-cache date+model token buckets are added only when the "
        "same authoritative JSONL bucket is absent; aggregate activity never invents tokens"
    )
    coverage["_legacyActivities"] = fallback["activities"]
    coverage["_legacyTokenBuckets"] = fallback["tokenBuckets"]
    coverage["_legacyDailyActivity"] = fallback["dailyActivity"]
    request_session_days = {
        (str(row.get("sessionId") or ""), str(row.get("date") or ""))
        for row in selected_rows.values()
        if row.get("sessionId") and row.get("date")
    }
    coverage["_authoritativeActivities"] = [
        {
            "sessionId": session_id,
            "date": day.isoformat(),
            "sources": ["projects-jsonl"],
            "anonymous": False,
        }
        for session_id, days in sorted(authoritative_session_days.items())
        for day in sorted(days)
        if (session_id, day.isoformat()) not in request_session_days
    ]
    coverage["authoritativeActivityRows"] = len(
        coverage["_authoritativeActivities"]
    )
    token_stats_details_cache_cleanup(connection, "claude", live_path_keys)
    return list(selected_rows.values()), coverage


def token_stats_detail_deepseek_file(
    path: Path,
    descriptor: dict,
    coverage: dict,
) -> dict | None:
    failures_before = coverage["filesFailed"]
    candidates = token_stats_deepseek_file_candidates(path, coverage)
    if coverage["filesFailed"] > failures_before:
        return None
    return {
        "signature": {
            "size": descriptor["size"],
            "mtimeNs": descriptor["mtimeNs"],
        },
        "anchor": token_stats_file_anchor(path, descriptor["size"]),
        "offset": descriptor["size"],
        "contribution": {"candidates": candidates},
        "coverage": coverage,
    }


def token_stats_build_deepseek_request_rows(
    descriptors: list[dict] | None = None,
    connection: sqlite3.Connection | None = None,
) -> tuple[list[dict], dict]:
    coverage = {
        **token_stats_details_source_coverage("deepseek"),
        "duplicateUsageRows": 0,
        "fileCacheHits": 0,
        "filesParsed": 0,
        "filesParsedIncrementally": 0,
        "bytesParsed": 0,
        "deduplicationRule": (
            "sessionId + data.message.id; keep maximum total, then latest timestamp"
        ),
        "countingRule": (
            "only assistant/message data.usage; assistant/chunk usage is excluded; "
            "reasoningTokens is an output detail and is not added again"
        ),
    }
    if descriptors is None:
        descriptors = token_stats_details_manifest()["files"]["deepseek"]
    coverage["jsonlFiles"] = len(descriptors)
    coverage["sourceFormats"] = dict(Counter(Path(item["path"]).name for item in descriptors))
    coverage["sessionsRoot"] = str(TOKEN_STATS_DEEPSEEK_HOME / "sessions")
    live_path_keys = {descriptor["pathKey"] for descriptor in descriptors}
    if zstandard is None:
        error = (
            "Python package 'zstandard' is unavailable; "
            "DeepSeek token logs were not read."
        )
        coverage["dependencyError"] = error
        coverage["filesFailed"] = len(descriptors)
        coverage["fileErrors"] = (
            [
                {
                    "file": str(TOKEN_STATS_DEEPSEEK_HOME / "sessions"),
                    "error": error,
                }
            ]
            if descriptors
            else []
        )
        token_stats_details_cache_cleanup(connection, "deepseek", live_path_keys)
        return [], coverage

    selected_rows: dict[str, dict] = {}
    for descriptor in descriptors:
        path = Path(descriptor["path"])
        cached = token_stats_details_cache_load(
            connection, "deepseek", descriptor["pathKey"]
        )
        payload = None
        if token_stats_cache_is_exact(cached, descriptor):
            payload = cached
            coverage["fileCacheHits"] += 1
        else:
            file_coverage = token_stats_details_source_coverage("deepseek")
            payload = token_stats_detail_deepseek_file(
                path, descriptor, file_coverage
            )
            coverage["filesParsed"] += 1
            coverage["bytesParsed"] += descriptor["size"]
            if payload is not None:
                token_stats_details_cache_save(
                    connection, "deepseek", descriptor, payload
                )
        if payload is None:
            continue
        token_stats_merge_file_coverage(coverage, payload.get("coverage", {}))
        for candidate in payload.get("contribution", {}).get("candidates", []):
            if not isinstance(candidate, dict):
                continue
            dedupe_key = candidate.get("key")
            row = candidate.get("row")
            if not dedupe_key or not isinstance(row, dict):
                continue
            selected = selected_rows.get(dedupe_key)
            candidate_key = token_stats_deepseek_candidate_key(row)
            selected_key = token_stats_deepseek_candidate_key(selected) if selected else None
            if selected is None or candidate_key > selected_key:
                selected_rows[dedupe_key] = row

    coverage["duplicateUsageRows"] = max(
        0, coverage["usageCandidates"] - len(selected_rows)
    )
    coverage["requestRows"] = len(selected_rows)
    token_stats_details_cache_cleanup(connection, "deepseek", live_path_keys)
    return list(selected_rows.values()), coverage


def token_stats_detail_cline_file(
    path: Path,
    descriptor: dict,
    coverage: dict,
) -> dict | None:
    failures_before = coverage["filesFailed"]
    candidates = token_stats_cline_file_candidates(
        path, str(descriptor.get("provider") or "Cline"), coverage
    )
    if coverage["filesFailed"] > failures_before:
        return None
    return {
        "signature": {
            "size": descriptor["size"],
            "mtimeNs": descriptor["mtimeNs"],
            "contextKey": descriptor.get("contextKey") or "",
        },
        "anchor": token_stats_file_anchor(path, descriptor["size"]),
        "offset": descriptor["size"],
        "contribution": {"candidates": candidates},
        "coverage": coverage,
    }


def token_stats_build_cline_request_rows(
    descriptors: list[dict] | None = None,
    connection: sqlite3.Connection | None = None,
) -> tuple[list[dict], dict]:
    coverage = {
        **token_stats_details_source_coverage("deepseek"),
        "duplicateUsageRows": 0,
        "fileCacheHits": 0,
        "filesParsed": 0,
        "filesParsedIncrementally": 0,
        "bytesParsed": 0,
        "clineFiles": 0,
        "aggregateFallbackRows": 0,
        "deduplicationRule": (
            "task id + request timestamp + conversationHistoryIndex; "
            "keep the row with the largest verified request total"
        ),
        "countingRule": (
            "request-level ui_messages say=api_req_started usage only; "
            "taskHistory aggregates are not counted or allocated across dates/models"
        ),
        "attributionRule": (
            "request timestamp; modelInfo when present, otherwise the most recent "
            "task_metadata.model_usage event at or before the request"
        ),
    }
    if descriptors is None:
        descriptors = token_stats_details_manifest()["files"]["deepseekCline"]
    coverage["clineFiles"] = len(descriptors)
    live_path_keys = {descriptor["pathKey"] for descriptor in descriptors}
    selected_rows: dict[str, dict] = {}
    for descriptor in descriptors:
        path = Path(descriptor["path"])
        cached = token_stats_details_cache_load(
            connection, "deepseek-cline", descriptor["pathKey"]
        )
        payload = None
        if token_stats_cache_is_exact(cached, descriptor):
            payload = cached
            coverage["fileCacheHits"] += 1
        else:
            file_coverage = token_stats_details_source_coverage("deepseek")
            payload = token_stats_detail_cline_file(path, descriptor, file_coverage)
            coverage["filesParsed"] += 1
            coverage["bytesParsed"] += descriptor["size"]
            if payload is not None:
                token_stats_details_cache_save(
                    connection, "deepseek-cline", descriptor, payload
                )
        if payload is None:
            continue
        token_stats_merge_file_coverage(coverage, payload.get("coverage", {}))
        for candidate in payload.get("contribution", {}).get("candidates", []):
            if not isinstance(candidate, dict):
                continue
            dedupe_key = candidate.get("key")
            row = candidate.get("row")
            if not dedupe_key or not isinstance(row, dict):
                continue
            selected = selected_rows.get(dedupe_key)
            candidate_key = (row["totalTokens"], row.get("timestampUtc") or "")
            selected_key = (
                (selected["totalTokens"], selected.get("timestampUtc") or "")
                if selected
                else None
            )
            if selected is None or candidate_key > selected_key:
                selected_rows[dedupe_key] = row
    rows = list(selected_rows.values())
    coverage["duplicateUsageRows"] = max(
        0, coverage["usageCandidates"] - len(rows) - coverage["nonDeepSeekUsageRows"]
    )
    coverage["requestRows"] = len(rows)
    coverage["providerCoverage"] = {
        provider: {
            "requestRows": len(provider_rows),
            "tokens": sum(row["totalTokens"] for row in provider_rows),
        }
        for provider in ("Cline Chinese", "Cline")
        for provider_rows in [[row for row in rows if row["provider"] == provider]]
    }
    token_stats_details_cache_cleanup(connection, "deepseek-cline", live_path_keys)
    return rows, coverage


def token_stats_combine_deepseek_rows(
    client_rows: list[dict], cline_rows: list[dict]
) -> tuple[list[dict], int]:
    rows = []
    fingerprints: dict[str, tuple[str, int]] = {}
    duplicate_count = 0
    for original in client_rows + cline_rows:
        row = dict(original)
        if row.get("threadSource") == "deepseek-client":
            row["modelProvider"] = row.get("modelProvider") or row.get("provider") or "unknown"
            row["provider"] = "DeepSeek client"
            row["project"] = "DeepSeek client"
        fingerprint = token_stats_deepseek_cross_source_fingerprint(row)
        origin = str(row.get("threadSource") or row.get("provider") or "unknown")
        if fingerprint and fingerprint in fingerprints:
            existing_origin, existing_index = fingerprints[fingerprint]
            if existing_origin != origin:
                duplicate_count += 1
                existing = rows[existing_index]
                existing_known = bool(
                    existing.get(
                        "cacheCreationKnown", existing.get("cacheWriteKnown", False)
                    )
                )
                row_known = bool(
                    row.get("cacheCreationKnown", row.get("cacheWriteKnown", False))
                )
                if row_known and not existing_known:
                    existing["cacheCreationInputTokens"] = row.get(
                        "cacheCreationInputTokens", 0
                    )
                    existing["cacheWriteTokens"] = row.get(
                        "cacheWriteTokens", existing["cacheCreationInputTokens"]
                    )
                    existing["cacheCreationKnown"] = True
                    existing["cacheWriteKnown"] = True
                if token_stats_cache_read_known(row) and not token_stats_cache_read_known(existing):
                    existing["cachedInputTokens"] = row.get("cachedInputTokens", 0)
                    existing["cacheReadKnown"] = True
                continue
        if fingerprint:
            fingerprints[fingerprint] = (origin, len(rows))
        rows.append(row)
    rows.sort(key=lambda item: (item.get("timestampUtc") or "", item["id"]), reverse=True)
    return rows, duplicate_count


def token_stats_combined_deepseek_coverage(
    client_coverage: dict, cline_coverage: dict, rows: list[dict], cross_duplicates: int
) -> dict:
    coverage = token_stats_details_source_coverage("deepseek")
    token_stats_merge_file_coverage(coverage, client_coverage)
    token_stats_merge_file_coverage(coverage, cline_coverage)
    coverage.update(
        {
            "zstdFiles": int(client_coverage.get("jsonlFiles") or 0),
            "sourceFormats": dict(client_coverage.get("sourceFormats") or {}),
            "sessionsRoot": client_coverage.get("sessionsRoot"),
            "clineFiles": int(cline_coverage.get("clineFiles") or 0),
            "requestRows": len(rows),
            "crossSourceDuplicateRows": cross_duplicates,
            "aggregateFallbackRows": 0,
            "dependencyAvailable": client_coverage.get("dependencyAvailable", True),
            "dependencyError": client_coverage.get("dependencyError"),
            "countingRule": (
                "DeepSeek client assistant/message data.usage plus request-level Cline "
                "ui_messages api_req_started usage; no task aggregate allocation"
            ),
            "deduplicationRule": (
                "native request ids within each source, then exact timestamp + model + "
                "token-breakdown fingerprint across sources"
            ),
            "providerCoverage": {
                provider: {
                    "requestRows": len(provider_rows),
                    "tokens": sum(row["totalTokens"] for row in provider_rows),
                }
                for provider in ("DeepSeek client", "Cline Chinese", "Cline")
                for provider_rows in [[row for row in rows if row["provider"] == provider]]
            },
        }
    )
    return coverage


def token_stats_deepseek_summary_from_rows(rows: list[dict], coverage: dict) -> dict:
    daily: dict[date, dict] = defaultdict(token_stats_deepseek_daily_record)
    sessions = set()
    for row in rows:
        if row.get("sessionId"):
            sessions.add(row["sessionId"])
        try:
            day = date.fromisoformat(row["date"]) if row.get("date") else None
        except (TypeError, ValueError):
            day = None
        if day is None:
            continue
        record = daily[day]
        record["usageRows"] += 1
        record["tokens"] += row["totalTokens"]
        record["inputTokens"] += row["inputTokens"]
        record["outputTokens"] += row["outputTokens"]
        record["cacheReadTokens"] += row["cachedInputTokens"]
        if token_stats_cache_read_known(row):
            record["cacheReadKnownRequestCount"] += 1
        else:
            record["cacheReadUnknownRequestCount"] += 1
        record["cacheCreationTokens"] += row["cacheCreationInputTokens"]
        if bool(row.get("cacheCreationKnown", row.get("cacheWriteKnown", False))):
            record["cacheCreationKnownRequestCount"] += 1
        else:
            record["cacheCreationUnknownRequestCount"] += 1
        record["reasoningTokens"] += row["reasoningTokens"]
        if row.get("sessionId"):
            record["sessions"].add(row["sessionId"])
    coverage["sessions"] = len(sessions)
    coverage["dailyTokenTotal"] = sum(record["tokens"] for record in daily.values())
    days, top_days, metrics = token_stats_deepseek_days(daily, coverage)
    total = sum(row["totalTokens"] for row in rows)
    return {
        "canonicalTotal": total,
        "totalWithCacheRead": total,
        "totalWithoutCacheRead": sum(
            row["inputTokens"] + row["outputTokens"] for row in rows
        ),
        "totalSessions": len(sessions),
        "totalProviders": len({row["provider"] for row in rows}),
        "totalModels": len({row["model"] for row in rows}),
        **metrics,
        "days": days,
        "topDays": top_days,
        "providers": token_stats_group_requests(rows, "provider"),
        "models": token_stats_group_models(rows),
        "coverage": coverage,
    }


def token_stats_build_deepseek_combined(manifest: dict | None = None) -> dict:
    manifest = manifest or token_stats_details_manifest()
    connection = token_stats_details_cache_connection()
    try:
        client_rows, client_coverage = token_stats_build_deepseek_request_rows(
            manifest["files"]["deepseek"], connection
        )
        cline_rows, cline_coverage = token_stats_build_cline_request_rows(
            manifest["files"]["deepseekCline"], connection
        )
        if connection is not None:
            try:
                connection.commit()
            except (OSError, sqlite3.Error):
                pass
    finally:
        if connection is not None:
            try:
                connection.close()
            except sqlite3.Error:
                pass
    rows, cross_duplicates = token_stats_combine_deepseek_rows(client_rows, cline_rows)
    coverage = token_stats_combined_deepseek_coverage(
        client_coverage, cline_coverage, rows, cross_duplicates
    )
    return token_stats_deepseek_summary_from_rows(rows, coverage)


def token_stats_build_workbuddy_request_rows(
    descriptors: list[dict] | None = None,
    connection: sqlite3.Connection | None = None,
) -> tuple[list[dict], dict]:
    with TOKEN_STATS_WORKBUDDY_BUILD_LOCK:
        coverage = {
            **token_stats_details_source_coverage("workbuddy"),
            "jsonlFiles": 0,
        }
        if descriptors is None:
            descriptors = token_stats_details_manifest()["files"]["workbuddy"]
        coverage["jsonlFiles"] = len(descriptors)
        live_path_keys = {descriptor["pathKey"] for descriptor in descriptors}
        all_rows = []
        for descriptor in descriptors:
            path = Path(descriptor["path"])
            path_key = descriptor["pathKey"]
            cached = token_stats_details_cache_load(connection, "workbuddy", path_key)
            if token_stats_cache_is_exact(cached, descriptor):
                all_rows.extend(cached.get("rows", []))
                coverage["fileCacheHits"] += 1
                continue

            start_offset = 0
            rows = []
            if token_stats_cache_is_append(cached, path, descriptor):
                rows = list(cached.get("rows", []))
                start_offset = token_stats_nonnegative_int(cached.get("parserOffset")) or 0
                coverage["filesAppended"] += 1
            else:
                coverage["filesParsed"] += 1
            failed_before = coverage["filesFailed"]
            new_rows, parser_offset = token_stats_parse_workbuddy_segment(
                path,
                coverage,
                start_offset,
                descriptor["size"],
            )
            if coverage["filesFailed"] > failed_before:
                continue
            rows.extend(new_rows)
            all_rows.extend(rows)
            token_stats_details_cache_save(
                connection,
                "workbuddy",
                descriptor,
                {
                    "signature": {
                        "size": descriptor["size"],
                        "mtimeNs": descriptor["mtimeNs"],
                    },
                    "anchor": token_stats_file_anchor(path, descriptor["size"]),
                    "parserOffset": parser_offset,
                    "rows": rows,
                },
            )

        deduplicated = []
        seen_ids = set()
        for row in all_rows:
            row_id = row.get("id")
            if row_id in seen_ids:
                coverage["duplicateUsageRowsSkipped"] += 1
                continue
            seen_ids.add(row_id)
            deduplicated.append(row)
        deduplicated.sort(
            key=lambda item: (item.get("timestampUtc") or "", item["id"]),
            reverse=True,
        )
        coverage["requestRows"] = len(deduplicated)
        coverage["countingRule"] = (
            "One WorkBuddy model-call record per usage-bearing JSONL object; rawUsage, "
            "providerData.usage, and message.usage are alternative representations, not sums"
        )
        coverage["deduplicationRule"] = (
            "session id plus provider message id, falling back to the usage record id"
        )
        token_stats_details_cache_cleanup(connection, "workbuddy", live_path_keys)
        if connection is not None:
            try:
                connection.commit()
            except (OSError, sqlite3.Error):
                pass
        return deduplicated, coverage


def token_stats_build_workbuddy(descriptors: list[dict] | None = None) -> dict:
    if descriptors is None:
        descriptors = token_stats_details_manifest()["files"]["workbuddy"]
    connection = token_stats_details_cache_connection()
    try:
        rows, coverage = token_stats_build_workbuddy_request_rows(
            descriptors, connection
        )
        if connection is not None:
            try:
                connection.commit()
            except (OSError, sqlite3.Error):
                pass
    finally:
        if connection is not None:
            try:
                connection.close()
            except sqlite3.Error:
                pass
    return token_stats_deepseek_summary_from_rows(rows, coverage)


def token_stats_build_antigravity_request_rows(
    descriptors: list[dict] | None = None,
    connection: sqlite3.Connection | None = None,
) -> tuple[list[dict], dict]:
    coverage = {
        **token_stats_details_source_coverage("antigravity"),
        "sqliteDbFiles": 0,
        "fileCacheHits": 0,
        "filesParsed": 0,
    }
    if descriptors is None:
        descriptors = token_stats_details_manifest()["files"]["antigravity"]
    coverage["sqliteDbFiles"] = len(descriptors)
    live_path_keys = {descriptor["pathKey"] for descriptor in descriptors}
    summaries = token_stats_antigravity_summaries()
    all_rows = []
    for descriptor in descriptors:
        path = Path(descriptor["path"])
        path_key = descriptor["pathKey"]
        cached = token_stats_details_cache_load(connection, "antigravity", path_key)
        if token_stats_cache_is_exact(cached, descriptor):
            rows = cached.get("rows", [])
            all_rows.extend(rows)
            coverage["fileCacheHits"] += 1
            continue
        coverage["filesParsed"] += 1
        failed_before = coverage["filesFailed"]
        candidates = token_stats_antigravity_db_candidates(path, summaries, coverage)
        if coverage["filesFailed"] > failed_before:
            continue
        all_rows.extend(candidates)
        token_stats_details_cache_save(
            connection,
            "antigravity",
            descriptor,
            {
                "signature": {
                    "size": descriptor["size"],
                    "mtimeNs": descriptor["mtimeNs"],
                    "contextKey": descriptor.get("contextKey") or "",
                },
                "rows": candidates,
            },
        )
    coverage["requestRows"] = len(all_rows)
    token_stats_details_cache_cleanup(connection, "antigravity", live_path_keys)
    return all_rows, coverage


def token_stats_index_requests_by_source_date(rows: list[dict]) -> dict[str, dict[str, list[dict]]]:
    indexed: dict[str, dict[str, list[dict]]] = {"all": {}}
    for row in rows:
        day = row.get("date")
        if not isinstance(day, str):
            continue
        source = str(row.get("source") or "unknown")
        indexed["all"].setdefault(day, []).append(row)
        indexed.setdefault(source, {}).setdefault(day, []).append(row)
    return indexed


def token_stats_index_period_rows(
    index: dict, start: date, end: date, source: str
) -> list[dict]:
    by_source_date = index.get("_requestsBySourceDate")
    if not isinstance(by_source_date, dict):
        start_text = start.isoformat()
        end_text = end.isoformat()
        return [
            row
            for row in index.get("requests", [])
            if row.get("date")
            and start_text <= row["date"] <= end_text
            and (source == "all" or row.get("source") == source)
        ]
    buckets = by_source_date.get(source)
    if not isinstance(buckets, dict):
        return []
    start_text = start.isoformat()
    end_text = end.isoformat()
    selected = []
    # The source rows are already sorted newest-first. Local calendar dates are
    # monotonic with their UTC timestamps, so descending buckets preserve API order.
    for day in sorted(
        (key for key in buckets if start_text <= key <= end_text), reverse=True
    ):
        selected.extend(buckets[day])
    # Timestamp-less fallback rows can carry a recent local date while sorting
    # after every timestamped request in the canonical list. Restore that same
    # public ordering after joining date buckets.
    selected.sort(
        key=lambda item: (item.get("timestampUtc") or "", item["id"]),
        reverse=True,
    )
    return selected


def token_stats_build_details_index_uncached(manifest: dict | None = None) -> dict:
    manifest = manifest or token_stats_details_manifest()
    connection = token_stats_details_cache_connection()
    try:
        codex_rows, codex_coverage = token_stats_build_codex_request_rows(
            manifest["files"]["codex"], connection
        )
        claude_rows, claude_coverage = token_stats_build_claude_request_rows(
            manifest["files"]["claude"], manifest["claudeRoot"], connection
        )
        claude_legacy = {
            "activities": claude_coverage.pop("_legacyActivities", []),
            "tokenBuckets": claude_coverage.pop("_legacyTokenBuckets", []),
            "dailyActivity": claude_coverage.pop("_legacyDailyActivity", {}),
            "authoritativeActivities": claude_coverage.pop(
                "_authoritativeActivities", []
            ),
        }
        deepseek_rows, deepseek_coverage = token_stats_build_deepseek_request_rows(
            manifest["files"]["deepseek"], connection
        )
        cline_rows, cline_coverage = token_stats_build_cline_request_rows(
            manifest["files"]["deepseekCline"], connection
        )
        deepseek_rows, cross_duplicates = token_stats_combine_deepseek_rows(
            deepseek_rows, cline_rows
        )
        deepseek_coverage = token_stats_combined_deepseek_coverage(
            deepseek_coverage, cline_coverage, deepseek_rows, cross_duplicates
        )
        # End any earlier source's SQLite write transaction before taking the
        # WorkBuddy lock. The summary path takes that lock before updating the
        # same cache, so carrying the transaction across it reverses lock order.
        if connection is not None:
            try:
                connection.commit()
            except (OSError, sqlite3.Error):
                pass
        workbuddy_rows, workbuddy_coverage = token_stats_build_workbuddy_request_rows(
            manifest["files"]["workbuddy"], connection
        )
        antigravity_rows, antigravity_coverage = token_stats_build_antigravity_request_rows(
            manifest["files"]["antigravity"], connection
        )
        if connection is not None:
            try:
                connection.commit()
            except (OSError, sqlite3.Error):
                pass
    finally:
        if connection is not None:
            try:
                connection.close()
            except sqlite3.Error:
                pass
    rows = codex_rows + claude_rows + deepseek_rows + workbuddy_rows + antigravity_rows
    rows.sort(key=lambda item: (item.get("timestampUtc") or "", item["id"]), reverse=True)
    dated_rows = [row["date"] for row in rows if isinstance(row.get("date"), str)]
    return {
        "generatedAt": datetime.now(TOKEN_STATS_TZ).isoformat(timespec="seconds"),
        "requests": rows,
        "_requestsBySourceDate": token_stats_index_requests_by_source_date(rows),
        "_requestAvailableStart": min(dated_rows) if dated_rows else None,
        "_requestAvailableEnd": max(dated_rows) if dated_rows else None,
        "legacyAggregates": {"claude": claude_legacy},
        "coverage": {
            "codex": codex_coverage,
            "claude": claude_coverage,
            "deepseek": deepseek_coverage,
            "workbuddy": workbuddy_coverage,
            "antigravity": antigravity_coverage,
            "unsupportedRequestFields": ["status", "duration"],
            "fileCache": str(TOKEN_STATS_DETAILS_FILE_CACHE_DB),
        },
    }


def token_stats_details_index(force: bool = False) -> dict:
    global TOKEN_STATS_DETAILS_CACHE
    global TOKEN_STATS_DETAILS_CACHE_BUILT_AT
    global TOKEN_STATS_DETAILS_CACHE_SIGNATURE
    global TOKEN_STATS_DETAILS_CACHE_REVISION

    observed_revision = TOKEN_STATS_DETAILS_CACHE_REVISION
    manifest = token_stats_details_manifest()
    if not force and TOKEN_STATS_DETAILS_CACHE is not None and (
        TOKEN_STATS_DETAILS_CACHE_SIGNATURE == manifest["signature"]
    ):
        return TOKEN_STATS_DETAILS_CACHE
    with TOKEN_STATS_DETAILS_BUILD_LOCK:
        if TOKEN_STATS_DETAILS_CACHE_REVISION != observed_revision:
            # A concurrent builder published while this caller was collecting
            # its lock-free manifest. Refresh once under the lock so even a
            # forced refresh cannot overwrite newer data with an old snapshot.
            manifest = token_stats_details_manifest()
        if not force and TOKEN_STATS_DETAILS_CACHE is not None and (
            TOKEN_STATS_DETAILS_CACHE_SIGNATURE == manifest["signature"]
        ):
            return TOKEN_STATS_DETAILS_CACHE
        built = token_stats_build_details_index_uncached(manifest)
        TOKEN_STATS_DETAILS_CACHE = built
        TOKEN_STATS_DETAILS_CACHE_BUILT_AT = time.monotonic()
        TOKEN_STATS_DETAILS_CACHE_SIGNATURE = manifest["signature"]
        TOKEN_STATS_DETAILS_CACHE_REVISION += 1
        return built


def token_stats_parse_period_date(value: str, field: str) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError(f"{field} must use YYYY-MM-DD.")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} is not a valid date.") from exc


def token_stats_parse_period_time(value: str, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be an ISO 8601 date-time.")
    normalized = value.strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}", normalized):
        raise ValueError(f"{field} must be a valid ISO 8601 date-time.")
    if normalized[-1:].lower() == "z":
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{field} must be a valid ISO 8601 date-time.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TOKEN_STATS_TZ)
    try:
        return parsed.astimezone(TOKEN_STATS_TZ)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"{field} must be a valid ISO 8601 date-time.") from exc


def token_stats_normalize_period_time(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(f"{field} must be a datetime.")
    if value.tzinfo is None:
        value = value.replace(tzinfo=TOKEN_STATS_TZ)
    try:
        return value.astimezone(TOKEN_STATS_TZ)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"{field} must be a valid datetime.") from exc


def token_stats_row_timestamp(row: dict) -> datetime | None:
    for field in ("timestampUtc", "timestamp"):
        parsed = chat_history_parse_timestamp(row.get(field))
        if parsed is not None:
            return parsed
    return None


def token_stats_request_metrics(
    rows: list[dict], include_anonymous_session_days: bool = False
) -> dict:
    sessions = {
        row["sessionId"]
        for row in rows
        if row.get("sessionId") and not row.get("_anonymousSessionDay")
    }
    anonymous_session_days = sum(
        bool(row.get("_anonymousSessionDay")) for row in rows
    )
    input_tokens = sum(row["inputTokens"] for row in rows)
    raw_input_tokens = sum(row["rawInputTokens"] for row in rows)
    output_tokens = sum(row["outputTokens"] for row in rows)
    cached_tokens = sum(row["cachedInputTokens"] for row in rows)
    cache_creation_tokens = sum(row["cacheCreationInputTokens"] for row in rows)
    reasoning_tokens = sum(row["reasoningTokens"] for row in rows)
    cost_usd = round(sum(float(row.get("costUsd") or 0) for row in rows), 6)
    request_count = sum(int(row.get("_requestCount", 1)) for row in rows)
    cache_read_known_request_count = sum(
        int(row.get("_requestCount", 1))
        for row in rows
        if token_stats_cache_read_known(row) and int(row.get("_requestCount", 1)) > 0
    )
    cache_read_unknown_request_count = max(0, request_count - cache_read_known_request_count)
    cache_read_coverage = token_stats_cache_coverage(cache_read_known_request_count, request_count)
    cache_hit_ratio = (
        cached_tokens / raw_input_tokens
        if cache_read_coverage == "full" and raw_input_tokens > 0
        else None
    )
    cache_creation_known_request_count = sum(
        int(row.get("_requestCount", 1))
        for row in rows
        if bool(row.get("cacheCreationKnown", row.get("cacheWriteKnown", False)))
        and int(row.get("_requestCount", 1)) > 0
    )
    cache_creation_unknown_request_count = max(
        0, request_count - cache_creation_known_request_count
    )
    if request_count > 0 and cache_creation_known_request_count == request_count:
        cache_creation_coverage = "full"
    elif cache_creation_known_request_count > 0:
        cache_creation_coverage = "partial"
    else:
        cache_creation_coverage = "none"
    priced_request_count = sum(
        bool(row.get("priceKnown")) and int(row.get("_requestCount", 1)) > 0
        for row in rows
    )
    cost_unknown = any(bool(row.get("_costUnknown")) for row in rows)
    cost_known = request_count > 0 and priced_request_count == request_count and not cost_unknown
    return {
        "requestCount": request_count,
        "sessionCount": len(sessions) + (
            anonymous_session_days if include_anonymous_session_days else 0
        ),
        "anonymousActivitySessionDays": anonymous_session_days,
        "inputTokens": input_tokens,
        "rawInputTokens": raw_input_tokens,
        "outputTokens": output_tokens,
        "cachedInputTokens": cached_tokens,
        "cacheReadKnown": cache_read_coverage == "full",
        "cacheReadKnownRequestCount": cache_read_known_request_count,
        "cacheReadUnknownRequestCount": cache_read_unknown_request_count,
        "cacheReadCoverage": cache_read_coverage,
        "cacheCreationInputTokens": cache_creation_tokens,
        "cacheWriteTokens": cache_creation_tokens,
        "cacheCreationKnown": cache_creation_coverage == "full",
        "cacheWriteKnown": cache_creation_coverage == "full",
        "cacheCreationKnownRequestCount": cache_creation_known_request_count,
        "cacheCreationUnknownRequestCount": cache_creation_unknown_request_count,
        "cacheCreationCoverage": cache_creation_coverage,
        "cacheTokens": cached_tokens + cache_creation_tokens,
        "reasoningTokens": reasoning_tokens,
        "totalTokens": sum(row["totalTokens"] for row in rows),
        "unattributedTokens": sum(
            row["totalTokens"] for row in rows if row.get("_unattributedTokens")
        ),
        "activityOnlySessions": len(
            {
                row["sessionId"]
                for row in rows
                if row.get("sessionId")
                and row.get("_activityOnly")
                and not row.get("_authoritativeActivity")
                and not row.get("_anonymousSessionDay")
            }
        )
        + (anonymous_session_days if include_anonymous_session_days else 0),
        "costUsd": cost_usd,
        "totalCost": cost_usd if cost_known else None,
        "cost": cost_usd if cost_known else None,
        "costKnown": cost_known,
        "pricedRequestCount": priced_request_count,
        "cacheHitRatio": round(cache_hit_ratio, 6) if cache_hit_ratio is not None else None,
        "cacheHitRate": round(cache_hit_ratio * 100, 2) if cache_hit_ratio is not None else None,
    }


def token_stats_group_requests(
    rows: list[dict], field: str, include_anonymous_session_days: bool = False
) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        value = row.get(field)
        grouped[str(value or "unknown")].append(row)
    result = []
    for name, group_rows in grouped.items():
        metrics = token_stats_request_metrics(
            group_rows,
            include_anonymous_session_days=include_anonymous_session_days,
        )
        result.append(
            {
                field: name,
                **metrics,
                "sources": sorted({row["source"] for row in group_rows}),
                "providers": sorted({row["provider"] for row in group_rows}),
                "successRate": None,
            }
        )
    result.sort(key=lambda item: (-item["totalTokens"], item[field].lower()))
    return result


def token_stats_group_models(
    rows: list[dict], include_anonymous_session_days: bool = False
) -> list[dict]:
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        key = (str(row.get("model") or "unknown"), str(row.get("provider") or "unknown"))
        grouped[key].append(row)
    result = []
    for (model, provider), group_rows in grouped.items():
        result.append(
            {
                "model": model,
                "provider": provider,
                **token_stats_request_metrics(
                    group_rows,
                    include_anonymous_session_days=include_anonymous_session_days,
                ),
                "sources": sorted({row["source"] for row in group_rows}),
                "successRate": None,
            }
        )
    result.sort(
        key=lambda item: (
            -item["totalTokens"],
            item["model"].lower(),
            item["provider"].lower(),
        )
    )
    return result


def token_stats_request_trend(
    rows: list[dict],
    start: date,
    end: date,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> tuple[str, list[dict]]:
    if start_time is not None and end_time is not None:
        duration = end_time - start_time
        grain = "hour" if duration <= timedelta(days=2) else "day"
        bucket_delta = timedelta(hours=1) if grain == "hour" else timedelta(days=1)
        bucket_count = max(1, math.ceil(duration / bucket_delta))
        grouped: dict[int, list[dict]] = defaultdict(list)
        start_utc = start_time.astimezone(timezone.utc)
        for row in rows:
            parsed = token_stats_row_timestamp(row)
            if parsed is None:
                continue
            bucket_index = int((parsed - start_utc) // bucket_delta)
            if 0 <= bucket_index < bucket_count:
                grouped[bucket_index].append(row)
        trend = []
        for bucket_index in range(bucket_count):
            cursor = start_time + bucket_index * bucket_delta
            key = cursor.isoformat()
            trend.append(
                {
                    "bucket": key,
                    "timestamp": key,
                    "date": key,
                    "day": cursor.date().isoformat(),
                    **token_stats_request_metrics(
                        grouped.get(bucket_index, []),
                        include_anonymous_session_days=True,
                    ),
                }
            )
        return grain, trend

    has_unbucketed_legacy = any(
        row.get("_activityOnly") or row.get("_unattributedTokens") for row in rows
    )
    grain = "hour" if (end - start).days <= 1 and not has_unbucketed_legacy else "day"
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if grain == "hour":
            timestamp = row.get("timestamp")
            if not timestamp:
                continue
            try:
                parsed = datetime.fromisoformat(timestamp)
            except (TypeError, ValueError):
                continue
            key = parsed.replace(minute=0, second=0, microsecond=0).isoformat()
        else:
            key = row.get("date")
            if not key:
                continue
        grouped[key].append(row)

    trend = []
    if grain == "hour":
        period_start = datetime.combine(start, datetime.min.time(), tzinfo=TOKEN_STATS_TZ)
        hour_count = ((end - start).days + 1) * 24
        for offset in range(hour_count):
            cursor = period_start + timedelta(hours=offset)
            key = cursor.isoformat()
            trend.append(
                {
                    "bucket": key,
                    "timestamp": key,
                    "date": key,
                    "day": cursor.date().isoformat(),
                    **token_stats_request_metrics(
                        grouped.get(key, []), include_anonymous_session_days=True
                    ),
                }
            )
    else:
        day_count = (end - start).days + 1
        for offset in range(day_count):
            cursor = start + timedelta(days=offset)
            key = cursor.isoformat()
            trend.append(
                {
                    "bucket": key,
                    "timestamp": f"{key}T00:00:00+08:00",
                    "date": key,
                    **token_stats_request_metrics(
                        grouped.get(key, []), include_anonymous_session_days=True
                    ),
                }
            )
    return grain, trend


def token_stats_claude_legacy_metric_rows(legacy: dict) -> list[dict]:
    rows = []
    empty_usage = {
        "inputTokens": 0,
        "rawInputTokens": 0,
        "outputTokens": 0,
        "cachedInputTokens": 0,
        "cacheReadKnown": False,
        "cacheCreationInputTokens": 0,
        "reasoningTokens": 0,
    }
    for activity in legacy.get("authoritativeActivities", []):
        rows.append(
            {
                "id": token_stats_request_id(
                    "claude-authoritative-activity",
                    activity.get("sessionId"),
                    activity.get("date"),
                ),
                "timestamp": None,
                "timestampUtc": None,
                "date": activity.get("date"),
                "provider": "Claude Code",
                "model": "unknown",
                "source": "claude",
                "sessionId": activity.get("sessionId") or "",
                "totalTokens": 0,
                "costUsd": 0,
                "priceKnown": False,
                "_requestCount": 0,
                "_activityOnly": True,
                "_authoritativeActivity": True,
                "_anonymousSessionDay": False,
                **empty_usage,
            }
        )
    for activity in legacy.get("activities", []):
        rows.append(
            {
                "id": token_stats_request_id(
                    "claude-legacy-activity",
                    activity.get("sessionId"),
                    activity.get("date"),
                ),
                "timestamp": None,
                "timestampUtc": None,
                "date": activity.get("date"),
                "provider": "Claude Code",
                "model": "unknown",
                "source": "claude",
                "sessionId": activity.get("sessionId") or "",
                "totalTokens": 0,
                "costUsd": 0,
                "priceKnown": False,
                "_requestCount": 0,
                "_activityOnly": True,
                "_anonymousSessionDay": bool(activity.get("anonymous")),
                **empty_usage,
            }
        )
    for bucket in legacy.get("tokenBuckets", []):
        rows.append(
            {
                "id": token_stats_request_id(
                    "claude-legacy-token", bucket.get("date"), bucket.get("model")
                ),
                "timestamp": None,
                "timestampUtc": None,
                "date": bucket.get("date"),
                "provider": "Claude Code",
                "model": bucket.get("model") or "unknown",
                "source": "claude",
                "sessionId": "",
                "totalTokens": token_stats_nonnegative_int(bucket.get("tokens")) or 0,
                "costUsd": 0,
                "priceKnown": False,
                "_requestCount": 0,
                "_costUnknown": True,
                "_unattributedTokens": True,
                **empty_usage,
            }
        )
    return rows


def build_token_stats_details(
    start: date,
    end: date,
    source: str = "all",
    provider: str = "all",
    model: str = "all",
    limit: int = 500,
    force: bool = False,
    offset: int = 0,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> dict:
    if (start_time is None) != (end_time is None):
        raise ValueError("start_time and end_time must be provided together.")
    exact_range = start_time is not None
    if exact_range:
        start_time = token_stats_normalize_period_time(start_time, "start_time")
        end_time = token_stats_normalize_period_time(end_time, "end_time")
        if start_time >= end_time:
            raise ValueError("start_time must be before end_time.")
        start = start_time.date()
        end = end_time.date()
    if start > end:
        raise ValueError("start must be on or before end.")
    if (end - start).days + 1 > TOKEN_STATS_DETAILS_MAX_DAYS:
        raise ValueError(
            f"Date range cannot exceed {TOKEN_STATS_DETAILS_MAX_DAYS} inclusive days."
        )
    if source not in {
        "all",
        "codex",
        "claude",
        "deepseek",
        "workbuddy",
        "antigravity",
    }:
        raise ValueError(
            "source must be all, codex, claude, deepseek, workbuddy, or antigravity."
        )
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 5000:
        raise ValueError("limit must be an integer from 1 to 5000.")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("offset must be a non-negative integer.")
    provider = provider.strip() if isinstance(provider, str) else "all"
    model = model.strip() if isinstance(model, str) else "all"
    provider = provider or "all"
    model = model or "all"
    index = token_stats_details_index(force=force)
    legacy_rows = token_stats_claude_legacy_metric_rows(
        index.get("legacyAggregates", {}).get("claude", {})
    )
    legacy_dates = [
        row["date"] for row in legacy_rows if isinstance(row.get("date"), str)
    ]
    request_available_start = index.get("_requestAvailableStart")
    request_available_end = index.get("_requestAvailableEnd")
    if request_available_start is None or request_available_end is None:
        request_dates = [
            row["date"]
            for row in index.get("requests", [])
            if isinstance(row.get("date"), str)
        ]
        request_available_start = min(request_dates) if request_dates else None
        request_available_end = max(request_dates) if request_dates else None
    available_starts = [value for value in [request_available_start, *legacy_dates] if value]
    available_ends = [value for value in [request_available_end, *legacy_dates] if value]
    available_start = min(available_starts) if available_starts else None
    available_end = max(available_ends) if available_ends else None
    candidate_end = (
        (end_time - timedelta(microseconds=1)).date() if exact_range else end
    )
    request_period_rows = token_stats_index_period_rows(
        index, start, candidate_end, source
    )
    start_text = start.isoformat()
    candidate_end_text = candidate_end.isoformat()
    legacy_period_rows = [
        row
        for row in legacy_rows
        if row.get("date")
        and start_text <= row["date"] <= candidate_end_text
        and (source == "all" or row.get("source") == source)
    ]
    period_candidates = request_period_rows + legacy_period_rows
    if exact_range:
        start_utc = start_time.astimezone(timezone.utc)
        end_utc = end_time.astimezone(timezone.utc)
        period_metric_rows = []
        for row in period_candidates:
            parsed_timestamp = token_stats_row_timestamp(row)
            if parsed_timestamp is None:
                continue
            if start_utc <= parsed_timestamp < end_utc:
                period_metric_rows.append(row)
    else:
        period_metric_rows = period_candidates
    filter_options = {
        "sources": sorted({row["source"] for row in period_metric_rows}),
        "providers": sorted({row["provider"] for row in period_metric_rows}, key=str.casefold),
        "models": sorted({row["model"] for row in period_metric_rows}, key=str.casefold),
    }
    provider_filter = provider.casefold()
    model_filter = model.casefold()
    metric_rows = [
        row
        for row in period_metric_rows
        if (provider_filter == "all" or row["provider"].casefold() == provider_filter)
        and (model_filter == "all" or row["model"].casefold() == model_filter)
    ]
    excluded_untimestamped_rows = 0
    if exact_range:
        excluded_untimestamped_rows = sum(
            1
            for row in period_candidates
            if token_stats_row_timestamp(row) is None
            and (provider_filter == "all" or row["provider"].casefold() == provider_filter)
            and (model_filter == "all" or row["model"].casefold() == model_filter)
        )
    rows = [row for row in metric_rows if int(row.get("_requestCount", 1)) > 0]
    include_anonymous_session_days = start == end
    metrics = token_stats_request_metrics(
        metric_rows,
        include_anonymous_session_days=include_anonymous_session_days,
    )
    providers = token_stats_group_requests(
        metric_rows,
        "provider",
        include_anonymous_session_days=include_anonymous_session_days,
    )
    models = token_stats_group_models(
        metric_rows,
        include_anonymous_session_days=include_anonymous_session_days,
    )
    grain, trend = token_stats_request_trend(
        metric_rows,
        start,
        end,
        start_time=start_time,
        end_time=end_time,
    )
    request_page_count = max(1, (len(rows) + limit - 1) // limit)
    if not rows:
        offset = 0
    elif offset >= len(rows):
        offset = (request_page_count - 1) * limit
    request_rows = rows[offset : offset + limit]
    request_page = offset // limit + 1
    period = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "inclusive": True,
        "timezone": TOKEN_STATS_TIMEZONE,
        "source": source,
        "provider": provider,
        "model": model,
        "requestLimit": limit,
        "requestOffset": offset,
        "requestPage": request_page,
        "requestPageCount": request_page_count,
        "trendGrain": grain,
    }
    if exact_range:
        period.update(
            {
                "startTime": start_time.isoformat(),
                "endTime": end_time.isoformat(),
                "exact": True,
                "inclusive": False,
            }
        )
    result = {
        "ok": True,
        "generatedAt": index["generatedAt"],
        "timezone": TOKEN_STATS_TIMEZONE,
        "availableStart": available_start,
        "availableEnd": available_end,
        "period": period,
        "summary": metrics,
        "metrics": metrics,
        "trend": trend,
        "requests": request_rows,
        "requestLogs": request_rows,
        "requestTotal": len(rows),
        "requestReturned": len(request_rows),
        "requestOffset": offset,
        "requestLimit": limit,
        "requestPage": request_page,
        "requestPageCount": request_page_count,
        "requestTruncated": offset > 0 or offset + len(request_rows) < len(rows),
        "legacyFallback": {
            "activitySessions": metrics["activityOnlySessions"],
            "tokenBuckets": sum(bool(row.get("_unattributedTokens")) for row in metric_rows),
            "totalTokens": metrics["unattributedTokens"],
            "requestRowsAdded": 0,
            "transcriptAvailable": False,
        },
        "providers": providers,
        "providerStats": providers,
        "models": models,
        "modelStats": models,
        "filterOptions": filter_options,
        "coverage": index["coverage"],
    }
    if exact_range:
        result["exactRangeCoverage"] = {
            "excludedUntimestampedRows": excluded_untimestamped_rows,
            "precision": "timestamped-requests-only",
            "legacyAggregatesExcluded": excluded_untimestamped_rows > 0,
        }
    return result


def scan_codex_usage(path_value: str, tier: str = "standard") -> dict:
    project_path = normalize_project_path(path_value)
    thread_meta = codex_threads_for_project(project_path)
    session_index = load_codex_session_index()
    session_files = codex_session_files_by_thread()
    sessions = []

    for thread_id, meta in thread_meta.items():
        usage = final_token_usage_for_thread(thread_id, session_files.get(thread_id, []))
        if not usage:
            continue

        model = meta.get("model", "")
        cost = estimate_model_cost(model, tier, usage, usage_is_request=False)
        index_item = session_index.get(thread_id, {})
        sessions.append(
            {
                "threadId": thread_id,
                "threadName": index_item.get("thread_name") or meta.get("threadName") or thread_id,
                "updatedAt": index_item.get("updated_at") or meta.get("updatedAt") or "",
                "cwd": meta.get("cwd", ""),
                "model": model,
                "tier": tier,
                "usage": usage,
                "costUsd": cost["costUsd"],
                "price": cost["price"],
                "priceKnown": cost["priceKnown"],
                "sourceFile": usage.get("sourceFile", ""),
            }
        )

    sessions.sort(key=lambda item: item.get("updatedAt") or "", reverse=True)
    totals = {
        "inputTokens": sum(item["usage"].get("inputTokens", 0) for item in sessions),
        "cachedInputTokens": sum(item["usage"].get("cachedInputTokens", 0) for item in sessions),
        "outputTokens": sum(item["usage"].get("outputTokens", 0) for item in sessions),
        "reasoningOutputTokens": sum(item["usage"].get("reasoningOutputTokens", 0) for item in sessions),
        "totalTokens": sum(item["usage"].get("totalTokens", 0) for item in sessions),
        "costUsd": round(sum(item.get("costUsd", 0) for item in sessions), 6),
        "sessionCount": len(sessions),
    }

    return {
        "ok": True,
        "projectPath": str(project_path),
        "tier": tier,
        "sessions": sessions,
        "totals": totals,
        "prices": MODEL_PRICES_PER_1M,
        "priceSource": MODEL_PRICE_SOURCE,
        "codexHome": str(CODEX_HOME),
    }


def normalize_project_path(path_value: str) -> Path:
    value = (path_value or str(ROOT)).strip().strip('"')
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = (ROOT / path).resolve()
    else:
        path = path.resolve()
    if path.exists() and path.is_file():
        return path.parent
    return path


def codex_threads_for_project(project_path: Path) -> dict[str, dict]:
    if not CODEX_LOG_DB.exists():
        return {}

    rows: dict[str, dict] = {}
    try:
        con = sqlite3.connect(CODEX_LOG_DB)
        query = """
            select thread_id, ts, feedback_log_body
            from logs
            where feedback_log_body like '%run_sampling_request%'
              and thread_id is not null
            order by id asc
        """
        for thread_id, ts, body in con.execute(query):
            model = extract_log_field(body or "", "model")
            cwd = extract_log_cwd(body or "")
            if not cwd or not path_is_related(cwd, project_path):
                continue
            rows[thread_id] = {
                "model": model or rows.get(thread_id, {}).get("model", ""),
                "cwd": cwd,
                "updatedAt": iso_from_unix(ts),
            }
    except Exception:
        return {}
    finally:
        try:
            con.close()
        except Exception:
            pass

    return rows


def extract_log_field(body: str, key: str) -> str:
    match = re.search(rf"\b{re.escape(key)}=([^\s}}]+)", body)
    return match.group(1) if match else ""


def extract_log_cwd(body: str) -> str:
    match = re.search(r"\bcwd=([^}]+)", body)
    if not match:
        return ""
    return match.group(1).strip()


def path_is_related(cwd_value: str, project_path: Path) -> bool:
    try:
        cwd_path = Path(cwd_value).expanduser().resolve()
    except Exception:
        return False

    project_text = normalize_path_text(project_path)
    cwd_text = normalize_path_text(cwd_path)
    return (
        cwd_text == project_text
        or cwd_text.startswith(f"{project_text}\\")
    )


def normalize_path_text(path: Path) -> str:
    return str(path).replace("/", "\\").rstrip("\\").lower()


def load_codex_session_index() -> dict[str, dict]:
    if not CODEX_SESSION_INDEX.exists():
        return {}

    index = {}
    try:
        with CODEX_SESSION_INDEX.open("r", encoding="utf-8", errors="replace") as source:
            for line in source:
                try:
                    item = json.loads(line)
                except Exception:
                    continue
                if item.get("id"):
                    index[item["id"]] = item
    except Exception:
        return {}
    return index


def codex_session_files_by_thread() -> dict[str, list[Path]]:
    result: dict[str, list[Path]] = {}
    if not CODEX_SESSIONS_DIR.exists():
        return result

    for path in CODEX_SESSIONS_DIR.rglob("*.jsonl"):
        match = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$", path.name)
        if not match:
            continue
        result.setdefault(match.group(1), []).append(path)
    return result


def final_token_usage_for_thread(thread_id: str, session_files: list[Path]) -> dict:
    best_usage = {}
    best_timestamp = ""
    for path in session_files:
        try:
            with path.open("r", encoding="utf-8", errors="replace") as source:
                for line in source:
                    try:
                        item = json.loads(line)
                    except Exception:
                        continue
                    payload = item.get("payload") or {}
                    if payload.get("type") != "token_count":
                        continue
                    usage = payload.get("info", {}).get("total_token_usage") or {}
                    if not usage:
                        continue
                    timestamp = item.get("timestamp", "")
                    if timestamp >= best_timestamp:
                        best_timestamp = timestamp
                        best_usage = normalize_token_usage(usage)
                        best_usage["sourceFile"] = str(path)
        except Exception:
            continue
    return best_usage


def normalize_token_usage(usage: dict) -> dict:
    return {
        "inputTokens": int(usage.get("input_tokens") or 0),
        "cachedInputTokens": int(usage.get("cached_input_tokens") or 0),
        "outputTokens": int(usage.get("output_tokens") or 0),
        "reasoningOutputTokens": int(usage.get("reasoning_output_tokens") or 0),
        "totalTokens": int(usage.get("total_tokens") or 0),
    }


def estimate_model_cost(model: str, tier: str, usage: dict, *, usage_is_request: bool = True) -> dict:
    price = price_for_model(model, tier)
    # The legacy project endpoint has session totals, not request context sizes.
    # It cannot determine Astra's long-context tier or historical model changes.
    if model.lower() == "gpt-6-astra" and not usage_is_request:
        price = {}
    if not price:
        return {
            "costUsd": 0,
            "priceKnown": False,
            "price": {},
        }

    # Threshold applies to the full request input, including cached tokens.
    # Copy rates: never mutate the shared table or compound multipliers on refresh.
    if model.lower() == "gpt-6-astra" and int(usage.get("inputTokens", 0)) > 272_000:
        price = {
            key: value * (1.5 if key == "output" else 2)
            for key, value in price.items()
        }
    # Codex rollout logs do not report cache writes separately; do not invent them.
    input_tokens = max(int(usage.get("inputTokens", 0)) - int(usage.get("cachedInputTokens", 0)), 0)
    cached_tokens = int(usage.get("cachedInputTokens", 0))
    output_tokens = int(usage.get("outputTokens", 0))
    cost = (
        input_tokens * price["input"]
        + cached_tokens * price["cachedInput"]
        + output_tokens * price["output"]
    ) / 1_000_000
    return {
        "costUsd": round(cost, 6),
        "priceKnown": True,
        "price": price,
    }


def price_for_model(model: str, tier: str) -> dict:
    model_prices = MODEL_PRICES_PER_1M.get(model) or MODEL_PRICES_PER_1M.get(model.lower())
    if not model_prices:
        return {}
    normalized_tier = token_stats_normalize_service_tier(tier)
    if normalized_tier is None:
        return {}
    return model_prices.get(normalized_tier) or {}


def iso_from_unix(value: int) -> str:
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(value)))
    except Exception:
        return ""


class WorkbenchHandler(SimpleHTTPRequestHandler):
    # Explicitly publish only the browser application. Files elsewhere in ROOT
    # may contain local records, credentials, Git history, screenshots or backups.
    PUBLIC_STATIC_PATHS = frozenset({
        "index.html",
        "manifest.webmanifest",
        "src/app.js",
        "src/styles.css",
        "src/modules/accounting/accounting.js",
        "src/modules/chats/chats.js",
        "src/modules/health/health.js",
        "src/modules/papers/papers.js",
        "src/modules/papers/paper-trends.js",
        "src/modules/social/social.js",
        "src/modules/tasks/tasks.js",
        "src/modules/token-stats/activity-heatmap.js",
        "src/modules/token-stats/token-stats.css",
        "src/modules/token-stats/token-stats.js",
        "src/modules/tweets/tweets.js",
        "src/modules/videos/videos.js",
        "assets/icons/ai-workbench.ico",
        "assets/icons/ai-workbench-icon.png",
        "assets/icons/ai-workbench-32.png",
        "assets/icons/ai-workbench-180.png",
        "assets/icons/ai-workbench-192.png",
        "assets/icons/ai-workbench-512.png",
        "assets/icons/ai-workbench-taskbar.ico",
        "assets/icons/ai-workbench-taskbar.png",
        "assets/icons/ai-workbench-taskbar-32.png",
        "assets/icons/ai-workbench-taskbar-180.png",
        "assets/icons/ai-workbench-taskbar-192.png",
        "assets/icons/ai-workbench-taskbar-512.png",
        "assets/icons/ai-workbench-sunny-v1.ico",
        "assets/icons/ai-workbench-sunny-v1-32.png",
        "assets/icons/ai-workbench-sunny-v1-180.png",
        "assets/icons/ai-workbench-sunny-v1-192.png",
        "assets/icons/ai-workbench-sunny-v1-512.png",
        "assets/icons/ai-workbench-soft-v2.ico",
        "assets/icons/ai-workbench-soft-v2-32.png",
        "assets/icons/ai-workbench-soft-v2-180.png",
        "assets/icons/ai-workbench-soft-v2-192.png",
        "assets/icons/ai-workbench-soft-v2-512.png",
    })

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def parse_request(self):
        if not super().parse_request():
            return False
        # Apply the local-browser boundary before every method and API route,
        # including HEAD, so a rebinding Host cannot read local personal data.
        fetch_sites = self.headers.get_all("Sec-Fetch-Site") or []
        if (
            not self.is_trusted_tweet_clipboard_request()
            or len(fetch_sites) > 1
            or (fetch_sites and fetch_sites[0].strip().lower() == "cross-site")
        ):
            self.send_error(403, "Forbidden")
            return False
        return True

    def public_static_path(self) -> Path | None:
        try:
            if any(ord(char) < 32 or ord(char) == 127 for char in self.path):
                return None
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.scheme or parsed.netloc or parsed.fragment:
                return None
            decoded = urllib.parse.unquote(parsed.path, encoding="utf-8", errors="strict")
            relative = "index.html" if decoded == "/" else decoded.removeprefix("/")
            if not decoded.startswith("/") or relative not in self.PUBLIC_STATIC_PATHS:
                return None
            root = ROOT.resolve(strict=True)
            candidate = root
            for component in relative.split("/"):
                candidate = candidate / component
                stat = candidate.lstat()
                # Reject junctions and other Windows reparse points as well as
                # symlinks, even when their target remains inside the project.
                if candidate.is_symlink() or getattr(stat, "st_file_attributes", 0) & 0x400:
                    return None
            candidate.resolve(strict=True).relative_to(root)
            return candidate if candidate.is_file() else None
        except (OSError, RuntimeError, UnicodeError, ValueError):
            return None

    def send_head(self):
        # SimpleHTTPRequestHandler serves HEAD through this same method. Check
        # both methods before its filesystem fallback or directory-listing logic.
        if self.public_static_path() is None:
            self.send_error(404, "Not found")
            return None
        return super().send_head()

    def end_headers(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in {"", "/"} or parsed.path.endswith((".html", ".js", ".css")):
            self.send_header("Cache-Control", "no-store, max-age=0")
        super().end_headers()

    def is_trusted_tweet_clipboard_request(self) -> bool:
        port = int(self.server.server_address[1])
        allowed_hosts = {
            f"127.0.0.1:{port}",
            f"localhost:{port}",
        }
        host_values = self.headers.get_all("Host") or []
        if len(host_values) != 1 or host_values[0].strip().lower() not in allowed_hosts:
            return False

        origin_values = self.headers.get_all("Origin") or []
        if not origin_values:
            return True
        if len(origin_values) != 1:
            return False
        allowed_origins = {f"http://{host}" for host in allowed_hosts}
        return origin_values[0].strip().lower() in allowed_origins

    def is_trusted_paper_trends_request(self) -> bool:
        """Require browser same-origin evidence for the network-expensive fetch."""
        port = int(self.server.server_address[1])
        allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        host_values = self.headers.get_all("Host") or []
        if len(host_values) != 1 or host_values[0].strip().lower() not in allowed_hosts:
            return False

        allowed_origins = {f"http://{host}" for host in allowed_hosts}
        origin_values = self.headers.get_all("Origin") or []
        if origin_values:
            return len(origin_values) == 1 and origin_values[0].strip().lower() in allowed_origins

        fetch_site_values = self.headers.get_all("Sec-Fetch-Site") or []
        if len(fetch_site_values) == 1 and fetch_site_values[0].strip().lower() == "same-origin":
            return True

        referer_values = self.headers.get_all("Referer") or []
        if len(referer_values) != 1:
            return False
        referer = urllib.parse.urlparse(referer_values[0].strip())
        return f"{referer.scheme}://{referer.netloc}".lower() in allowed_origins

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/accounting-storage":
            if not self.is_trusted_tweet_clipboard_request():
                self.send_json({"ok": False, "error": "Forbidden"}, status=403)
                return
            try:
                self.send_json(read_accounting_storage_state())
            except AccountingStorageReadError as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=500)
            return

        if parsed.path == "/api/accounting-auth-candidates":
            if not self.is_trusted_tweet_clipboard_request():
                self.send_json({"ok": False, "error": "Forbidden"}, status=403)
                return
            try:
                self.send_json(public_accounting_cpa_auth_candidates())
            except AccountingStorageReadError as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=500)
            except OSError:
                self.send_json({"ok": False, "error": "accounting_auth_candidates_read_failed"}, status=500)
            return

        if parsed.path.startswith("/api/accounting-auth/"):
            if not self.is_trusted_tweet_clipboard_request():
                self.send_json({"ok": False, "error": "Forbidden"}, status=403)
                return
            digest = parsed.path.removeprefix("/api/accounting-auth/")
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                self.send_json({"error": "Not found"}, status=404)
                return
            path = ACCOUNTING_AUTH_DIR / f"{digest}.json.dpapi"
            if not path.is_file():
                self.send_json({"error": "Not found"}, status=404)
                return
            try:
                body = accounting_unprotect_bytes(path.read_bytes())
            except Exception:
                self.send_json({"ok": False, "error": "accounting_auth_read_failed"}, status=500)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="auth-{digest[:8]}.json"')
            self.send_header("Cache-Control", "private, no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if parsed.path.startswith("/api/accounting-credential/"):
            if not self.is_trusted_tweet_clipboard_request():
                self.send_json({"ok": False, "error": "Forbidden"}, status=403)
                return
            record_id = urllib.parse.unquote(parsed.path.removeprefix("/api/accounting-credential/"))
            try:
                credential = read_accounting_secret(record_id)
            except (OSError, UnicodeDecodeError, ValueError):
                self.send_json({"ok": False, "error": "accounting_credential_read_failed"}, status=400)
                return
            self.send_json({"ok": True, "credential": credential})
            return

        if parsed.path.startswith("/api/accounting-storage/") or parsed.path == "/api/accounting-auth":
            self.send_json({"error": "Not found"}, status=404)
            return

        if parsed.path == "/api/social-storage":
            if not self.is_trusted_tweet_clipboard_request():
                self.send_json({"ok": False, "error": "Forbidden"}, status=403)
                return
            try:
                self.send_json(read_social_storage_state())
            except SocialStorageReadError as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=500)
            return

        if parsed.path.startswith("/api/social-media/"):
            if not self.is_trusted_tweet_clipboard_request():
                self.send_json({"ok": False, "error": "Forbidden"}, status=403)
                return
            digest = parsed.path.removeprefix("/api/social-media/")
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                self.send_json({"error": "Not found"}, status=404)
                return
            matches = list(SOCIAL_MEDIA_DIR.glob(f"{digest}.*")) if SOCIAL_MEDIA_DIR.exists() else []
            if not matches or not matches[0].is_file():
                self.send_json({"error": "Not found"}, status=404)
                return
            path = sorted(matches)[0]
            size = path.stat().st_size
            range_value = self.headers.get("Range")
            try:
                start, end = parse_social_media_range(range_value, size) if range_value else (0, size - 1)
            except (TypeError, ValueError):
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            response_length = max(0, end - start + 1)
            self.send_response(206 if range_value else 200)
            self.send_header("Content-Type", social_media_type_for_path(path))
            self.send_header("Cache-Control", "private, max-age=86400")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(response_length))
            if range_value:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            with path.open("rb") as source:
                source.seek(start)
                remaining = response_length
                while remaining:
                    chunk = source.read(min(SOCIAL_MEDIA_CHUNK_BYTES, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
            return

        if parsed.path.startswith("/api/social-storage/"):
            self.send_json({"error": "Not found"}, status=404)
            return

        if parsed.path == "/api/tweet-bookmarks/import" or parsed.path.startswith(
            "/api/tweet-bookmarks/import/"
        ):
            if not self.is_trusted_tweet_clipboard_request():
                self.send_json({"ok": False, "error": "Forbidden"}, status=403)
                return
            if parsed.path == "/api/tweet-bookmarks/import":
                self.send_method_not_allowed("POST")
                return
            self.serve_tweet_bookmark_import_status(parsed.path)
            return

        if parsed.path == "/api/tweet-clipboard":
            if not self.is_trusted_tweet_clipboard_request():
                self.send_json({"ok": False, "error": "Forbidden"}, status=403)
                return
            self.send_json(read_tweet_clipboard())
            return

        if parsed.path.startswith("/api/tweet-clipboard/"):
            self.send_json({"error": "Not found"}, status=404)
            return

        if parsed.path == "/api/token-stats/summary":
            self.serve_token_stats_summary(parsed.query)
            return

        if parsed.path == "/api/token-stats/details":
            self.serve_token_stats_details(parsed.query)
            return

        if parsed.path == "/api/token-stats" or parsed.path.startswith("/api/token-stats/"):
            self.send_json({"error": "Not found"}, status=404)
            return

        if parsed.path == "/api/chat-history/summary":
            self.serve_chat_history_summary(parsed.query)
            return

        if parsed.path.startswith("/api/chat-history/session/"):
            self.serve_chat_history_session(parsed.path)
            return

        if parsed.path == "/api/chat-history/open-terminal":
            self.send_method_not_allowed("POST")
            return

        if parsed.path == "/api/chat-history" or parsed.path.startswith("/api/chat-history/"):
            self.send_json({"error": "Not found"}, status=404)
            return

        if parsed.path == "/api/papers":
            self.send_json({"vaults": load_vaults(), "papers": scan_papers()})
            return

        if parsed.path == "/api/paper-trends":
            if not self.is_trusted_paper_trends_request():
                self.send_json({"ok": False, "error": "Forbidden"}, status=403)
                return
            params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
            refresh = params.get("refresh", [""])[0] == "1"
            try:
                payload = fetch_paper_trends(
                    force=refresh,
                    topic=params.get("topic", [""])[0],
                    query=params.get("query", [""])[0],
                )
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=400)
                return
            self.send_json(public_paper_trends_payload(payload), status=200 if payload.get("ok") else 503)
            return

        if parsed.path == "/api/paper-trend-image":
            if not self.is_trusted_paper_trends_request():
                self.send_json({"ok": False, "error": "Forbidden"}, status=403)
                return
            params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
            if set(params) != {"id"} or len(params["id"]) != 1:
                self.send_json({"error": "Not found"}, status=404)
                return
            try:
                body, content_type = fetch_paper_trend_image(params["id"][0])
            except PaperTrendImageNotFound:
                self.send_json({"error": "Not found"}, status=404)
                return
            except PaperTrendImageFetchError:
                self.send_json({"error": "Image unavailable"}, status=502)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "private, max-age=3600")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'; sandbox")
            self.end_headers()
            self.wfile.write(body)
            return

        if parsed.path == "/api/paper-trend-likes":
            if not self.is_trusted_paper_trends_request():
                self.send_json({"ok": False, "error": "Forbidden"}, status=403)
                return
            try:
                payload = read_paper_trend_likes_state()
            except PaperTrendLikesReadError as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=500)
                return
            payload["topics"] = paper_trend_topics_payload()
            self.send_json(payload)
            return

        if parsed.path.startswith("/api/paper-trends/") or parsed.path.startswith("/api/paper-trend-likes/") or parsed.path.startswith("/api/paper-trend-image/"):
            self.send_json({"error": "Not found"}, status=404)
            return

        if parsed.path == "/api/pdf":
            self.serve_pdf(parsed.query)
            return

        if parsed.path == "/api/thumbnail":
            self.serve_thumbnail(parsed.query)
            return

        if parsed.path == "/api/video-meta":
            self.serve_video_meta(parsed.query)
            return

        if parsed.path == "/api/health-storage":
            if not self.is_trusted_tweet_clipboard_request():
                self.send_json({"ok": False, "error": "Forbidden"}, status=403)
                return
            try:
                self.send_json(read_health_storage_state())
            except HealthStorageReadError as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=500)
            return

        if parsed.path.startswith("/api/health-storage/"):
            self.send_json({"error": "Not found"}, status=404)
            return

        if parsed.path == "/api/video-storage":
            if not self.is_trusted_tweet_clipboard_request():
                self.send_json({"ok": False, "error": "Forbidden"}, status=403)
                return
            try:
                self.send_json(read_video_storage_state())
            except VideoStorageReadError as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=500)
            return

        if parsed.path.startswith("/api/video-storage/"):
            self.send_json({"error": "Not found"}, status=404)
            return

        if parsed.path == "/api/youtube-playlist/import":
            self.send_method_not_allowed("POST")
            return

        if parsed.path.startswith("/api/youtube-playlist/"):
            self.send_json({"error": "Not found"}, status=404)
            return

        if parsed.path == "/api/tweet-meta":
            self.serve_tweet_meta(parsed.query)
            return

        if parsed.path == "/api/video-image":
            self.serve_video_image(parsed.query)
            return

        if parsed.path == "/api/codex-usage":
            self.serve_codex_usage(parsed.query)
            return

        super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/accounting-auth-candidates/import":
            if not self.is_trusted_tweet_clipboard_request():
                self.send_json({"ok": False, "error": "Forbidden"}, status=403)
                return
            try:
                payload = self.read_accounting_storage_json_body()
                self.send_json(import_accounting_cpa_auth_candidate(payload.get("candidateId")))
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=400)
            except AccountingStorageReadError as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=500)
            except OSError:
                self.send_json({"ok": False, "error": "accounting_auth_write_failed"}, status=500)
            return

        if parsed.path == "/api/accounting-auth":
            if not self.is_trusted_tweet_clipboard_request():
                self.send_json({"ok": False, "error": "Forbidden"}, status=403)
                return
            try:
                if self.headers.get("Transfer-Encoding"):
                    raise ValueError("Transfer-Encoding is not supported.")
                content_type = self.headers.get_content_type()
                if content_type not in {"application/json", "text/json", "application/octet-stream"}:
                    raise ValueError("invalid_accounting_auth_type")
                length = int(self.headers.get("Content-Length", "-1"))
                if length < 0:
                    raise ValueError("Content-Length is required.")
                if length > ACCOUNTING_AUTH_MAX_BYTES:
                    self.close_connection = True
                    raise ValueError("accounting_auth_too_large")
                self.send_json(store_accounting_auth_stream(self.rfile, length))
            except (TypeError, ValueError) as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=400)
            except OSError:
                self.send_json({"ok": False, "error": "accounting_auth_write_failed"}, status=500)
            return

        if parsed.path == "/api/accounting-credential":
            if not self.is_trusted_tweet_clipboard_request():
                self.send_json({"ok": False, "error": "Forbidden"}, status=403)
                return
            try:
                payload = self.read_accounting_storage_json_body()
                write_accounting_secret(payload.get("id"), payload.get("credential"))
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=400)
                return
            except OSError:
                self.send_json({"ok": False, "error": "accounting_credential_write_failed"}, status=500)
                return
            self.send_json({"ok": True})
            return

        if parsed.path == "/api/accounting-storage":
            self.send_method_not_allowed("PUT")
            return

        if parsed.path.startswith("/api/accounting-storage/") or parsed.path.startswith("/api/accounting-auth/"):
            self.send_json({"error": "Not found"}, status=404)
            return

        if parsed.path == "/api/social-media":
            if not self.is_trusted_tweet_clipboard_request():
                self.send_json({"ok": False, "error": "Forbidden"}, status=403)
                return
            try:
                content_type = self.headers.get_content_type()
                if self.headers.get("Transfer-Encoding"):
                    raise ValueError("Transfer-Encoding is not supported.")
                length = int(self.headers.get("Content-Length", "-1"))
                if length < 0:
                    raise ValueError("Content-Length is required.")
                if length > SOCIAL_MEDIA_MAX_BYTES:
                    self.close_connection = True
                    raise ValueError("social_media_too_large")
                self.send_json(store_social_media_stream(content_type, self.rfile, length))
            except (TypeError, ValueError) as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=400)
            except OSError:
                self.send_json({"ok": False, "error": "social_media_write_failed"}, status=500)
            return

        if parsed.path == "/api/social-storage":
            self.send_method_not_allowed("PUT")
            return

        if parsed.path.startswith("/api/social-storage/") or parsed.path.startswith("/api/social-media/"):
            self.send_json({"error": "Not found"}, status=404)
            return

        if parsed.path == "/api/paper-trends":
            self.send_method_not_allowed("GET")
            return

        if parsed.path == "/api/paper-trend-image":
            self.send_method_not_allowed("GET")
            return

        if parsed.path == "/api/paper-trend-likes":
            self.send_method_not_allowed("PUT")
            return

        if parsed.path.startswith("/api/paper-trends/") or parsed.path.startswith("/api/paper-trend-likes/"):
            self.send_json({"error": "Not found"}, status=404)
            return

        if parsed.path == "/api/health-storage":
            self.send_method_not_allowed("PUT")
            return

        if parsed.path.startswith("/api/health-storage/"):
            self.send_json({"error": "Not found"}, status=404)
            return

        if parsed.path == "/api/video-storage":
            if not self.is_trusted_tweet_clipboard_request():
                self.send_json({"ok": False, "error": "Forbidden"}, status=403)
                return
            self.serve_video_storage_write()
            return

        if parsed.path.startswith("/api/video-storage/"):
            self.send_json({"error": "Not found"}, status=404)
            return

        if parsed.path == "/api/youtube-playlist/import":
            if not self.is_trusted_tweet_clipboard_request():
                self.send_json({"ok": False, "error": "Forbidden"}, status=403)
                return
            self.serve_youtube_playlist_import()
            return

        if parsed.path.startswith("/api/youtube-playlist/"):
            self.send_json({"error": "Not found"}, status=404)
            return

        if parsed.path == "/api/tweet-bookmarks/import":
            if not self.is_trusted_tweet_clipboard_request():
                self.send_json({"ok": False, "error": "Forbidden"}, status=403)
                return
            self.serve_tweet_bookmark_import_start()
            return

        if parsed.path.startswith("/api/tweet-bookmarks/import/"):
            if not self.is_trusted_tweet_clipboard_request():
                self.send_json({"ok": False, "error": "Forbidden"}, status=403)
                return
            match = re.fullmatch(
                r"/api/tweet-bookmarks/import/([0-9a-f]{32})",
                parsed.path,
            )
            if match and tweet_bookmark_import_job_snapshot(match.group(1)):
                self.send_method_not_allowed("GET")
            else:
                self.send_json({"error": "Not found"}, status=404)
            return

        if parsed.path == "/api/tweet-clipboard":
            self.send_method_not_allowed("GET")
            return

        if parsed.path.startswith("/api/tweet-clipboard/"):
            self.send_json({"error": "Not found"}, status=404)
            return

        if parsed.path == "/api/token-stats/summary":
            self.send_method_not_allowed("GET")
            return

        if parsed.path == "/api/token-stats/details":
            self.send_method_not_allowed("GET")
            return

        if parsed.path == "/api/token-stats" or parsed.path.startswith("/api/token-stats/"):
            self.send_json({"error": "Not found"}, status=404)
            return

        if parsed.path == "/api/chat-history/open-terminal":
            self.serve_chat_history_open_terminal()
            return

        if parsed.path == "/api/chat-history/summary" or parsed.path.startswith(
            "/api/chat-history/session/"
        ):
            self.send_method_not_allowed("GET")
            return

        if parsed.path == "/api/chat-history" or parsed.path.startswith("/api/chat-history/"):
            self.send_json({"error": "Not found"}, status=404)
            return

        self.send_error(501, f"Unsupported method ({self.command!r})")

    def do_PUT(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/paper-trends":
            self.send_method_not_allowed("GET")
            return

        if parsed.path == "/api/paper-trend-image":
            self.send_method_not_allowed("GET")
            return

        if parsed.path == "/api/paper-trend-likes":
            if not self.is_trusted_paper_trends_request():
                self.send_json({"ok": False, "error": "Forbidden"}, status=403)
                return
            try:
                payload = self.read_paper_trend_likes_json_body()
                state = write_paper_trend_like_intent(
                    payload.get("item"), payload.get("liked"), payload.get("baseRevision")
                )
            except PaperTrendLikesRevisionConflict as exc:
                self.send_json({"ok": False, "error": "revision_conflict", "state": exc.state}, status=409)
                return
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=400)
                return
            except PaperTrendLikesReadError as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=500)
                return
            except OSError:
                self.send_json({"ok": False, "error": "paper_trend_likes_write_failed"}, status=500)
                return
            self.send_json(state)
            return

        if parsed.path.startswith("/api/paper-trend-likes/"):
            self.send_json({"error": "Not found"}, status=404)
            return
        if parsed.path == "/api/accounting-storage":
            if not self.is_trusted_tweet_clipboard_request():
                self.send_json({"ok": False, "error": "Forbidden"}, status=403)
                return
            self.serve_accounting_storage_write()
            return

        if parsed.path.startswith("/api/accounting-storage/") or parsed.path.startswith("/api/accounting-auth"):
            self.send_json({"error": "Not found"}, status=404)
            return

        if parsed.path == "/api/social-storage":
            if not self.is_trusted_tweet_clipboard_request():
                self.send_json({"ok": False, "error": "Forbidden"}, status=403)
                return
            self.serve_social_storage_write()
            return

        if parsed.path.startswith("/api/social-storage/") or parsed.path.startswith("/api/social-media"):
            self.send_json({"error": "Not found"}, status=404)
            return

        if parsed.path == "/api/health-storage":
            if not self.is_trusted_tweet_clipboard_request():
                self.send_json({"ok": False, "error": "Forbidden"}, status=403)
                return
            self.serve_health_storage_write()
            return

        if parsed.path.startswith("/api/health-storage/"):
            self.send_json({"error": "Not found"}, status=404)
            return

        self.send_error(501, f"Unsupported method ({self.command!r})")

    def send_json(self, payload: dict, status: int = 200, headers: dict | None = None):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_method_not_allowed(self, allowed: str):
        self.send_json(
            {"error": "Method not allowed"},
            status=405,
            headers={"Allow": allowed},
        )

    def serve_tweet_bookmark_import_start(self):
        try:
            payload = self.read_chat_history_json_body()
            url, max_items = validate_tweet_bookmark_import_payload(payload)
        except ValueError as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=400)
            return

        created, active = create_tweet_bookmark_import_job(url, max_items)
        if active:
            self.send_json(
                {
                    "ok": False,
                    "error": "A bookmark import is already active.",
                    "jobId": active["jobId"],
                    "requestUrl": active["requestUrl"],
                    "maxItems": active["maxItems"],
                    "status": active["status"],
                },
                status=409,
            )
            return
        self.send_json(
            {
                "ok": True,
                "jobId": created["jobId"],
                "requestUrl": created["requestUrl"],
                "maxItems": created["maxItems"],
                "status": created["status"],
            },
            status=202,
        )

    def serve_tweet_bookmark_import_status(self, request_path: str):
        match = re.fullmatch(
            r"/api/tweet-bookmarks/import/([0-9a-f]{32})",
            request_path,
        )
        if not match:
            self.send_json({"error": "Not found"}, status=404)
            return
        snapshot = tweet_bookmark_import_job_snapshot(match.group(1))
        if not snapshot:
            self.send_json({"error": "Not found"}, status=404)
            return
        self.send_json(snapshot)

    def serve_token_stats_summary(self, query: str):
        refresh = urllib.parse.parse_qs(query).get("refresh", [""])[0] == "1"
        try:
            summary = build_token_stats(force=refresh)
        except Exception as exc:
            self.send_json(
                {"ok": False, "error": str(exc) or "Failed to build token statistics"},
                status=500,
            )
            return
        self.send_json(summary)

    def serve_token_stats_details(self, query: str):
        params = urllib.parse.parse_qs(query, keep_blank_values=True)
        today = datetime.now(TOKEN_STATS_TZ).date()
        start_time_value = params.get("start_time", [None])[0]
        end_time_value = params.get("end_time", [None])[0]
        has_exact_range = start_time_value is not None or end_time_value is not None
        start_value = params.get("start", [today.isoformat()])[0]
        end_value = params.get("end", [start_value])[0]
        source = params.get("source", ["all"])[0].strip().lower()
        provider = params.get("provider", ["all"])[0]
        model = params.get("model", ["all"])[0]
        limit_value = params.get("limit", ["500"])[0]
        offset_value = params.get("offset", [""])[0]
        page_value = params.get("page", [""])[0]
        refresh = params.get("refresh", [""])[0] == "1"
        try:
            if has_exact_range:
                if start_time_value is None or end_time_value is None:
                    raise ValueError("start_time and end_time must be provided together.")
                start_time = token_stats_parse_period_time(start_time_value, "start_time")
                end_time = token_stats_parse_period_time(end_time_value, "end_time")
                start = start_time.date()
                end = end_time.date()
            else:
                start_time = None
                end_time = None
                start = token_stats_parse_period_date(start_value, "start")
                end = token_stats_parse_period_date(end_value, "end")
            try:
                limit = int(limit_value)
            except (TypeError, ValueError) as exc:
                raise ValueError("limit must be an integer from 1 to 5000.") from exc
            if offset_value != "":
                try:
                    offset = int(offset_value)
                except (TypeError, ValueError) as exc:
                    raise ValueError("offset must be a non-negative integer.") from exc
            elif page_value != "":
                try:
                    page = int(page_value)
                except (TypeError, ValueError) as exc:
                    raise ValueError("page must be a positive integer.") from exc
                if page < 1:
                    raise ValueError("page must be a positive integer.")
                offset = (page - 1) * limit
            else:
                offset = 0
            details = build_token_stats_details(
                start=start,
                end=end,
                source=source,
                provider=provider,
                model=model,
                limit=limit,
                offset=offset,
                force=refresh,
                start_time=start_time,
                end_time=end_time,
            )
        except ValueError as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=400)
            return
        except Exception as exc:
            self.send_json(
                {"ok": False, "error": str(exc) or "Failed to build token details"},
                status=500,
            )
            return
        self.send_json(details)

    def serve_chat_history_summary(self, query: str):
        refresh = urllib.parse.parse_qs(query).get("refresh", [""])[0] == "1"
        try:
            summary = build_chat_history_index(force=refresh)
        except Exception as exc:
            self.send_json({"error": str(exc) or "Failed to scan chat history"}, status=500)
            return
        self.send_json(summary)

    def serve_chat_history_session(self, request_path: str):
        prefix = "/api/chat-history/session/"
        key = urllib.parse.unquote(request_path[len(prefix) :])
        if not re.fullmatch(r"[0-9a-f]{64}", key):
            self.send_json({"error": "Not found"}, status=404)
            return

        try:
            build_chat_history_index()
        except Exception as exc:
            self.send_json({"error": str(exc) or "Failed to scan chat history"}, status=500)
            return
        session = CHAT_HISTORY_SESSIONS_BY_KEY.get(key)
        if not session:
            self.send_json({"error": "Not found"}, status=404)
            return
        try:
            messages = chat_history_transcript(session)
        except Exception as exc:
            self.send_json({"error": str(exc) or "Failed to read chat history"}, status=500)
            return
        self.send_json({"session": session, "messages": messages})

    def read_chat_history_json_body(self) -> dict:
        if self.headers.get_content_type() != "application/json":
            raise ValueError("Content-Type must be application/json.")
        if self.headers.get("Transfer-Encoding"):
            raise ValueError("Transfer-Encoding is not supported.")

        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length) if raw_length is not None else -1
        except (TypeError, ValueError) as exc:
            raise ValueError("Content-Length is invalid.") from exc
        if length < 0:
            raise ValueError("Content-Length is required.")
        if length > CHAT_HISTORY_BODY_LIMIT:
            self.close_connection = True
            raise ValueError("Request body exceeds 64KB.")

        raw = self.rfile.read(length)
        if len(raw) != length:
            raise ValueError("Request body is incomplete.")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Request body must be valid UTF-8 JSON.") from exc
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object.")
        return payload

    def read_paper_trend_likes_json_body(self) -> dict:
        if self.headers.get_content_type() != "application/json":
            raise ValueError("Content-Type must be application/json.")
        if self.headers.get("Transfer-Encoding"):
            raise ValueError("Transfer-Encoding is not supported.")
        try:
            length = int(self.headers.get("Content-Length", "-1"))
        except (TypeError, ValueError) as exc:
            raise ValueError("Content-Length is invalid.") from exc
        if length < 0:
            raise ValueError("Content-Length is required.")
        if length > PAPER_TREND_LIKES_MAX_BODY:
            self.close_connection = True
            raise ValueError("paper_trend_likes_request_too_large")
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise ValueError("Request body is incomplete.")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Request body must be valid UTF-8 JSON.") from exc
        if not isinstance(payload, dict) or set(payload) != {"item", "liked", "baseRevision"}:
            raise ValueError("invalid_like_intent_payload")
        return payload

    def read_video_storage_json_body(self) -> dict:
        if self.headers.get_content_type() != "application/json":
            raise ValueError("Content-Type must be application/json.")
        if self.headers.get("Transfer-Encoding"):
            raise ValueError("Transfer-Encoding is not supported.")
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length) if raw_length is not None else -1
        except (TypeError, ValueError) as exc:
            raise ValueError("Content-Length is invalid.") from exc
        if length < 0:
            raise ValueError("Content-Length is required.")
        if length > VIDEO_STORAGE_MAX_BODY_BYTES:
            self.close_connection = True
            raise ValueError("video_storage_too_large")
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise ValueError("Request body is incomplete.")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Request body must be valid UTF-8 JSON.") from exc
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object.")
        return payload

    def read_health_storage_json_body(self) -> dict:
        if self.headers.get_content_type() != "application/json":
            raise ValueError("Content-Type must be application/json.")
        if self.headers.get("Transfer-Encoding"):
            raise ValueError("Transfer-Encoding is not supported.")
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length) if raw_length is not None else -1
        except (TypeError, ValueError) as exc:
            raise ValueError("Content-Length is invalid.") from exc
        if length < 0:
            raise ValueError("Content-Length is required.")
        if length > HEALTH_STORAGE_MAX_BODY_BYTES:
            self.close_connection = True
            raise ValueError("health_storage_too_large")
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise ValueError("Request body is incomplete.")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Request body must be valid UTF-8 JSON.") from exc
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object.")
        return payload

    def read_social_storage_json_body(self) -> dict:
        if self.headers.get_content_type() != "application/json":
            raise ValueError("Content-Type must be application/json.")
        if self.headers.get("Transfer-Encoding"):
            raise ValueError("Transfer-Encoding is not supported.")
        try:
            length = int(self.headers.get("Content-Length", "-1"))
        except (TypeError, ValueError) as exc:
            raise ValueError("Content-Length is invalid.") from exc
        if length < 0:
            raise ValueError("Content-Length is required.")
        if length > SOCIAL_STORAGE_MAX_BODY_BYTES:
            self.close_connection = True
            raise ValueError("social_storage_too_large")
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise ValueError("Request body is incomplete.")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Request body must be valid UTF-8 JSON.") from exc
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object.")
        return payload

    def read_accounting_storage_json_body(self) -> dict:
        if self.headers.get_content_type() != "application/json":
            raise ValueError("Content-Type must be application/json.")
        if self.headers.get("Transfer-Encoding"):
            raise ValueError("Transfer-Encoding is not supported.")
        try:
            length = int(self.headers.get("Content-Length", "-1"))
        except (TypeError, ValueError) as exc:
            raise ValueError("Content-Length is invalid.") from exc
        if length < 0:
            raise ValueError("Content-Length is required.")
        if length > ACCOUNTING_STORAGE_MAX_BODY_BYTES:
            self.close_connection = True
            raise ValueError("accounting_storage_too_large")
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise ValueError("Request body is incomplete.")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Request body must be valid UTF-8 JSON.") from exc
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object.")
        return payload

    def serve_chat_history_open_terminal(self):
        try:
            payload = self.read_chat_history_json_body()
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=400)
            return

        mode = payload.get("mode", "terminal")
        if not isinstance(mode, str) or mode not in CHAT_HISTORY_TERMINAL_MODES:
            self.send_json({"error": "Unsupported terminal mode."}, status=400)
            return

        try:
            build_chat_history_index()
        except Exception as exc:
            self.send_json({"error": str(exc) or "Failed to scan chat history"}, status=500)
            return

        key = payload.get("key")
        try:
            if key is not None and key != "":
                if not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{64}", key):
                    self.send_json({"error": "Not found"}, status=404)
                    return
                session = CHAT_HISTORY_SESSIONS_BY_KEY.get(key)
                if not session:
                    self.send_json({"error": "Not found"}, status=404)
                    return
                cwd = chat_history_existing_directory(session.get("cwd"))
            else:
                requested_cwd = payload.get("cwd")
                if not isinstance(requested_cwd, str) or not requested_cwd.strip():
                    raise ValueError("A valid session key or indexed folder is required.")
                cwd = chat_history_existing_directory(requested_cwd)
                if not chat_history_is_indexed_folder_or_ancestor(cwd):
                    raise ValueError("This folder is not in the indexed chat history.")

            chat_history_open_terminal(cwd, mode)
        except (OSError, RuntimeError, ValueError) as exc:
            self.send_json({"error": str(exc)}, status=400)
            return
        self.send_json({"ok": True})

    def serve_video_meta(self, query: str):
        url = urllib.parse.parse_qs(query).get("url", [""])[0].strip()
        if not url:
            self.send_json({"ok": False, "error": "missing_url"})
            return

        self.send_json(fetch_video_meta(url))

    def serve_video_storage_write(self):
        try:
            payload = self.read_video_storage_json_body()
            state = write_video_storage_state(payload.get("videos"), payload.get("baseRevision"))
        except VideoStorageRevisionConflict as exc:
            self.send_json(
                {"ok": False, "error": "revision_conflict", "state": exc.state},
                status=409,
            )
            return
        except ValueError as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=400)
            return
        except VideoStorageReadError as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=500)
            return
        except OSError:
            self.send_json({"ok": False, "error": "video_storage_write_failed"}, status=500)
            return
        self.send_json(state)

    def serve_health_storage_write(self):
        try:
            payload = self.read_health_storage_json_body()
            state = write_health_storage_state(
                payload.get("profile"),
                payload.get("entries"),
                payload.get("baseRevision"),
            )
        except HealthStorageRevisionConflict as exc:
            self.send_json(
                {"ok": False, "error": "revision_conflict", "state": exc.state},
                status=409,
            )
            return
        except ValueError as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=400)
            return
        except HealthStorageReadError as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=500)
            return
        except OSError:
            self.send_json({"ok": False, "error": "health_storage_write_failed"}, status=500)
            return
        self.send_json(state)

    def serve_social_storage_write(self):
        try:
            payload = self.read_social_storage_json_body()
            state = write_social_storage_state(payload.get("entries"), payload.get("baseRevision"))
        except SocialStorageRevisionConflict as exc:
            self.send_json({"ok": False, "error": "revision_conflict", "state": exc.state}, status=409)
            return
        except ValueError as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=400)
            return
        except SocialStorageReadError as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=500)
            return
        except OSError:
            self.send_json({"ok": False, "error": "social_storage_write_failed"}, status=500)
            return
        self.send_json(state)

    def serve_accounting_storage_write(self):
        try:
            payload = self.read_accounting_storage_json_body()
            state = write_accounting_storage_state(payload.get("entries"), payload.get("baseRevision"))
        except AccountingStorageRevisionConflict as exc:
            self.send_json({"ok": False, "error": "revision_conflict", "state": exc.state}, status=409)
            return
        except ValueError as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=400)
            return
        except AccountingStorageReadError as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=500)
            return
        except OSError:
            self.send_json({"ok": False, "error": "accounting_storage_write_failed"}, status=500)
            return
        self.send_json(state)

    def serve_youtube_playlist_import(self):
        try:
            payload = self.read_chat_history_json_body()
            url = payload.get("url")
            if not isinstance(url, str):
                raise ValueError("invalid_youtube_playlist_url")
            result = fetch_youtube_playlist_import(url)
        except ValueError as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=400)
            return
        except PermissionError as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=403)
            return
        except TimeoutError as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=504)
            return
        except (LookupError, RuntimeError) as exc:
            self.send_json({"ok": False, "error": str(exc)}, status=502)
            return
        self.send_json(result)

    def serve_tweet_meta(self, query: str):
        url = urllib.parse.parse_qs(query).get("url", [""])[0].strip()
        if not url:
            self.send_json(empty_tweet_meta("", "missing_url"))
            return

        try:
            self.send_json(fetch_tweet_meta(url))
        except Exception:
            self.send_json(empty_tweet_meta(url, "metadata_fetch_failed"))

    def serve_video_image(self, query: str):
        url = urllib.parse.parse_qs(query).get("url", [""])[0].strip()
        if not url:
            self.send_error(400)
            return

        try:
            content_type, body = fetch_video_image(url)
        except Exception:
            self.send_error(404)
            return

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "public, max-age=3600")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def serve_codex_usage(self, query: str):
        params = urllib.parse.parse_qs(query)
        path = params.get("path", [str(ROOT)])[0].strip()
        tier = params.get("tier", ["standard"])[0].strip().lower()
        if tier not in {"standard", "batch", "flex", "priority"}:
            tier = "standard"

        try:
            self.send_json(scan_codex_usage(path, tier))
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)})

    def get_paper(self, query: str) -> dict | None:
        paper_id = urllib.parse.parse_qs(query).get("id", [""])[0]
        if paper_id not in PAPER_INDEX:
            scan_papers()
        return PAPER_INDEX.get(paper_id)

    def serve_pdf(self, query: str):
        paper = self.get_paper(query)
        if not paper:
            self.send_error(404)
            return

        path = Path(paper["absolutePath"])
        if not path.exists():
            self.send_error(404)
            return

        ctype = mimetypes.guess_type(path.name)[0] or "application/pdf"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Disposition", f'inline; filename="{path.name}"')
        self.send_header("Content-Length", str(path.stat().st_size))
        self.end_headers()
        with path.open("rb") as source:
            self.copyfile(source, self.wfile)

    def serve_thumbnail(self, query: str):
        paper = self.get_paper(query)
        if not paper:
            self.send_error(404)
            return

        if fitz is None:
            self.send_error(501)
            return

        path = Path(paper["absolutePath"])
        if not path.exists():
            self.send_error(404)
            return

        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_name = f'{paper["id"]}-{int(path.stat().st_mtime)}-{path.stat().st_size}.png'
        cache_path = CACHE_DIR / cache_name

        if not cache_path.exists():
            try:
                doc = fitz.open(path)
                page = doc.load_page(0)
                pix = page.get_pixmap(matrix=fitz.Matrix(0.55, 0.55), alpha=False)
                pix.save(cache_path)
                doc.close()
            except Exception:
                self.send_error(500)
                return

        body = cache_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Cache-Control", "public, max-age=86400")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    port = int(os.environ.get("AI_WORKBENCH_PORT", "5173"))
    server = ThreadingHTTPServer(("127.0.0.1", port), WorkbenchHandler)
    print(f"AI workbench running at http://127.0.0.1:{port}/")
    server.serve_forever()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
