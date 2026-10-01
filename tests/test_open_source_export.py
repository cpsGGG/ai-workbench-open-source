"""Exercise the release boundary with private canaries in temporary projects."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile
import zlib


EXPORTER_PATH = Path(__file__).resolve().parents[1] / "scripts" / "export-open-source.py"
SPEC = importlib.util.spec_from_file_location("workbench_release_export", EXPORTER_PATH)
exporter = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = exporter
SPEC.loader.exec_module(exporter)


def png_chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff)


def small_png(*metadata: bytes) -> bytes:
    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return exporter.PNG_SIGNATURE + png_chunk(b"IHDR", header) + b"".join(metadata) + png_chunk(b"IDAT", zlib.compress(b"\0\0\0\0")) + png_chunk(b"IEND", b"")


class OpenSourceExportTests(unittest.TestCase):
    def test_license_is_supported_without_allowing_unknown_extensionless_files(self):
        self.assertEqual(exporter.scan_file("LICENSE", b"MIT License\n"), [])
        with self.assertRaises(exporter.ExportError):
            exporter.scan_file("credentials", b"plain text\n")

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "scripts").mkdir()
        (self.root / "src").mkdir()
        (self.root / "src" / "app.js").write_text("export const title = 'Workbench';\n", encoding="utf-8")
        self.names = ["src/app.js", exporter.ALLOWLIST]
        self.write_allowlist(self.names)

    def write_allowlist(self, names):
        (self.root / exporter.ALLOWLIST).write_text(json.dumps({"schema_version": 1, "files": names}), encoding="utf-8")

    def assert_blocked(self, category):
        with self.assertRaises(exporter.ExportError) as raised:
            exporter.export_project(self.root, ".release/test-release")
        self.assertIn(category, str(raised.exception))
        self.assertFalse((self.root / ".release").exists(), "failed preflight must write nothing")
        return str(raised.exception)

    def test_private_canaries_are_excluded_and_originals_unchanged(self):
        private = {
            ".cache/user-data.json": b"private-accounting-canary",
            ".git/config": b"private-git-canary",
            "AGENTS.md": b"private-user-instructions-canary",
            "_backups/private.json": b"private-backup-canary",
            "docs/claude-token-cache-storage-guide.html": b"private-statistics-canary",
            "assets/icons/unused-generation.json": b"private-image-prompt-canary",
        }
        for name, value in private.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(value)
        result = exporter.export_project(self.root, ".release/test-release")
        with zipfile.ZipFile(result / "source.zip") as archive:
            all_exported = b"".join(archive.read(name) for name in archive.namelist())
            self.assertEqual(set(archive.namelist()), {*self.names, exporter.MANIFEST})
            for value in private.values():
                self.assertNotIn(value, all_exported)
        for name, value in private.items():
            self.assertEqual((self.root / name).read_bytes(), value)

    def test_manifest_and_snapshot_and_zip_sha256_agree(self):
        result = exporter.export_project(self.root, ".release/test-release")
        with zipfile.ZipFile(result / "source.zip") as archive:
            manifest = json.loads(archive.read(exporter.MANIFEST))
            self.assertEqual([item["path"] for item in manifest["files"]], sorted(self.names))
            for item in manifest["files"]:
                zipped = archive.read(item["path"])
                self.assertEqual(zipped, (result / "snapshot" / item["path"]).read_bytes())
                self.assertEqual(item["bytes"], len(zipped))
                self.assertEqual(item["sha256"], hashlib.sha256(zipped).hexdigest())
                self.assertEqual(archive.getinfo(item["path"]).date_time, (1980, 1, 1, 0, 0, 0))

    def test_missing_required_file_writes_nothing(self):
        self.write_allowlist([*self.names, "README.md"])
        self.assert_blocked("required-path-missing")

    def test_directory_allowlist_is_rejected(self):
        self.write_allowlist(["src"])
        self.assert_blocked("not-an-unlinked-regular-file")

    def test_nested_allowlist_paths_are_checked(self):
        nested = self.root / "src" / "nested"
        nested.mkdir()
        (nested / "file.js").write_text("safe", encoding="utf-8")
        self.write_allowlist(["src/nested/file.js"])
        destination, files = exporter.preflight(self.root)
        self.assertEqual(files, {"src/nested/file.js": b"safe"})
        self.assertEqual(destination.parent, self.root / ".release")
        self.assertFalse(destination.exists())

    def test_traversal_absolute_backslashes_globs_and_duplicates_rejected(self):
        drive = "C" + ":"
        for value in ("../private.txt", "src/../../private.txt", "src/../app.js", "/private.txt", drive + "/private.txt", drive + "private.txt", "src\\app.js", "src/*", "src//app.js", "src/app.js.", "src/app.js ", "src/app.js\x7f"):
            with self.subTest(value=value):
                self.write_allowlist([value])
                self.assert_blocked("allowlist-path")
        self.write_allowlist(["src/app.js", "SRC/APP.JS"])
        self.assert_blocked("duplicate-allowlist-path")

    def test_forbidden_paths_cannot_be_allowlisted(self):
        for value in (".git/config", ".cache/data.json", "_backups/private.txt", "AGENTS.md", "agents.md", "docs/claude-token-cache-storage-guide.html", "DOCS/CLAUDE-TOKEN-CACHE-STORAGE-GUIDE.HTML", "assets/icon-source.png", "assets/icon-generation.json", "assets/ICON-GENERATION.JSON"):
            with self.subTest(value=value):
                self.write_allowlist([value])
                self.assert_blocked("forbidden-")

    def test_symlink_file_and_nested_directory_rejected(self):
        target = self.root / "outside.js"
        target.write_text("private", encoding="utf-8")
        link = self.root / "src" / "link.js"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("this platform/account does not support symlink creation")
        self.write_allowlist(["src/link.js"])
        self.assert_blocked("symlink-or-reparse-point")
        link.unlink()
        directory_link = self.root / "src" / "nested"
        directory_link.symlink_to(self.root, target_is_directory=True)
        self.write_allowlist(["src/nested/outside.js"])
        self.assert_blocked("symlink-or-reparse-point")

    @unittest.skipUnless(os.name == "nt", "Windows junction regression")
    def test_windows_junction_directory_rejected(self):
        junction = self.root / "src" / "junction"
        target = self.root / "target"
        target.mkdir()
        (target / "private.js").write_text("private", encoding="utf-8")
        # Literal validated temporary paths; no enumeration or deletion commands.
        command = subprocess.run(["cmd", "/d", "/c", "mklink", "/J", str(junction), str(target)], capture_output=True)
        if command.returncode:
            self.skipTest("junction creation unavailable")
        self.write_allowlist(["src/junction/private.js"])
        self.assert_blocked("symlink-or-reparse-point")
        junction.rmdir()

    def test_hardlink_rejected(self):
        source = self.root / "src" / "app.js"
        try:
            os.link(source, self.root / "second.js")
        except OSError:
            self.skipTest("hard links unavailable")
        self.assert_blocked("not-an-unlinked-regular-file")

    def test_secret_rejected_without_zip_or_value_in_diagnostic(self):
        credential = "sk-" + "a9B8c7D6" * 5
        (self.root / "src" / "app.js").write_text("// safe line\nconst credential = '" + credential + "';\n", encoding="utf-8")
        message = self.assert_blocked("api-key")
        self.assertIn("src/app.js:2:", message)
        self.assertNotIn(credential, message)

    def test_literal_credential_and_html_attribute_boundary(self):
        credential = "a9B8c7D6" * 4
        value = "api_key='" + credential + "'"
        (self.root / "src" / "app.js").write_text(value, encoding="utf-8")
        self.assert_blocked("literal-credential")
        (self.root / "src" / "app.js").write_text('data-accounting-view-secret="${escapeAttr(entry.id)}"', encoding="utf-8")
        exporter.preflight(self.root)

    def test_private_key_and_email_and_user_path_rejected(self):
        fixtures = [
            ("-----BEGIN " + "RSA PRIVATE KEY-----", "private-key"),
            ("alice" + "@" + "private.invalid", "email-address"),
            ("C" + ":/Users/" + "private-person" + "/notes", "personal-user-path"),
            ("D" + ":/" + "private-person" + "/notes", "absolute-local-path"),
        ]
        for value, category in fixtures:
            with self.subTest(category=category):
                (self.root / "src" / "app.js").write_text(value, encoding="utf-8")
                message = self.assert_blocked(category)
                self.assertNotIn(value, message)

    def test_exact_synthetic_fixtures_allowed_without_general_test_bypass(self):
        synthetic = "user@example.com\nfixture@example.invalid\nfixture@example.test\nC:/Users/test/project\nC:/Users/tester/project\nC:/Users/example/WorkBuddy/demo\n"
        (self.root / "src" / "app.js").write_text(synthetic, encoding="utf-8")
        exporter.preflight(self.root)
        (self.root / "src" / "app.js").write_text("other" + "@example.com", encoding="utf-8")
        self.assert_blocked("email-address")

    def test_existing_output_never_overwritten(self):
        destination = self.root / ".release" / "test-release"
        destination.mkdir(parents=True)
        marker = destination / "source.zip"
        marker.write_bytes(b"existing-release")
        with self.assertRaisesRegex(exporter.ExportError, "output-already-exists"):
            exporter.export_project(self.root, destination)
        self.assertEqual(marker.read_bytes(), b"existing-release")

    def test_output_escape_nested_path_and_reverse_traversal_rejected(self):
        for output in ("../outside", ".release/nested/output", ".release/a/../../.release/output", "src/output"):
            with self.subTest(output=output):
                with self.assertRaises(exporter.ExportError):
                    exporter.export_project(self.root, output)
        self.assertFalse((self.root / ".release").exists())

    def test_output_symlink_rejected(self):
        release = self.root / ".release"
        try:
            release.symlink_to(self.root, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("this platform/account does not support symlink creation")
        with self.assertRaisesRegex(exporter.ExportError, "symlink-or-reparse-point"):
            exporter.export_project(self.root, ".release/output")
        self.assertFalse((self.root / "output").exists())

    def test_png_metadata_and_embedded_ico_metadata_are_rejected(self):
        for kind in (b"tEXt", b"zTXt", b"iTXt", b"eXIf"):
            with self.subTest(kind=kind):
                image = small_png(png_chunk(kind, b"private-canary"))
                (self.root / "icon.png").write_bytes(image)
                self.write_allowlist(["icon.png"])
                self.assert_blocked("image-text-or-exif-metadata")
        image = small_png(png_chunk(b"eXIf", b"private-canary"))
        directory = struct.pack("<HHH", 0, 1, 1) + struct.pack("<BBBBHHII", 1, 1, 0, 0, 1, 32, len(image), 22)
        (self.root / "icon.ico").write_bytes(directory + image)
        self.write_allowlist(["icon.ico"])
        self.assert_blocked("image-text-or-exif-metadata")

    def test_clean_png_allowed_and_trailing_payload_rejected(self):
        (self.root / "icon.png").write_bytes(small_png())
        self.write_allowlist(["icon.png"])
        exporter.preflight(self.root)
        (self.root / "icon.png").write_bytes(small_png() + b"private-canary")
        self.assert_blocked("png-trailing-payload")

    def test_ico_dib_and_unreferenced_payload_rejected(self):
        image = struct.pack("<I", 40) + b"\0" * 36 + b"private-canary"
        directory = struct.pack("<HHH", 0, 1, 1) + struct.pack("<BBBBHHII", 1, 1, 0, 0, 1, 32, len(image), 22)
        (self.root / "icon.ico").write_bytes(directory + image)
        self.write_allowlist(["icon.ico"])
        self.assert_blocked("unsupported-ico-bitmap-metadata")

    def test_all_files_scanned_before_writing(self):
        (self.root / "zzz.txt").write_text("token='" + "sk-" + "c0D1e2F3" * 5 + "'", encoding="utf-8")
        self.write_allowlist([*self.names, "zzz.txt"])
        self.assert_blocked("api-key")


if __name__ == "__main__":
    unittest.main()
