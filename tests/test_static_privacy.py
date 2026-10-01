import http.client
import json
import os
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class QuietWorkbenchHandler(server.WorkbenchHandler):
    def log_message(self, *_args):
        pass


class StaticPrivacyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "workbench"
        self.root.mkdir()
        self.outside = Path(self.temporary.name) / "outside"
        self.outside.mkdir()
        for name in server.WorkbenchHandler.PUBLIC_STATIC_PATHS:
            self.write_file(name, f"public test fixture: {name}".encode())
        for name in (
            ".git/config", ".cache/token-stats-details.sqlite",
            ".review-shots/private.png", "_backups/accounting.json",
            "server.py", "paper_trends.py", "AGENTS.md", ".env",
            "docs/private.md", "tests/private.py", "api/accounting-storage",
            "src/secret.js", "src/modules/health/README.md",
            "assets/icons/ai-workbench-soft-v2-source.png",
            "assets/icons/ai-workbench-soft-v2-generation.json",
            "assets/icons/private.png", "index.html:secret",
        ):
            # An NTFS alternate data stream cannot be created portably. The URL
            # is still tested below on every platform.
            if ":" not in name:
                self.write_file(name, b"PRIVATE_TEST_SENTINEL")
        self.patch_root = patch.object(server, "ROOT", self.root)
        self.patch_root.start()
        self.addCleanup(self.patch_root.stop)
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), QuietWorkbenchHandler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)

    def close_server(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    def write_file(self, name, body):
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        return target

    def request(self, path, method="GET", headers=()):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.putrequest(method, path, skip_host=True)
            for key, value in headers or (("Host", f"127.0.0.1:{self.port}"),):
                connection.putheader(key, value)
            connection.endheaders()
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def assert_not_public(self, path):
        for method in ("GET", "HEAD"):
            with self.subTest(path=path, method=method):
                status, _headers, body = self.request(path, method)
                self.assertEqual(status, 404)
                self.assertNotIn(b"PRIVATE_TEST_SENTINEL", body)
                if method == "HEAD":
                    self.assertEqual(body, b"")

    def test_frontend_files_and_head_remain_available(self):
        for path in (
            "/", "/index.html", "/manifest.webmanifest?v=test",
            "/src/app.js?v=test", "/src/styles.css",
            "/src/modules/papers/papers.js",
            "/src/modules/token-stats/activity-heatmap.js",
            "/src/modules/token-stats/token-stats.css",
            "/assets/icons/ai-workbench-soft-v2.ico",
            "/assets/icons/ai-workbench-soft-v2-192.png",
        ):
            with self.subTest(path=path):
                status, headers, body = self.request(path)
                self.assertEqual(status, 200)
                self.assertTrue(body.startswith(b"public test fixture:"))
                head_status, head_headers, head_body = self.request(path, "HEAD")
                self.assertEqual(head_status, 200)
                self.assertEqual(head_headers["Content-Length"], headers["Content-Length"])
                self.assertEqual(head_body, b"")

    def test_current_application_modules_are_all_registered(self):
        project_root = Path(server.__file__).resolve().parent
        for path in (project_root / "src").rglob("*"):
            if path.suffix in {".js", ".css"}:
                relative = path.relative_to(project_root).as_posix()
                with self.subTest(module=relative):
                    self.assertIn(relative, server.WorkbenchHandler.PUBLIC_STATIC_PATHS)
                    self.assertEqual(self.request("/" + relative)[0], 200)

    def test_private_files_unknown_assets_and_directory_lists_are_denied(self):
        for path in (
            "/.git/config", "/.git/", "/.cache/token-stats-details.sqlite",
            "/.cache/", "/.review-shots/private.png", "/.review-shots/",
            "/_backups/accounting.json", "/_backups/", "/server.py",
            "/paper_trends.py", "/AGENTS.md", "/.env", "/docs/private.md",
            "/tests/private.py", "/src/", "/src/secret.js",
            "/src/modules/health/README.md", "/assets/", "/assets/icons/",
            "/assets/icons/private.png", "/assets/icons/ai-workbench-soft-v2-source.png",
            "/assets/icons/ai-workbench-soft-v2-generation.json", "/missing.js",
            "/api/accounting-storage/extra", "/api/unknown",
        ):
            self.assert_not_public(path)

    def test_encoded_traversal_and_windows_path_aliases_are_denied(self):
        for path in (
            "/src/../server.py", "/src/%2e%2e/server.py",
            "/src/%2E%2E%2Fserver.py", "/%2egit/config", "/%2Ecache/token-stats-details.sqlite",
            "/src/%252e%252e/server.py", "/src/..%5cserver.py",
            "/src\\..\\server.py", "/C:/server.py", "/D:%5cserver.py",
            "/src/app.js:secret", "/src/app.js%3asecret", "/src/app.js%00",
            "/src/app.js.", "/src/app.js%20", "/src/app.js/",
            "/src//app.js", "/src/./app.js", "/src/x/../app.js",
            "/src/%ffapp.js", "/src/%zzapp.js", "/index.html#private",
            "http://evil.example/index.html",
        ):
            self.assert_not_public(path)

    def test_api_head_cannot_fall_back_to_files_or_scan_personal_data(self):
        with patch.object(server, "scan_papers") as scanner:
            for path in ("/api/papers", "/api/accounting-storage", "/api/token-stats/details"):
                self.assertEqual(self.request(path, "HEAD")[0], 404)
            scanner.assert_not_called()

    def test_loopback_hosts_and_same_origin_requests_still_work(self):
        for host in (f"127.0.0.1:{self.port}", f"localhost:{self.port}"):
            for extra in ((), (("Origin", "http://" + host),), (("Sec-Fetch-Site", "none"),)):
                with self.subTest(host=host, extra=extra):
                    self.assertEqual(self.request("/", headers=(("Host", host), *extra))[0], 200)
        with (
            patch.object(server, "load_vaults", return_value=[]),
            patch.object(server, "scan_papers", return_value=[{"id": "mock-only"}]),
        ):
            status, _headers, body = self.request("/api/papers")
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body), {"vaults": [], "papers": [{"id": "mock-only"}]})

    def test_untrusted_hosts_origins_and_fetch_site_are_rejected_before_routing(self):
        host = f"127.0.0.1:{self.port}"
        invalid_headers = (
            (("Host", f"evil.example:{self.port}"),),
            (("Host", "127.0.0.1:1"),),
            (("Host", "localhost"),),
            (("Host", f"127.0.0.1:{self.port}.evil.example"),),
            (("Host", host), ("Host", host)),
            (("Host", host), ("Origin", "https://evil.example")),
            (("Host", host), ("Origin", "null")),
            (("Host", host), ("Origin", "http://127.0.0.1:1")),
            (("Host", host), ("Origin", f"https://{host}")),
            (("Host", host), ("Origin", f"http://{host}"), ("Origin", f"http://{host}")),
            (("Host", host), ("Sec-Fetch-Site", "cross-site")),
            (("Host", host), ("Sec-Fetch-Site", "same-origin"), ("Sec-Fetch-Site", "cross-site")),
        )
        with patch.object(server, "scan_papers") as scanner:
            for headers in invalid_headers:
                for method in ("GET", "HEAD", "POST", "PUT"):
                    with self.subTest(headers=headers, method=method):
                        status, _headers, body = self.request("/api/papers", method, headers)
                        self.assertEqual(status, 403)
                        if method == "HEAD":
                            self.assertEqual(body, b"")
                self.assertEqual(self.request("/index.html", headers=headers)[0], 403)
            scanner.assert_not_called()

    def make_symlink(self, link, target, directory=False):
        try:
            link.symlink_to(target, target_is_directory=directory)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"Symlink creation is unavailable: {type(exc).__name__}")

    def test_symlink_public_filename_to_external_private_file_is_denied(self):
        target = self.outside / "private.js"
        target.write_bytes(b"PRIVATE_TEST_SENTINEL")
        link = self.root / "src/app.js"
        link.unlink()
        self.make_symlink(link, target)
        self.assert_not_public("/src/app.js")

    def test_symlink_public_filename_to_internal_private_file_is_denied(self):
        link = self.root / "src/app.js"
        link.unlink()
        self.make_symlink(link, self.root / "server.py")
        self.assert_not_public("/src/app.js")

    def test_symlink_directory_component_is_denied(self):
        original = self.root / "src"
        original.rename(self.root / "private-source")
        self.make_symlink(original, self.root / "private-source", directory=True)
        self.assert_not_public("/src/app.js")

    @unittest.skipUnless(os.name == "nt", "Windows reparse point coverage")
    def test_windows_junction_directory_component_is_denied(self):
        original = self.root / "src"
        target = self.root / "private-source"
        original.rename(target)
        # mklink is only used to create a junction in this temporary test root.
        result = subprocess.run(
            ["cmd", "/d", "/c", "mklink", "/J", str(original), str(target)],
            capture_output=True, check=False,
        )
        if result.returncode:
            self.skipTest("Junction creation is unavailable")
        try:
            self.assert_not_public("/src/app.js")
        finally:
            original.rmdir()


if __name__ == "__main__":
    unittest.main()
