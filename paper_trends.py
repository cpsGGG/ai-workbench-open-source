from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path


PAPER_TRENDS_LIMIT = 20
PAPER_TRENDS_CACHE_SCHEMA_VERSION = 6
PAPER_TRENDS_CACHE_TTL = 10 * 60
PAPER_TRENDS_TIMEOUT = 6
PAPER_TRENDS_MAX_BODY = 3 * 1024 * 1024
PAPER_TRENDS_MAX_WORKERS = 8
PAPER_TRENDS_CACHE_PATH = Path(__file__).resolve().parent / ".cache" / "paper-trends.json"
PAPER_TRENDS_USER_AGENT = "AIWorkbench/1.0 (+local paper trends reader)"
PAPER_TRENDS_QUERY_MAX_CHARS = 48
PAPER_TRENDS_QUERY_MAX_BYTES = 144
PAPER_TRENDS_CACHE_MAX_FILES = 40
PAPER_TREND_IMAGE_MAX_BODY = 2 * 1024 * 1024
PAPER_TREND_IMAGE_CACHE_MAX_ITEMS = 64
PAPER_TREND_IMAGE_NEGATIVE_TTL = 5 * 60
PAPER_TREND_LIKES_SCHEMA_VERSION = 1
PAPER_TREND_LIKES_MAX_RECORDS = 500
PAPER_TREND_LIKES_MAX_BODY = 256 * 1024
PAPER_TREND_LIKES_PATH = (
    Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "AIWorkbench" / "paper-trend-likes.json"
)

PAPER_TREND_TOPICS = {
    "large-models": {
        "label": "大模型",
        "terms": ("large language model", "language model", "llm", "foundation model", "大模型", "语言模型"),
    },
    "world-models": {
        "label": "世界模型",
        "terms": ("world model", "world models", "embodied world model", "世界模型"),
    },
    "robotics": {
        "label": "机器人",
        "terms": ("robotics", "robotic", "robot", "humanoid", "embodied ai", "manipulation", "机器人"),
    },
    "computer-vision": {
        "label": "计算机视觉",
        "terms": ("computer vision", "visual recognition", "image generation", "vision model", "cv", "计算机视觉"),
    },
    "machine-learning": {
        "label": "机器学习",
        "terms": ("machine learning", "reinforcement learning", "representation learning", "机器学习"),
    },
    "deep-learning": {
        "label": "深度学习",
        "terms": ("deep learning", "neural network", "transformer", "diffusion model", "深度学习", "神经网络"),
    },
}

_SOURCE_URLS = {
    "hugging-face": "https://huggingface.co/api/daily_papers?limit=50",
    "arxiv": (
        "https://export.arxiv.org/api/query?"
        "search_query=cat%3Acs.AI%20OR%20cat%3Acs.LG%20OR%20cat%3Acs.CL%20OR%20cat%3Acs.CV"
        "&start=0&max_results=50&sortBy=submittedDate&sortOrder=descending"
    ),
    "openai": "https://openai.com/news/rss.xml",
    "deepmind": "https://deepmind.google/blog/rss.xml",
    "microsoft-research": "https://www.microsoft.com/en-us/research/feed/",
    "google-research": "https://research.google/blog/rss/",
    "apple-ml": "https://machinelearning.apple.com/rss.xml",
    "amazon-science": "https://www.amazon.science/index.rss",
}
_ALLOWED_UPSTREAM_HOSTS = {
    "huggingface.co",
    "export.arxiv.org",
    "arxiv.org",
    "openai.com",
    "www.openai.com",
    "deepmind.google",
    "www.microsoft.com",
    "research.google",
    "machinelearning.apple.com",
    "www.amazon.science",
    "hn.algolia.com",
}
# Images have a deliberately separate and narrower allowlist than feeds.  The
# browser never receives these upstream URLs; it requests an item id from the
# local proxy and the server resolves that id against a fetched/cache item.
_IMAGE_ALLOWED_HOSTS_BY_SOURCE = {
    "hugging-face": {"cdn-thumbnails.huggingface.co"},
    "deepmind": {"lh3.googleusercontent.com"},
    "microsoft-research": {"www.microsoft.com"},
    "google-research": {"storage.googleapis.com"},
    "amazon-science": {"cdn.amazon.science"},
}
_IMAGE_CONTENT_TYPES = {
    "image/jpeg": "image/jpeg",
    "image/png": "image/png",
    "image/webp": "image/webp",
}
_OFFICIAL_HN_HOSTS = {
    "openai.com",
    "www.openai.com",
    "deepmind.google",
    "anthropic.com",
    "www.anthropic.com",
    "mistral.ai",
    "www.mistral.ai",
    "x.ai",
    "www.x.ai",
    "kimi.com",
    "www.kimi.com",
}
_CACHE_LOCK = threading.RLock()
_FETCH_LOCK = threading.Lock()
_MEMORY_CACHE: dict[str, dict] | dict | None = None
_LIKES_LOCK = threading.RLock()
_IMAGE_CACHE_LOCK = threading.RLock()
_IMAGE_FETCH_SLOTS = threading.BoundedSemaphore(4)
_IMAGE_MEMORY_CACHE: OrderedDict[str, tuple[bytes, str]] = OrderedDict()
_IMAGE_NEGATIVE_CACHE: dict[str, float] = {}


class PaperTrendLikesRevisionConflict(Exception):
    def __init__(self, state: dict):
        super().__init__("revision_conflict")
        self.state = state


class PaperTrendLikesReadError(Exception):
    pass


class PaperTrendImageNotFound(Exception):
    pass


class PaperTrendImageFetchError(Exception):
    pass

_SOURCE_PRIORITY = {
    "openai": 0,
    "deepmind": 1,
    "google-research": 2,
    "microsoft-research": 3,
    "apple-ml": 4,
    "amazon-science": 5,
    "hugging-face": 6,
    "arxiv": 7,
}
_OFFICIAL_SOURCE_CONFIG = {
    "openai": ("OpenAI", False),
    "deepmind": ("Google DeepMind", False),
    "microsoft-research": ("Microsoft Research", True),
    "google-research": ("Google Research", True),
    "apple-ml": ("Apple Machine Learning Research", False),
    "amazon-science": ("Amazon Science", True),
}
_AI_RESEARCH_PATTERN = re.compile(
    r"\b(?:ai|artificial intelligence|machine learning|deep learning|language model|llm|vlm|"
    r"generative|transformer|diffusion|neural|agentic|agent|reasoning|robot|computer vision|"
    r"natural language|nlp|speech|multimodal|foundation model|reinforcement learning|rl|"
    r"embedding|model training|inference|benchmark|alignment|automated reasoning)\b",
    re.IGNORECASE,
)


class _PlainTextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str):
        self.parts.append(data)


class _FirstImageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.src = ""

    def handle_starttag(self, tag: str, attrs):
        if self.src or tag.lower() != "img":
            return
        values = {str(key).lower(): value for key, value in attrs}
        self.src = str(values.get("src") or values.get("data-src") or "").strip()


class _AllowlistedRedirectHandler(urllib.request.HTTPRedirectHandler):
    max_redirections = 4
    max_repeats = 2

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlparse(newurl)
        if parsed.scheme != "https" or (parsed.hostname or "").lower() not in _ALLOWED_UPSTREAM_HOSTS:
            raise urllib.error.URLError("upstream_redirect_blocked")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _ImageAllowlistedRedirectHandler(urllib.request.HTTPRedirectHandler):
    max_redirections = 3
    max_repeats = 2

    def __init__(self, source_key: str):
        super().__init__()
        self.source_key = source_key

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _safe_image_url(newurl, self.source_key):
            raise urllib.error.URLError("paper_trend_image_redirect_blocked")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _clean_text(value, limit: int = 520) -> str:
    parser = _PlainTextParser()
    try:
        parser.feed(str(value or ""))
        text = " ".join(parser.parts)
    except Exception:
        text = str(value or "")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _normalize_topic_search(topic="", query="") -> dict:
    if not isinstance(topic, str) or not isinstance(query, str):
        raise ValueError("invalid_search")
    topic = topic.strip().lower()
    query = re.sub(r"\s+", " ", query).strip()
    if topic and query:
        raise ValueError("topic_and_query_are_mutually_exclusive")
    if topic and topic not in PAPER_TREND_TOPICS:
        raise ValueError("unknown_topic")
    if len(query) > PAPER_TRENDS_QUERY_MAX_CHARS or len(query.encode("utf-8")) > PAPER_TRENDS_QUERY_MAX_BYTES:
        raise ValueError("query_too_long")
    if query and (re.search(r"[\x00-\x1f\x7f]", query) or not re.fullmatch(r"[\w\s\u4e00-\u9fff+.,()&\-]+", query)):
        raise ValueError("query_contains_unsupported_characters")

    normalized_query = query.casefold()
    if query:
        for key, config in PAPER_TREND_TOPICS.items():
            aliases = {key, config["label"].casefold(), *(term.casefold() for term in config["terms"])}
            if normalized_query in aliases:
                topic = key
                query = ""
                normalized_query = ""
                break
    config = PAPER_TREND_TOPICS.get(topic)
    if config:
        terms = list(config["terms"])
        label = config["label"]
        mode = "topic"
    elif query:
        tokens = [part for part in re.findall(r"[\w\u4e00-\u9fff]+", normalized_query) if len(part) >= 2]
        terms = list(dict.fromkeys([normalized_query, *tokens]))[:10]
        label = query
        mode = "query"
    else:
        terms = []
        label = "为你推荐"
        mode = "recommended"
    identity = f"{mode}:{topic or normalized_query}"
    return {
        "mode": mode,
        "topic": topic,
        "query": query,
        "normalizedQuery": normalized_query,
        "label": label,
        "terms": terms,
        "identity": identity,
    }


def paper_trend_topics_payload() -> list[dict]:
    return [{"key": key, "label": value["label"]} for key, value in PAPER_TREND_TOPICS.items()]


def _empty_likes_state() -> dict:
    return {
        "ok": True,
        "schemaVersion": PAPER_TREND_LIKES_SCHEMA_VERSION,
        "revision": 0,
        "likes": [],
        "updatedAt": "",
    }


def _validate_liked_item(value) -> dict:
    if not isinstance(value, dict):
        raise ValueError("invalid_liked_item")
    record_id = str(value.get("id") or "")
    if not re.fullmatch(r"[0-9a-f]{16}", record_id):
        raise ValueError("invalid_liked_item_id")
    url = _safe_external_url(value.get("url"))
    if not url or len(url) > 2048:
        raise ValueError("invalid_liked_item_url")
    if record_id != _stable_item_id(url):
        raise ValueError("liked_item_id_does_not_match_url")
    title = _clean_text(value.get("title"), 220)
    if not title:
        raise ValueError("invalid_liked_item_title")
    topics = value.get("topics") or []
    if not isinstance(topics, list) or len(topics) > 12:
        raise ValueError("invalid_liked_item_topics")
    clean_topics = []
    for topic in topics:
        if not isinstance(topic, str):
            raise ValueError("invalid_liked_item_topics")
        cleaned = _clean_text(topic, 40)
        if cleaned and cleaned not in clean_topics:
            clean_topics.append(cleaned)
    return {
        "id": record_id,
        "url": url,
        "title": title,
        "summary": _clean_text(value.get("summary"), 520),
        "source": _clean_text(value.get("source"), 100),
        "topics": clean_topics,
        "likedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def _validate_likes_state(payload) -> dict:
    if not isinstance(payload, dict) or payload.get("schemaVersion") != PAPER_TREND_LIKES_SCHEMA_VERSION:
        raise PaperTrendLikesReadError("paper_trend_likes_invalid_schema")
    revision = payload.get("revision")
    likes = payload.get("likes")
    if (
        not isinstance(revision, int)
        or isinstance(revision, bool)
        or revision < 0
        or not isinstance(likes, list)
        or len(likes) > PAPER_TREND_LIKES_MAX_RECORDS
    ):
        raise PaperTrendLikesReadError("paper_trend_likes_invalid_data")
    normalized = []
    seen = set()
    try:
        for value in likes:
            item = _validate_liked_item(value)
            liked_at = value.get("likedAt")
            if not isinstance(liked_at, str) or len(liked_at) > 40:
                raise ValueError("invalid_liked_at")
            if _date_and_timestamp(liked_at)[1] is None:
                raise ValueError("invalid_liked_at")
            item["likedAt"] = liked_at
            if item["id"] not in seen:
                normalized.append(item)
                seen.add(item["id"])
    except ValueError as exc:
        raise PaperTrendLikesReadError(str(exc)) from exc
    updated_at = payload.get("updatedAt", "")
    if not isinstance(updated_at, str) or len(updated_at) > 40:
        raise PaperTrendLikesReadError("paper_trend_likes_invalid_updated_at")
    return {
        "ok": True,
        "schemaVersion": PAPER_TREND_LIKES_SCHEMA_VERSION,
        "revision": revision,
        "likes": normalized,
        "updatedAt": updated_at,
    }


def read_paper_trend_likes_state() -> dict:
    with _LIKES_LOCK:
        if not PAPER_TREND_LIKES_PATH.exists():
            return _empty_likes_state()
        try:
            if PAPER_TREND_LIKES_PATH.stat().st_size > PAPER_TREND_LIKES_MAX_BODY:
                raise PaperTrendLikesReadError("paper_trend_likes_too_large")
            payload = json.loads(PAPER_TREND_LIKES_PATH.read_text(encoding="utf-8"))
        except PaperTrendLikesReadError:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PaperTrendLikesReadError("paper_trend_likes_read_failed") from exc
        return _validate_likes_state(payload)


def write_paper_trend_like_intent(item, liked, base_revision) -> dict:
    if (
        not isinstance(liked, bool)
        or not isinstance(base_revision, int)
        or isinstance(base_revision, bool)
        or base_revision < 0
    ):
        raise ValueError("invalid_like_intent")
    record = _validate_liked_item(item)
    with _LIKES_LOCK:
        current = read_paper_trend_likes_state()
        if base_revision != current["revision"]:
            raise PaperTrendLikesRevisionConflict(current)
        existing = {entry["id"]: entry for entry in current["likes"]}
        if liked:
            if record["id"] in existing:
                record["likedAt"] = existing[record["id"]]["likedAt"]
            existing[record["id"]] = record
        else:
            existing.pop(record["id"], None)
        likes = sorted(existing.values(), key=lambda value: value["likedAt"], reverse=True)
        if len(likes) > PAPER_TREND_LIKES_MAX_RECORDS:
            likes = likes[:PAPER_TREND_LIKES_MAX_RECORDS]
        state = {
            "ok": True,
            "schemaVersion": PAPER_TREND_LIKES_SCHEMA_VERSION,
            "revision": current["revision"] + 1,
            "likes": likes,
            "updatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        PAPER_TREND_LIKES_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = PAPER_TREND_LIKES_PATH.with_name(
            f".{PAPER_TREND_LIKES_PATH.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        try:
            body = json.dumps(state, ensure_ascii=False, indent=2).encode("utf-8")
            if len(body) > PAPER_TREND_LIKES_MAX_BODY:
                raise ValueError("paper_trend_likes_too_large")
            with temporary.open("xb") as target:
                target.write(body)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, PAPER_TREND_LIKES_PATH)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        return state


def _safe_external_url(value) -> str:
    try:
        parsed = urllib.parse.urlparse(str(value or "").strip())
    except Exception:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    return urllib.parse.urlunparse(parsed._replace(fragment=""))


def _safe_image_url(value, source_key: str, base_url: str = "") -> str:
    raw_value = str(value or "").strip()
    if not raw_value:
        return ""
    try:
        url = urllib.parse.urljoin(base_url, raw_value)
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return ""
    allowed_hosts = _IMAGE_ALLOWED_HOSTS_BY_SOURCE.get(source_key, set())
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.port not in {None, 443}
        or parsed.hostname.lower() not in allowed_hosts
    ):
        return ""
    return urllib.parse.urlunparse(parsed._replace(fragment=""))


def _first_html_image(value, source_key: str) -> str:
    parser = _FirstImageParser()
    try:
        parser.feed(str(value or ""))
    except Exception:
        return ""
    return _safe_image_url(parser.src, source_key, _SOURCE_URLS.get(source_key, ""))


def _rss_item_image(node: ET.Element, source_key: str) -> str:
    """Pick the first source-owned RSS image; never accept a generic host."""
    html_values = []
    for element in node.iter():
        local_name = element.tag.rsplit("}", 1)[-1].lower()
        if local_name in {"thumbnail", "content", "enclosure"}:
            media_type = str(element.get("type") or "").lower()
            media_url = element.get("url") or element.get("href")
            if media_url and (local_name != "enclosure" or not media_type or media_type.startswith("image/")):
                candidate = _safe_image_url(
                    media_url,
                    source_key,
                    _SOURCE_URLS.get(source_key, ""),
                )
                if candidate:
                    return candidate
        if local_name in {"description", "content", "encoded"} and element.text:
            html_values.append(element.text)
    for value in html_values:
        candidate = _first_html_image(value, source_key)
        if candidate:
            return candidate
    return ""


def _date_and_timestamp(value) -> tuple[str, float | None]:
    raw = str(value or "").strip()
    if not raw:
        return "", None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(raw)
        except (TypeError, ValueError, OverflowError):
            return "", None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed = parsed.astimezone(timezone.utc)
    return parsed.date().isoformat(), parsed.timestamp()


def _read_upstream(url: str, accepted_types: tuple[str, ...]) -> bytes:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in _ALLOWED_UPSTREAM_HOSTS:
        raise ValueError("upstream_not_allowed")
    request = urllib.request.Request(
        url,
        headers={
            "Accept": ", ".join(accepted_types),
            "User-Agent": PAPER_TRENDS_USER_AGENT,
        },
    )
    opener = urllib.request.build_opener(_AllowlistedRedirectHandler())
    with opener.open(request, timeout=PAPER_TRENDS_TIMEOUT) as response:
        content_type = (response.headers.get_content_type() or "").lower()
        if content_type not in accepted_types:
            raise ValueError(f"unexpected_content_type:{content_type or 'unknown'}")
        body = response.read(PAPER_TRENDS_MAX_BODY + 1)
        if len(body) > PAPER_TRENDS_MAX_BODY:
            raise ValueError("upstream_response_too_large")
        return body


def _read_json(url: str):
    body = _read_upstream(url, ("application/json", "text/json"))
    return json.loads(body.decode("utf-8"))


def _read_xml(url: str) -> ET.Element:
    body = _read_upstream(
        url,
        (
            "application/atom+xml",
            "application/rss+xml",
            "application/xml",
            "text/xml",
        ),
    )
    return ET.fromstring(body)


def _new_item(
    *,
    title: str,
    url: str,
    source: str,
    source_key: str,
    published_at: str,
    summary: str,
    item_type: str,
    authors: str = "",
    popularity: int = 0,
    comments: int = 0,
    hot_reason: str = "",
    image_url: str = "",
) -> dict | None:
    safe_url = _safe_external_url(url)
    clean_title = _clean_text(title, 220)
    clean_date, timestamp = _date_and_timestamp(published_at)
    if not clean_title or not safe_url:
        return None
    item = {
        "title": clean_title,
        "url": safe_url,
        "source": source,
        "sourceKey": source_key,
        "publishedAt": clean_date,
        "summary": _clean_text(summary),
        "itemType": item_type,
        "authors": _clean_text(authors, 180),
        "popularity": max(0, int(popularity or 0)),
        "comments": max(0, int(comments or 0)),
        "hotReason": _clean_text(hot_reason, 120),
        "_timestamp": timestamp,
    }
    safe_image = _safe_image_url(image_url, source_key)
    if safe_image:
        item["_imageUrl"] = safe_image
    return item


def _fetch_hugging_face() -> list[dict]:
    payload = _read_json(_SOURCE_URLS["hugging-face"])
    if not isinstance(payload, list):
        raise ValueError("invalid_hugging_face_payload")
    items = []
    for row in payload:
        paper = row.get("paper") if isinstance(row, dict) else None
        if not isinstance(paper, dict):
            continue
        paper_id = str(paper.get("id") or "").strip()
        if not re.fullmatch(r"\d{4}\.\d{4,5}(?:v\d+)?", paper_id):
            continue
        author_names = [
            str(author.get("name") or "").strip()
            for author in paper.get("authors", [])
            if isinstance(author, dict) and author.get("name")
        ]
        authors = ", ".join(author_names[:4])
        if len(author_names) > 4:
            authors += " et al."
        upvotes = int(paper.get("upvotes") or row.get("upvotes") or 0)
        item = _new_item(
            title=paper.get("title"),
            url=f"https://huggingface.co/papers/{paper_id}",
            source="Hugging Face Daily Papers",
            source_key="hugging-face",
            published_at=paper.get("publishedAt") or paper.get("submittedOnDailyAt"),
            summary=paper.get("summary"),
            item_type="论文",
            authors=authors,
            popularity=upvotes,
            image_url=row.get("thumbnail"),
            hot_reason=f"HF Daily Papers · {upvotes} 赞" if upvotes else "HF Daily Papers 今日收录",
        )
        if item:
            items.append(item)
    return items


def _arxiv_url(search: dict) -> str:
    if not search["terms"]:
        return _SOURCE_URLS["arxiv"]
    safe_terms = []
    for term in search["terms"][:8]:
        normalized = re.sub(r"[^\w\s\-]", " ", term, flags=re.UNICODE)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        if normalized:
            safe_terms.append(f'all:"{normalized}"')
    search_query = " OR ".join(safe_terms) or "cat:cs.AI"
    return "https://export.arxiv.org/api/query?" + urllib.parse.urlencode(
        {
            "search_query": search_query,
            "start": 0,
            "max_results": 100,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
    )


def _fetch_arxiv(search: dict | None = None) -> list[dict]:
    root = _read_xml(_arxiv_url(search or _normalize_topic_search()))
    namespace = {"atom": "http://www.w3.org/2005/Atom"}
    items = []
    for entry in root.findall("atom:entry", namespace):
        entry_url = entry.findtext("atom:id", default="", namespaces=namespace)
        alternate = next(
            (
                link.get("href", "")
                for link in entry.findall("atom:link", namespace)
                if link.get("rel") == "alternate"
            ),
            entry_url,
        )
        authors = [
            author.findtext("atom:name", default="", namespaces=namespace)
            for author in entry.findall("atom:author", namespace)
        ]
        author_text = ", ".join(filter(None, authors[:4]))
        if len(authors) > 4:
            author_text += " et al."
        item = _new_item(
            title=entry.findtext("atom:title", default="", namespaces=namespace),
            url=alternate,
            source="arXiv",
            source_key="arxiv",
            published_at=entry.findtext("atom:published", default="", namespaces=namespace),
            summary=entry.findtext("atom:summary", default="", namespaces=namespace),
            item_type="论文",
            authors=author_text,
            hot_reason="arXiv AI 分类最新提交",
        )
        if item:
            items.append(item)
    return items


def _fetch_official_feed(source_key: str, source: str, require_ai_match: bool = False) -> list[dict]:
    root = _read_xml(_SOURCE_URLS[source_key])
    items = []
    allowed_openai_categories = {"Research", "Publication", "Safety & Alignment", "Engineering"}
    for node in root.findall(".//item"):
        categories = [_clean_text(category.text, 80) for category in node.findall("category") if category.text]
        if source_key == "openai" and not set(categories).intersection(allowed_openai_categories):
            continue
        title = node.findtext("title") or ""
        description = node.findtext("description") or ""
        combined = f"{title} {description} {' '.join(categories)}"
        if require_ai_match and not _AI_RESEARCH_PATTERN.search(_clean_text(combined, 1200)):
            continue
        item_type = "技术报告" if re.search(
            r"\b(technical report|system card|model card|paper)\b", combined, re.IGNORECASE
        ) else "官方文章"
        item = _new_item(
            title=title,
            url=node.findtext("link") or node.findtext("guid") or "",
            source=source,
            source_key=source_key,
            published_at=node.findtext("pubDate") or "",
            summary=description,
            item_type=item_type,
            image_url=_rss_item_image(node, source_key),
            hot_reason="官方研究频道新发布" if source_key == "openai" else "官方研究源新发布",
        )
        if item:
            items.append(item)
        if len(items) >= 40:
            break
    return items


def _official_hn_url(url: str) -> bool:
    safe = _safe_external_url(url)
    if not safe:
        return False
    parsed = urllib.parse.urlparse(safe)
    host = (parsed.hostname or "").lower()
    if host in _OFFICIAL_HN_HOSTS:
        return True
    if host == "github.com":
        path = parsed.path.lower()
        return path.startswith(("/moonshotai/", "/deepseek-ai/", "/qwenlm/"))
    return False


def _fetch_hn_signals() -> list[dict]:
    cutoff = int((datetime.now(timezone.utc) - timedelta(days=120)).timestamp())
    query = urllib.parse.urlencode(
        {
            "tags": "story",
            "numericFilters": f"created_at_i>{cutoff},points>10",
            "hitsPerPage": 100,
        }
    )
    payload = _read_json(f"https://hn.algolia.com/api/v1/search?{query}")
    hits = payload.get("hits") if isinstance(payload, dict) else None
    if not isinstance(hits, list):
        raise ValueError("invalid_hn_payload")
    signals = []
    for hit in hits:
        if not isinstance(hit, dict) or not _official_hn_url(hit.get("url")):
            continue
        signals.append(
            {
                "title": _clean_text(hit.get("title"), 220),
                "url": _safe_external_url(hit.get("url")),
                "points": max(0, int(hit.get("points") or 0)),
                "comments": max(0, int(hit.get("num_comments") or 0)),
            }
        )
    return signals


def _canonical_url(value: str) -> str:
    parsed = urllib.parse.urlparse(value)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    path = re.sub(r"/+$", "", parsed.path.lower())
    return f"{host}{path}"


def _canonical_title(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", value.lower())


def _canonical_paper_id(value: str) -> str:
    parsed = urllib.parse.urlparse(value)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    path = parsed.path.strip("/")
    if host == "huggingface.co":
        match = re.fullmatch(r"papers/(\d{4}\.\d{4,5})(?:v\d+)?", path, flags=re.IGNORECASE)
        return f"arxiv:{match.group(1)}" if match else ""
    if host in {"arxiv.org", "export.arxiv.org"}:
        match = re.fullmatch(r"(?:abs|pdf)/(\d{4}\.\d{4,5})(?:v\d+)?(?:\.pdf)?", path, flags=re.IGNORECASE)
        return f"arxiv:{match.group(1)}" if match else ""
    return ""


def _stable_item_id(value: str) -> str:
    identity = _canonical_paper_id(value) or _canonical_url(value)
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]


def _term_matches(text: str, term: str) -> bool:
    normalized_term = term.casefold().strip()
    if not normalized_term:
        return False
    if re.search(r"[\u4e00-\u9fff]", normalized_term) or " " in normalized_term:
        return normalized_term in text
    return re.search(rf"(?<![a-z0-9]){re.escape(normalized_term)}(?![a-z0-9])", text) is not None


def _match_item(item: dict, terms: list[str]) -> tuple[list[str], float]:
    title = str(item.get("title") or "").casefold()
    summary = str(item.get("summary") or "").casefold()
    matched = []
    score = 0.0
    for term in terms:
        title_match = _term_matches(title, term)
        summary_match = _term_matches(summary, term)
        if title_match or summary_match:
            matched.append(term)
            score += 18.0 if title_match else 7.0
            if " " in term.strip():
                score += 8.0
    return matched, score


def _item_topics(item: dict) -> list[str]:
    topics = []
    for config in PAPER_TREND_TOPICS.values():
        matched, _ = _match_item(item, list(config["terms"]))
        if matched:
            topics.append(config["label"])
    return topics[:6]


def _preference_terms(likes: list[dict]) -> list[str]:
    scores: dict[str, int] = {}
    for liked in likes:
        text_item = {"title": liked.get("title", ""), "summary": liked.get("summary", "")}
        for config in PAPER_TREND_TOPICS.values():
            matched, _ = _match_item(text_item, list(config["terms"]))
            if matched or config["label"] in liked.get("topics", []):
                for term in config["terms"]:
                    scores[term] = scores.get(term, 0) + 1
    return [term for term, _ in sorted(scores.items(), key=lambda value: (-value[1], value[0]))[:24]]


def _merge_and_rank(
    candidates: list[dict],
    hn_signals: list[dict],
    search: dict | None = None,
    preference_terms: list[str] | None = None,
) -> list[dict]:
    search = search or _normalize_topic_search()
    preference_terms = preference_terms or []
    deduped: list[dict] = []
    by_url: dict[str, dict] = {}
    by_title: dict[str, dict] = {}
    by_paper_id: dict[str, dict] = {}
    # Completion order from concurrent fetches must not decide which source
    # represents a duplicate. Official/editorial sources and HF's curated page
    # win deterministically; richer fields can still be merged into that item.
    ordered_candidates = sorted(
        (copy.deepcopy(item) for item in candidates),
        key=lambda item: (
            _SOURCE_PRIORITY.get(item.get("sourceKey"), 99),
            -(item.get("popularity") or 0),
            -(item.get("_timestamp") or 0),
            _canonical_title(item.get("title", "")),
        ),
    )
    for candidate in ordered_candidates:
        url_key = _canonical_url(candidate["url"])
        title_key = _canonical_title(candidate["title"])
        paper_id_key = _canonical_paper_id(candidate["url"])
        existing = (
            (by_paper_id.get(paper_id_key) if paper_id_key else None)
            or by_url.get(url_key)
            or by_title.get(title_key)
        )
        if existing is None:
            deduped.append(candidate)
            by_url[url_key] = candidate
            by_title[title_key] = candidate
            if paper_id_key:
                by_paper_id[paper_id_key] = candidate
            continue
        by_url[url_key] = existing
        by_title[title_key] = existing
        if paper_id_key:
            by_paper_id[paper_id_key] = existing
        if candidate.get("popularity", 0) > existing.get("popularity", 0):
            existing["popularity"] = candidate["popularity"]
            existing["hotReason"] = candidate.get("hotReason") or existing.get("hotReason", "")
        if len(candidate.get("summary") or "") > len(existing.get("summary") or ""):
            existing["summary"] = candidate["summary"]
        if len(candidate.get("authors") or "") > len(existing.get("authors") or ""):
            existing["authors"] = candidate["authors"]
        if not existing.get("_imageUrl") and candidate.get("_imageUrl"):
            existing["_imageUrl"] = candidate["_imageUrl"]

    for signal in hn_signals:
        item = by_url.get(_canonical_url(signal["url"])) or by_title.get(_canonical_title(signal["title"]))
        if not item:
            continue
        item["popularity"] = max(item.get("popularity", 0), signal["points"])
        item["comments"] = max(item.get("comments", 0), signal["comments"])
        item["hotReason"] = f"社区热议 · HN {signal['points']} 分 / {signal['comments']} 评论"

    now = datetime.now(timezone.utc).timestamp()
    source_bonus = {
        "hugging-face": 18,
        "arxiv": 8,
        "openai": 27,
        "deepmind": 27,
        "google-research": 25,
        "microsoft-research": 25,
        "apple-ml": 25,
        "amazon-science": 22,
    }
    for item in deduped:
        timestamp = item.get("_timestamp")
        if timestamp is None or timestamp > now + 2 * 86400:
            freshness = 0
        else:
            age_days = max(0.0, (now - timestamp) / 86400)
            freshness = max(0.0, 70.0 - age_days * 1.35)
        heat = min(60.0, math.log1p(item.get("popularity", 0)) * 18.0)
        discussion = min(15.0, math.log1p(item.get("comments", 0)) * 4.0)
        matched_terms, relevance = _match_item(item, search["terms"])
        if search["terms"] and not matched_terms:
            item["_excluded"] = True
            continue
        preferred_terms, preference_score = _match_item(item, preference_terms)
        item["matchedTerms"] = matched_terms[:8]
        item["relevanceScore"] = round(relevance, 1)
        item["topics"] = _item_topics(item)
        if search["terms"]:
            item["matchReason"] = f"匹配：{'、'.join(matched_terms[:3])}"
        elif preferred_terms:
            item["matchReason"] = f"基于点赞偏好：{'、'.join(preferred_terms[:3])}"
        else:
            item["matchReason"] = "综合新鲜度与公开热度"
        item["preferenceAdjusted"] = bool(preferred_terms)
        item["_score"] = (
            freshness
            + source_bonus.get(item["sourceKey"], 0)
            + heat
            + discussion
            + (relevance * 1000 if search["terms"] else 0)
            + min(24.0, preference_score * 0.5)
        )

    ranked = sorted(
        (item for item in deduped if not item.get("_excluded")),
        key=lambda item: (item["_score"], item.get("_timestamp") or 0),
        reverse=True,
    )
    selected = []
    source_counts: dict[str, int] = {}
    per_source_target = 4
    for item in ranked:
        source_key = item["sourceKey"]
        if source_counts.get(source_key, 0) >= per_source_target:
            continue
        selected.append(item)
        source_counts[source_key] = source_counts.get(source_key, 0) + 1
        if len(selected) == PAPER_TRENDS_LIMIT:
            break
    if len(selected) < PAPER_TRENDS_LIMIT:
        selected_urls = {item["url"] for item in selected}
        selected.extend(item for item in ranked if item["url"] not in selected_urls)
    selected = selected[:PAPER_TRENDS_LIMIT]

    for rank, item in enumerate(selected, start=1):
        item["rank"] = rank
        item["id"] = _stable_item_id(item["url"])
        if item.get("_imageUrl"):
            item["imageUrl"] = "/api/paper-trend-image?" + urllib.parse.urlencode({"id": item["id"]})
        item.pop("_timestamp", None)
        item.pop("_score", None)
        item.pop("_excluded", None)
    return selected


def _cached_trend_payloads() -> list[dict]:
    payloads = []
    with _CACHE_LOCK:
        if isinstance(_MEMORY_CACHE, dict):
            if "items" in _MEMORY_CACHE:
                payloads.append(copy.deepcopy(_MEMORY_CACHE))
            else:
                payloads.extend(copy.deepcopy(list(_MEMORY_CACHE.values())))
        paths = [PAPER_TRENDS_CACHE_PATH]
        if PAPER_TRENDS_CACHE_PATH.parent.exists():
            paths.extend(PAPER_TRENDS_CACHE_PATH.parent.glob("paper-trends-*.json"))
        for path in paths[: PAPER_TRENDS_CACHE_MAX_FILES + 1]:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            if (
                isinstance(payload, dict)
                and payload.get("schemaVersion") == PAPER_TRENDS_CACHE_SCHEMA_VERSION
                and isinstance(payload.get("items"), list)
            ):
                payloads.append(payload)
    return payloads


def _image_binding_for_item(item_id: str) -> tuple[str, str]:
    if not re.fullmatch(r"[0-9a-f]{16}", str(item_id or "")):
        raise PaperTrendImageNotFound("paper_trend_image_not_found")
    for payload in _cached_trend_payloads():
        for item in payload.get("items", []) if isinstance(payload.get("items"), list) else []:
            if not isinstance(item, dict) or item.get("id") != item_id:
                continue
            # Never trust cache ids blindly: the id must still bind to this
            # exact article, source, and source-specific image host.
            if _stable_item_id(str(item.get("url") or "")) != item_id:
                continue
            source_key = str(item.get("sourceKey") or "")
            image_url = _safe_image_url(item.get("_imageUrl"), source_key)
            if image_url:
                return image_url, source_key
    raise PaperTrendImageNotFound("paper_trend_image_not_found")


def _image_type_from_magic(body: bytes) -> str:
    if body.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if body.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(body) >= 12 and body.startswith(b"RIFF") and body[8:12] == b"WEBP":
        return "image/webp"
    return ""


def fetch_paper_trend_image(item_id: str) -> tuple[bytes, str]:
    image_url, source_key = _image_binding_for_item(item_id)
    cache_key = hashlib.sha256(f"{item_id}|{image_url}".encode("utf-8")).hexdigest()
    now = time.time()
    with _IMAGE_CACHE_LOCK:
        cached = _IMAGE_MEMORY_CACHE.get(cache_key)
        if cached:
            _IMAGE_MEMORY_CACHE.move_to_end(cache_key)
            return cached
        if _IMAGE_NEGATIVE_CACHE.get(cache_key, 0) > now:
            raise PaperTrendImageFetchError("paper_trend_image_temporarily_unavailable")
        _IMAGE_NEGATIVE_CACHE.pop(cache_key, None)

    request = urllib.request.Request(
        image_url,
        headers={
            "Accept": "image/webp,image/png,image/jpeg",
            "User-Agent": PAPER_TRENDS_USER_AGENT,
        },
    )
    try:
        with _IMAGE_FETCH_SLOTS:
            opener = urllib.request.build_opener(_ImageAllowlistedRedirectHandler(source_key))
            with opener.open(request, timeout=PAPER_TRENDS_TIMEOUT) as response:
                final_url = response.geturl()
                if not _safe_image_url(final_url, source_key):
                    raise ValueError("paper_trend_image_final_url_blocked")
                declared_type = (response.headers.get_content_type() or "").lower()
                if declared_type not in _IMAGE_CONTENT_TYPES:
                    raise ValueError("paper_trend_image_type_blocked")
                content_length = response.headers.get("Content-Length")
                if content_length and int(content_length) > PAPER_TREND_IMAGE_MAX_BODY:
                    raise ValueError("paper_trend_image_too_large")
                body = response.read(PAPER_TREND_IMAGE_MAX_BODY + 1)
        if len(body) > PAPER_TREND_IMAGE_MAX_BODY:
            raise ValueError("paper_trend_image_too_large")
        detected_type = _image_type_from_magic(body)
        if not detected_type or detected_type != declared_type:
            raise ValueError("paper_trend_image_content_mismatch")
    except PaperTrendImageFetchError:
        raise
    except Exception as exc:
        with _IMAGE_CACHE_LOCK:
            _IMAGE_NEGATIVE_CACHE[cache_key] = time.time() + PAPER_TREND_IMAGE_NEGATIVE_TTL
        raise PaperTrendImageFetchError(str(exc) or "paper_trend_image_fetch_failed") from exc

    result = (body, detected_type)
    with _IMAGE_CACHE_LOCK:
        _IMAGE_MEMORY_CACHE[cache_key] = result
        _IMAGE_MEMORY_CACHE.move_to_end(cache_key)
        while len(_IMAGE_MEMORY_CACHE) > PAPER_TREND_IMAGE_CACHE_MAX_ITEMS:
            _IMAGE_MEMORY_CACHE.popitem(last=False)
    return result


def public_paper_trends_payload(payload: dict) -> dict:
    """Return the browser representation without exposing upstream image URLs."""
    result = copy.deepcopy(payload)
    for item in result.get("items", []) if isinstance(result.get("items"), list) else []:
        if isinstance(item, dict):
            item.pop("_imageUrl", None)
    return result


def _source_error(exc: Exception) -> str:
    if isinstance(exc, (TimeoutError, urllib.error.URLError)) and "timed out" in str(exc).lower():
        return "请求超时"
    if isinstance(exc, urllib.error.HTTPError):
        return f"上游 HTTP {exc.code}"
    value = str(exc).splitlines()[0][:100]
    return value or "抓取失败"


def _cache_key(search: dict, preference_revision: int) -> str:
    raw = f"{search['identity']}|likes:{preference_revision}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _cache_path(cache_key: str) -> Path:
    return PAPER_TRENDS_CACHE_PATH.with_name(f"paper-trends-{cache_key}.json")


def _valid_cache_payload(payload, search: dict | None = None, preference_revision: int = 0) -> bool:
    items = payload.get("items") if isinstance(payload, dict) else None
    expected_search = (search or _normalize_topic_search())["identity"]
    return (
        isinstance(items, list)
        and 0 < len(items) <= PAPER_TRENDS_LIMIT
        and payload.get("schemaVersion") == PAPER_TRENDS_CACHE_SCHEMA_VERSION
        and payload.get("limit") == PAPER_TRENDS_LIMIT
        and payload.get("searchIdentity", "recommended:") == expected_search
        and payload.get("preferenceRevision", 0) == preference_revision
    )


def _read_disk_cache(
    cache_key: str = "",
    search: dict | None = None,
    preference_revision: int = 0,
) -> dict | None:
    path = _cache_path(cache_key) if cache_key else PAPER_TRENDS_CACHE_PATH
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if _valid_cache_payload(payload, search, preference_revision) else None


def _write_disk_cache(payload: dict, cache_key: str = ""):
    PAPER_TRENDS_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    path = _cache_path(cache_key) if cache_key else PAPER_TRENDS_CACHE_PATH
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)
    cached_files = sorted(
        PAPER_TRENDS_CACHE_PATH.parent.glob("paper-trends-*.json"),
        key=lambda value: value.stat().st_mtime,
        reverse=True,
    )
    for expired in cached_files[PAPER_TRENDS_CACHE_MAX_FILES:]:
        try:
            expired.unlink()
        except OSError:
            pass


def _cached_payload(search: dict | None = None, preference_revision: int = 0) -> dict | None:
    global _MEMORY_CACHE
    search = search or _normalize_topic_search()
    cache_key = _cache_key(search, preference_revision)
    if isinstance(_MEMORY_CACHE, dict):
        memory_value = _MEMORY_CACHE.get(cache_key) if "items" not in _MEMORY_CACHE else _MEMORY_CACHE
        if _valid_cache_payload(memory_value, search, preference_revision):
            return copy.deepcopy(memory_value)
    cached = _read_disk_cache(cache_key, search, preference_revision)
    if _valid_cache_payload(cached, search, preference_revision):
        if not isinstance(_MEMORY_CACHE, dict) or "items" in _MEMORY_CACHE:
            _MEMORY_CACHE = {}
        _MEMORY_CACHE[cache_key] = cached
        return copy.deepcopy(cached)
    return None


def fetch_paper_trends(force: bool = False, topic: str = "", query: str = "") -> dict:
    global _MEMORY_CACHE
    search = _normalize_topic_search(topic, query)
    try:
        likes_state = read_paper_trend_likes_state()
        likes = likes_state["likes"]
        preference_revision = likes_state["revision"]
        preference_available = True
    except PaperTrendLikesReadError:
        likes = []
        preference_revision = 0
        preference_available = False
    preferences = _preference_terms(likes)
    cache_key = _cache_key(search, preference_revision)
    with _FETCH_LOCK:
        with _CACHE_LOCK:
            cached = _cached_payload(search, preference_revision)
            cache_age = time.time() - float(cached.get("fetchedAtEpoch") or 0) if cached else float("inf")
            # A refresh bypasses the ten-minute cache, but requests arriving
            # together share the same result instead of multiplying upstream traffic.
            if cached and (not force and cache_age < PAPER_TRENDS_CACHE_TTL or cache_age < 3):
                cached["cache"] = "fresh"
                return cached

        fetchers = {
            "hugging-face": ("Hugging Face", _fetch_hugging_face),
            "arxiv": ("arXiv", lambda: _fetch_arxiv(search)),
            "hn": ("Hacker News 热度信号", _fetch_hn_signals),
        }
        for key, (label, require_ai_match) in _OFFICIAL_SOURCE_CONFIG.items():
            fetchers[key] = (
                label,
                lambda key=key, label=label, require_ai_match=require_ai_match: _fetch_official_feed(
                    key, label, require_ai_match
                ),
            )
        candidates: list[dict] = []
        hn_signals: list[dict] = []
        source_status = []
        with ThreadPoolExecutor(
            max_workers=min(PAPER_TRENDS_MAX_WORKERS, len(fetchers)), thread_name_prefix="paper-trends"
        ) as executor:
            futures = {executor.submit(fetcher): (key, label) for key, (label, fetcher) in fetchers.items()}
            for future in as_completed(futures):
                key, label = futures[future]
                try:
                    results = future.result()
                    if key == "hn":
                        hn_signals = results
                    else:
                        candidates.extend(results)
                    source_status.append({"key": key, "name": label, "ok": True, "count": len(results)})
                except Exception as exc:
                    source_status.append({"key": key, "name": label, "ok": False, "count": 0, "error": _source_error(exc)})

        items = _merge_and_rank(candidates, hn_signals, search, preferences)
        source_status.sort(key=lambda source: list(fetchers).index(source["key"]))
        if items and (len(items) == PAPER_TRENDS_LIMIT or search["mode"] != "recommended"):
            fetched_at_epoch = time.time()
            selected_source_counts: dict[str, int] = {}
            for item in items:
                source_key = item["sourceKey"]
                selected_source_counts[source_key] = selected_source_counts.get(source_key, 0) + 1
            payload = {
                "ok": True,
                "schemaVersion": PAPER_TRENDS_CACHE_SCHEMA_VERSION,
                "limit": PAPER_TRENDS_LIMIT,
                "items": items,
                "fetchedAt": datetime.fromtimestamp(fetched_at_epoch, timezone.utc).isoformat().replace("+00:00", "Z"),
                "fetchedAtEpoch": fetched_at_epoch,
                "sources": source_status,
                "stale": False,
                "cache": "refreshed",
                "degradedDiversity": max(selected_source_counts.values(), default=0) > 4,
                "incomplete": len(items) < PAPER_TRENDS_LIMIT,
                "search": {key: search[key] for key in ("mode", "topic", "query", "label", "identity")},
                "searchIdentity": search["identity"],
                "preferenceRevision": preference_revision,
                "preferenceAvailable": preference_available,
            }
            if payload["incomplete"]:
                payload["warning"] = f"只找到 {len(items)} 条明确匹配“{search['label']}”的结果，没有用无关内容凑数。"
            with _CACHE_LOCK:
                if not isinstance(_MEMORY_CACHE, dict) or "items" in _MEMORY_CACHE:
                    _MEMORY_CACHE = {}
                _MEMORY_CACHE[cache_key] = copy.deepcopy(payload)
                try:
                    _write_disk_cache(payload, cache_key)
                except OSError:
                    pass
            return payload

        with _CACHE_LOCK:
            stale = _cached_payload(search, preference_revision)
        if stale:
            stale["ok"] = True
            stale["stale"] = True
            stale["cache"] = "stale"
            stale["sources"] = source_status
            stale["warning"] = f"部分或全部来源暂时不可用，显示上次同一条件“{search['label']}”的结果。"
            return stale
        return {
            "ok": False,
            "items": [],
            "sources": source_status,
            "search": {key: search[key] for key in ("mode", "topic", "query", "label", "identity")},
            "error": (
                f"暂时没有找到与“{search['label']}”明确相关的可靠结果，请换个关键词或稍后重试。"
                if search["mode"] != "recommended"
                else "暂时无法凑齐 20 条可靠结果，请稍后重试。"
            ),
        }
