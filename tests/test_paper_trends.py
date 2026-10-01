import copy
import json
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from unittest import mock

import paper_trends
from server import WorkbenchHandler


def candidate(index, *, source_key="arxiv", url=None, title=None, days_ago=0, popularity=0):
    item = paper_trends._new_item(
        title=title or f"AI result {index}",
        url=url or f"https://arxiv.org/abs/2608.{index:05d}",
        source="arXiv" if source_key == "arxiv" else source_key,
        source_key=source_key,
        published_at=time.strftime("%Y-%m-%d", time.gmtime(time.time() - days_ago * 86400)),
        summary=f"Summary {index}",
        item_type="论文",
        popularity=popularity,
        hot_reason="test signal",
    )
    assert item is not None
    return item


class PaperTrendsTests(unittest.TestCase):
    def setUp(self):
        paper_trends._MEMORY_CACHE = None
        paper_trends._IMAGE_MEMORY_CACHE.clear()
        paper_trends._IMAGE_NEGATIVE_CACHE.clear()

    def tearDown(self):
        paper_trends._MEMORY_CACHE = None

    def test_rejects_unsafe_links_and_strips_html(self):
        self.assertIsNone(
            paper_trends._new_item(
                title="Unsafe",
                url="javascript:alert(1)",
                source="test",
                source_key="test",
                published_at="2026-08-12",
                summary="x",
                item_type="官方文章",
            )
        )
        item = paper_trends._new_item(
            title="<b>Safe title</b>",
            url="https://example.com/item#fragment",
            source="test",
            source_key="test",
            published_at="2026-08-12",
            summary="<script>bad()</script><p>Useful summary</p>",
            item_type="官方文章",
        )
        self.assertEqual(item["title"], "Safe title")
        self.assertEqual(item["url"], "https://example.com/item")
        self.assertNotIn("<", item["summary"])

    def test_topics_and_custom_query_are_normalized_and_isolated(self):
        world_topic = paper_trends._normalize_topic_search(query="  世界模型  ")
        self.assertEqual(world_topic["identity"], "topic:world-models")
        custom = paper_trends._normalize_topic_search(query="  Multimodal   Agents ")
        equivalent = paper_trends._normalize_topic_search(query="multimodal agents")
        self.assertEqual(custom["identity"], equivalent["identity"])
        robot = paper_trends._normalize_topic_search(topic="robotics")
        self.assertNotEqual(paper_trends._cache_key(world_topic, 0), paper_trends._cache_key(robot, 0))
        with self.assertRaisesRegex(ValueError, "query_contains_unsupported_characters"):
            paper_trends._normalize_topic_search(query="https://evil.example/a")

    def test_query_relevance_gate_does_not_add_unrelated_fillers(self):
        search = paper_trends._normalize_topic_search(query="quantum banana")
        relevant = candidate(1, title="Quantum banana reasoning")
        unrelated = [candidate(index, title=f"Generic AI item {index}") for index in range(2, 30)]
        ranked = paper_trends._merge_and_rank([relevant, *unrelated], [], search, [])
        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0]["title"], relevant["title"])
        self.assertGreater(ranked[0]["relevanceScore"], 0)

    def test_likes_use_temp_file_and_revision_conflicts_preserve_state(self):
        with tempfile.TemporaryDirectory() as directory:
            temp_path = paper_trends.Path(directory) / "likes.json"
            item_a = candidate(1, title="World Model A")
            item_b = candidate(2, title="Robot B")
            for item in (item_a, item_b):
                item["id"] = paper_trends._stable_item_id(item["url"])
                item["topics"] = paper_trends._item_topics(item)
            with mock.patch.object(paper_trends, "PAPER_TREND_LIKES_PATH", temp_path):
                state_a = paper_trends.write_paper_trend_like_intent(item_a, True, 0)
                self.assertEqual(state_a["revision"], 1)
                with self.assertRaises(paper_trends.PaperTrendLikesRevisionConflict) as conflict:
                    paper_trends.write_paper_trend_like_intent(item_b, True, 0)
                current = conflict.exception.state
                self.assertEqual([item["id"] for item in current["likes"]], [item_a["id"]])
                state_b = paper_trends.write_paper_trend_like_intent(item_b, True, current["revision"])
                state_c = paper_trends.write_paper_trend_like_intent(item_a, False, state_b["revision"])
                self.assertEqual([item["id"] for item in state_c["likes"]], [item_b["id"]])
                persisted = json.loads(temp_path.read_text(encoding="utf-8"))
                self.assertEqual(persisted["revision"], state_c["revision"])

    def test_like_id_must_match_canonical_url(self):
        item = candidate(1)
        item["id"] = "0" * 16
        with self.assertRaisesRegex(ValueError, "liked_item_id_does_not_match_url"):
            paper_trends._validate_liked_item(item)

    def test_cache_read_validates_requested_query_context(self):
        search_a = paper_trends._normalize_topic_search(topic="robotics")
        search_b = paper_trends._normalize_topic_search(topic="world-models")
        payload = {
            "schemaVersion": paper_trends.PAPER_TRENDS_CACHE_SCHEMA_VERSION,
            "limit": paper_trends.PAPER_TRENDS_LIMIT,
            "items": [candidate(1)],
            "searchIdentity": search_a["identity"],
            "preferenceRevision": 0,
        }
        with tempfile.TemporaryDirectory() as directory:
            temp_path = paper_trends.Path(directory) / "paper-trends.json"
            with mock.patch.object(paper_trends, "PAPER_TRENDS_CACHE_PATH", temp_path):
                key = paper_trends._cache_key(search_a, 0)
                paper_trends._cache_path(key).write_text(json.dumps(payload), encoding="utf-8")
                self.assertIsNotNone(paper_trends._read_disk_cache(key, search_a, 0))
                self.assertIsNone(paper_trends._read_disk_cache(key, search_b, 0))

    def test_upstream_requires_https_allowlist(self):
        with self.assertRaisesRegex(ValueError, "upstream_not_allowed"):
            paper_trends._read_upstream("http://huggingface.co/api/test", ("application/json",))
        with self.assertRaisesRegex(ValueError, "upstream_not_allowed"):
            paper_trends._read_upstream("https://evil.example/test", ("application/json",))

    def test_hugging_face_thumbnail_is_bound_to_local_proxy(self):
        payload = [{"thumbnail": "https://cdn-thumbnails.huggingface.co/social-thumbnails/papers/2608.08160.png", "paper": {
            "id": "2608.08160", "title": "Visual paper", "summary": "AI summary", "publishedAt": "2026-08-13"
        }}]
        with mock.patch.object(paper_trends, "_read_json", return_value=payload):
            item = paper_trends._fetch_hugging_face()[0]
        ranked = paper_trends._merge_and_rank([item], [])
        self.assertEqual(ranked[0]["imageUrl"], f"/api/paper-trend-image?id={ranked[0]['id']}")
        public = paper_trends.public_paper_trends_payload({"items": ranked})
        self.assertNotIn("_imageUrl", public["items"][0])
        self.assertNotIn("cdn-thumbnails", json.dumps(public))

    def test_rss_images_obey_source_specific_host_allowlist(self):
        xml = """<item xmlns:media="http://search.yahoo.com/mrss/">
          <media:thumbnail url="https://storage.googleapis.com/gweb-research2023-media/hero.png" />
          <description><![CDATA[<img src="https://evil.example/tracker.png">]]></description>
        </item>"""
        node = paper_trends.ET.fromstring(xml)
        self.assertEqual(
            paper_trends._rss_item_image(node, "google-research"),
            "https://storage.googleapis.com/gweb-research2023-media/hero.png",
        )
        self.assertEqual(paper_trends._rss_item_image(node, "openai"), "")
        self.assertEqual(paper_trends._safe_image_url("http://storage.googleapis.com/a.png", "google-research"), "")

    def test_rss_content_without_media_url_does_not_bind_feed_url(self):
        xml = """<item xmlns:content="http://purl.org/rss/1.0/modules/content/">
          <content:encoded><![CDATA[<p>Article text without an image.</p>]]></content:encoded>
          <content>Plain article body.</content>
        </item>"""
        node = paper_trends.ET.fromstring(xml)
        self.assertEqual(paper_trends._rss_item_image(node, "microsoft-research"), "")

    def test_image_proxy_resolves_only_cache_bound_item_ids(self):
        item = candidate(1, source_key="hugging-face", url="https://huggingface.co/papers/2608.00001")
        item["id"] = paper_trends._stable_item_id(item["url"])
        item["_imageUrl"] = "https://cdn-thumbnails.huggingface.co/a.png"
        paper_trends._MEMORY_CACHE = {"cache-key": {"items": [item]}}
        self.assertEqual(
            paper_trends._image_binding_for_item(item["id"]),
            (item["_imageUrl"], "hugging-face"),
        )
        with self.assertRaises(paper_trends.PaperTrendImageNotFound):
            paper_trends._image_binding_for_item("0" * 16)
        item["_imageUrl"] = "https://evil.example/a.png"
        with self.assertRaises(paper_trends.PaperTrendImageNotFound):
            paper_trends._image_binding_for_item(item["id"])

    def test_image_proxy_checks_declared_type_size_and_magic(self):
        item = candidate(2, source_key="hugging-face", url="https://huggingface.co/papers/2608.00002")
        item["id"] = paper_trends._stable_item_id(item["url"])
        item["_imageUrl"] = "https://cdn-thumbnails.huggingface.co/a.png"
        paper_trends._MEMORY_CACHE = {"cache-key": {"items": [item]}}

        class Headers:
            def __init__(self, content_type, length=None): self.content_type, self.length = content_type, length
            def get_content_type(self): return self.content_type
            def get(self, name): return self.length if name == "Content-Length" else None

        class Response:
            def __init__(self, body, content_type="image/png", length=None):
                self.body, self.headers = body, Headers(content_type, length)
            def __enter__(self): return self
            def __exit__(self, *args): return None
            def geturl(self): return item["_imageUrl"]
            def read(self, size): return self.body

        png = b"\x89PNG\r\n\x1a\n" + b"x" * 20
        with mock.patch("urllib.request.OpenerDirector.open", return_value=Response(png)):
            body, content_type = paper_trends.fetch_paper_trend_image(item["id"])
        self.assertEqual((body, content_type), (png, "image/png"))

        paper_trends._IMAGE_MEMORY_CACHE.clear()
        with mock.patch("urllib.request.OpenerDirector.open", return_value=Response(b"<svg/>", "image/svg+xml")):
            with self.assertRaises(paper_trends.PaperTrendImageFetchError):
                paper_trends.fetch_paper_trend_image(item["id"])

        paper_trends._IMAGE_NEGATIVE_CACHE.clear()
        with mock.patch("urllib.request.OpenerDirector.open", return_value=Response(png, length=str(paper_trends.PAPER_TREND_IMAGE_MAX_BODY + 1))):
            with self.assertRaises(paper_trends.PaperTrendImageFetchError):
                paper_trends.fetch_paper_trend_image(item["id"])

    def test_redirect_rejects_non_allowlisted_destination(self):
        handler = paper_trends._AllowlistedRedirectHandler()
        request = urllib.request.Request("https://openai.com/news/rss.xml")
        with self.assertRaisesRegex(urllib.error.URLError, "upstream_redirect_blocked"):
            handler.redirect_request(request, None, 302, "Found", {}, "https://evil.example/feed")

    def test_deduplicates_hugging_face_and_arxiv_versions(self):
        hf = candidate(
            1,
            source_key="hugging-face",
            url="https://huggingface.co/papers/2608.01234",
            title="One Important Paper",
            popularity=80,
        )
        arxiv = candidate(
            2,
            source_key="arxiv",
            url="https://arxiv.org/abs/2608.01234v2",
            title="One Important Paper (revised)",
        )
        ranked = paper_trends._merge_and_rank(
            [hf, arxiv] + [candidate(index) for index in range(3, 8)],
            [],
        )
        matches = [item for item in ranked if "2608.01234" in item["url"]]
        self.assertEqual(len(matches), 1)

    def test_partial_source_failure_still_returns_twenty(self):
        def fail():
            raise TimeoutError("timed out")

        source_rows = [candidate(index, source_key="hugging-face", popularity=10) for index in range(1, 9)]

        def official(source_key, source, require_ai_match=False):
            offset = list(paper_trends._OFFICIAL_SOURCE_CONFIG).index(source_key) * 10 + 100
            return [candidate(offset + index, source_key=source_key) for index in range(4)]

        patches = (
            mock.patch.object(paper_trends, "_fetch_hugging_face", return_value=source_rows),
            mock.patch.object(paper_trends, "_fetch_arxiv", side_effect=fail),
            mock.patch.object(paper_trends, "_fetch_official_feed", side_effect=official),
            mock.patch.object(paper_trends, "_fetch_hn_signals", return_value=[]),
            mock.patch.object(paper_trends, "_read_disk_cache", return_value=None),
            mock.patch.object(paper_trends, "_write_disk_cache"),
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            payload = paper_trends.fetch_paper_trends(force=True)
        self.assertTrue(payload["ok"])
        self.assertEqual(len(payload["items"]), 20)
        self.assertEqual(payload["limit"], 20)
        self.assertEqual(payload["schemaVersion"], paper_trends.PAPER_TRENDS_CACHE_SCHEMA_VERSION)
        self.assertFalse(next(source for source in payload["sources"] if source["key"] == "arxiv")["ok"])

    def test_total_failure_without_cache_is_explicit(self):
        failure = TimeoutError("timed out")
        with (
            mock.patch.object(paper_trends, "_fetch_hugging_face", side_effect=failure),
            mock.patch.object(paper_trends, "_fetch_arxiv", side_effect=failure),
            mock.patch.object(paper_trends, "_fetch_official_feed", side_effect=failure),
            mock.patch.object(paper_trends, "_fetch_hn_signals", side_effect=failure),
            mock.patch.object(paper_trends, "_read_disk_cache", return_value=None),
        ):
            payload = paper_trends.fetch_paper_trends(force=True)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["items"], [])

    def test_total_failure_keeps_last_success_as_stale(self):
        stale = {
            "ok": True,
            "schemaVersion": paper_trends.PAPER_TRENDS_CACHE_SCHEMA_VERSION,
            "limit": paper_trends.PAPER_TRENDS_LIMIT,
            "items": [candidate(index) for index in range(1, 21)],
            "fetchedAt": "2026-08-01T00:00:00Z",
            "fetchedAtEpoch": 1,
            "sources": [],
            "stale": False,
        }
        paper_trends._MEMORY_CACHE = copy.deepcopy(stale)
        failure = TimeoutError("timed out")
        with (
            mock.patch.object(paper_trends, "_fetch_hugging_face", side_effect=failure),
            mock.patch.object(paper_trends, "_fetch_arxiv", side_effect=failure),
            mock.patch.object(paper_trends, "_fetch_official_feed", side_effect=failure),
            mock.patch.object(paper_trends, "_fetch_hn_signals", side_effect=failure),
        ):
            payload = paper_trends.fetch_paper_trends(force=True)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["stale"])
        self.assertEqual(len(payload["items"]), 20)

    def test_old_five_item_cache_is_rejected(self):
        old_cache = {
            "ok": True,
            "items": [candidate(index) for index in range(1, 6)],
            "fetchedAtEpoch": time.time(),
        }
        paper_trends._MEMORY_CACHE = copy.deepcopy(old_cache)
        with mock.patch.object(paper_trends, "_read_disk_cache", return_value=old_cache):
            self.assertIsNone(paper_trends._cached_payload())

    def test_duplicate_representative_is_deterministic(self):
        hf = candidate(
            1,
            source_key="hugging-face",
            url="https://huggingface.co/papers/2608.04567",
            title="A Shared Paper",
            popularity=100,
        )
        arxiv = candidate(
            2,
            source_key="arxiv",
            url="https://arxiv.org/abs/2608.04567v2",
            title="A Shared Paper revised",
        )
        fillers = [candidate(index) for index in range(30, 55)]
        first = paper_trends._merge_and_rank([arxiv, hf, *fillers], [])
        second = paper_trends._merge_and_rank([hf, arxiv, *reversed(fillers)], [])
        first_match = next(item for item in first if "2608.04567" in item["url"])
        second_match = next(item for item in second if "2608.04567" in item["url"])
        self.assertEqual(first_match["sourceKey"], "hugging-face")
        self.assertEqual(first_match["url"], second_match["url"])

    def test_route_trust_requires_same_origin_browser_evidence(self):
        def trusted(headers):
            handler = object.__new__(WorkbenchHandler)
            handler.headers = headers
            handler.server = type("Server", (), {"server_address": ("127.0.0.1", 5173)})()
            return handler.is_trusted_paper_trends_request()

        from email.message import Message

        no_context = Message()
        no_context["Host"] = "127.0.0.1:5173"
        self.assertFalse(trusted(no_context))

        same_origin = Message()
        same_origin["Host"] = "127.0.0.1:5173"
        same_origin["Sec-Fetch-Site"] = "same-origin"
        self.assertTrue(trusted(same_origin))

        cross_site = Message()
        cross_site["Host"] = "127.0.0.1:5173"
        cross_site["Origin"] = "https://evil.example"
        self.assertFalse(trusted(cross_site))

        local_origin = Message()
        local_origin["Host"] = "localhost:5173"
        local_origin["Origin"] = "http://localhost:5173"
        self.assertTrue(trusted(local_origin))


if __name__ == "__main__":
    unittest.main()
