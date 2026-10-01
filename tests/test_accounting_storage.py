import io
import hashlib
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import server


def sample_entry(**overrides):
    value = {
        "id": "purchase:test-1",
        "purchasedOn": "2026-08-13",
        "category": "日抛 Plus",
        "quantity": 2,
        "unitPriceCents": 850,
        "totalPriceCents": 1700,
        "merchant": "商家 A",
        "note": "",
        "hasCredential": True,
        "authFiles": [],
        "importFingerprint": "",
        "createdAt": "2026-08-13T01:00:00+00:00",
        "updatedAt": "2026-08-13T01:00:00+00:00",
        "deletedAt": "",
    }
    value.update(overrides)
    return value


class AccountingStorageTests(unittest.TestCase):
    def test_validate_uses_integer_cents_and_fixed_categories(self):
        normalized = server.validate_accounting_storage_entries([sample_entry()])
        self.assertEqual(normalized[0]["totalPriceCents"], 1700)
        self.assertNotIn("credential", normalized[0])
        with self.assertRaisesRegex(ValueError, "invalid_accounting_category"):
            server.validate_accounting_storage_entries([sample_entry(category="Plus")])
        with self.assertRaisesRegex(ValueError, "invalid_accounting_unit_price"):
            server.validate_accounting_storage_entries([sample_entry(unitPriceCents=8.5)])
        fingerprint = "a" * 64
        self.assertEqual(
            server.validate_accounting_storage_entries([sample_entry(importFingerprint=fingerprint)])[0]["importFingerprint"],
            fingerprint,
        )
        with self.assertRaisesRegex(ValueError, "invalid_accounting_import_fingerprint"):
            server.validate_accounting_storage_entries([sample_entry(importFingerprint="not-a-sha256")])

    def test_write_requires_matching_revision_and_preserves_records(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "accounting.json"
            with patch.object(server, "ACCOUNTING_STORAGE_PATH", path), patch.object(server, "ACCOUNTING_STORAGE_DIR", path.parent):
                first = server.write_accounting_storage_state([sample_entry()], 0)
                self.assertEqual(first["revision"], 1)
                with self.assertRaises(server.AccountingStorageRevisionConflict):
                    server.write_accounting_storage_state([], 0)
                loaded = server.read_accounting_storage_state()
                self.assertEqual(len(loaded["entries"]), 1)
                self.assertEqual(loaded["revision"], 1)

    def test_secret_is_not_plaintext_on_disk(self):
        with tempfile.TemporaryDirectory() as folder:
            secret_dir = Path(folder)
            with patch.object(server, "ACCOUNTING_SECRET_DIR", secret_dir):
                server.write_accounting_secret("purchase:test-1", "user@example.com / secret")
                path = server.accounting_secret_path("purchase:test-1")
                if server.os.name == "nt":
                    self.assertNotIn(b"secret", path.read_bytes())
                self.assertEqual(server.read_accounting_secret("purchase:test-1"), "user@example.com / secret")

    def test_auth_json_is_validated_and_encrypted_without_plaintext_temp_file(self):
        payload = b'{"access_token":"test-only-secret"}'
        with tempfile.TemporaryDirectory() as folder:
            auth_dir = Path(folder)
            with patch.object(server, "ACCOUNTING_AUTH_DIR", auth_dir):
                stored = server.store_accounting_auth_stream(io.BytesIO(payload), len(payload))
                digest = stored["url"].removeprefix("/api/accounting-auth/")
                path = auth_dir / f"{digest}.json.dpapi"
                self.assertTrue(path.is_file())
                self.assertEqual(list(auth_dir.glob(".*.tmp")), [])
                if server.os.name == "nt":
                    self.assertNotIn(b"test-only-secret", path.read_bytes())
                self.assertEqual(server.accounting_unprotect_bytes(path.read_bytes()), payload)

                with self.assertRaisesRegex(ValueError, "invalid_accounting_auth_json"):
                    server.store_accounting_auth_stream(io.BytesIO(b"not-json"), 8)

    def test_deleting_entry_removes_its_secret(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            storage_path = root / "accounting.json"
            with (
                patch.object(server, "ACCOUNTING_STORAGE_PATH", storage_path),
                patch.object(server, "ACCOUNTING_STORAGE_DIR", root),
                patch.object(server, "ACCOUNTING_SECRET_DIR", root / "secrets"),
            ):
                server.write_accounting_secret("purchase:test-1", "secret")
                server.write_accounting_storage_state([sample_entry()], 0)
                deleted = sample_entry(
                    updatedAt="2026-08-13T02:00:00+00:00",
                    deletedAt="2026-08-13T02:00:00+00:00",
                )
                server.write_accounting_storage_state([deleted], 1)
                self.assertFalse(server.accounting_secret_path("purchase:test-1").exists())

    def test_cpa_candidates_only_return_masked_metadata_for_valid_root_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = root / "config.yaml"
            auths = root / "auths"
            auths.mkdir()
            config.write_text('auth-dir: "auths"\n', encoding="utf-8")
            valid = auths / "codex-1234567890-plus.json"
            valid.write_text(
                json.dumps({"type": "codex", "access_token": "access", "refresh_token": "refresh"}),
                encoding="utf-8",
            )
            (auths / "codex-backup.cpa.2026-08-13_01-02-03.json").write_text(valid.read_text(encoding="utf-8"), encoding="utf-8")
            (auths / "other.json").write_text(valid.read_text(encoding="utf-8"), encoding="utf-8")
            (auths / "codex-missing-refresh.json").write_text(
                json.dumps({"type": "codex", "access_token": "access"}), encoding="utf-8"
            )
            nested = auths / "nested"
            nested.mkdir()
            (nested / "codex-nested.json").write_text(valid.read_text(encoding="utf-8"), encoding="utf-8")

            with (
                patch.object(server, "accounting_cpa_config_paths", return_value=[config.resolve()]),
                patch.object(server, "read_accounting_storage_state", return_value={"entries": []}),
            ):
                payload = server.public_accounting_cpa_auth_candidates()

            self.assertTrue(payload["ok"])
            self.assertEqual(len(payload["candidates"]), 1)
            candidate = payload["candidates"][0]
            self.assertEqual(set(candidate), {"id", "name", "source", "modifiedAt", "size"})
            self.assertNotEqual(candidate["name"], valid.name)
            self.assertNotIn("1234567890", candidate["name"])
            self.assertNotIn(str(root), json.dumps(candidate))
            self.assertNotIn("access", json.dumps(candidate))

    def test_cpa_import_rejects_changed_binding_and_reuses_content_digest(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = root / "config.yaml"
            auths = root / "auths"
            stored = root / "stored"
            auths.mkdir()
            config.write_text("auth-dir: auths\n", encoding="utf-8")
            path = auths / "codex-account-plus.json"
            original = json.dumps({"type": "codex", "access_token": "one", "refresh_token": "two"}).encode()
            path.write_bytes(original)

            with (
                patch.object(server, "accounting_cpa_config_paths", return_value=[config.resolve()]),
                patch.object(server, "ACCOUNTING_AUTH_DIR", stored),
                patch.object(server, "read_accounting_storage_state", return_value={"entries": []}),
            ):
                candidate_id = server.public_accounting_cpa_auth_candidates()["candidates"][0]["id"]
                first = server.import_accounting_cpa_auth_candidate(candidate_id)
                second = server.import_accounting_cpa_auth_candidate(candidate_id)
                self.assertEqual(first["url"], second["url"])
                self.assertEqual(len(list(stored.glob("*.json.dpapi"))), 1)
                path.write_bytes(json.dumps({"type": "codex", "access_token": "changed", "refresh_token": "two"}).encode())
                with self.assertRaisesRegex(ValueError, "accounting_auth_candidate_changed"):
                    server.import_accounting_cpa_auth_candidate(candidate_id)

    def test_cpa_import_rejects_candidate_after_it_is_bound(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            auths = root / "auths"
            stored = root / "stored"
            auths.mkdir()
            config = root / "config.yaml"
            config.write_text("auth-dir: auths\n", encoding="utf-8")
            path = auths / "codex-new-plus.json"
            data = json.dumps({"type": "codex", "access_token": "one", "refresh_token": "two"}).encode()
            path.write_bytes(data)
            digest = hashlib.sha256(data).hexdigest()
            bound = sample_entry(authFiles=[{
                "id": "auth:bound",
                "name": "auth.json",
                "size": len(data),
                "url": f"/api/accounting-auth/{digest}",
            }])
            with (
                patch.object(server, "accounting_cpa_config_paths", return_value=[config.resolve()]),
                patch.object(server, "ACCOUNTING_AUTH_DIR", stored),
                patch.object(server, "read_accounting_storage_state", return_value={"entries": []}),
            ):
                candidate_id = server.public_accounting_cpa_auth_candidates()["candidates"][0]["id"]
            with (
                patch.object(server, "accounting_cpa_config_paths", return_value=[config.resolve()]),
                patch.object(server, "ACCOUNTING_AUTH_DIR", stored),
                patch.object(server, "read_accounting_storage_state", return_value={"entries": [bound]}),
            ):
                with self.assertRaisesRegex(ValueError, "accounting_auth_candidate_changed"):
                    server.import_accounting_cpa_auth_candidate(candidate_id)

    def test_active_accounting_entries_cannot_share_one_auth(self):
        url = "/api/accounting-auth/" + "a" * 64
        attachment = {"id": "auth:shared", "name": "auth.json", "size": 10, "url": url}
        with self.assertRaisesRegex(ValueError, "accounting_auth_already_bound"):
            server.validate_accounting_storage_entries([
                sample_entry(id="purchase:first", authFiles=[attachment]),
                sample_entry(id="purchase:second", authFiles=[{**attachment, "id": "auth:shared-2"}]),
            ])

    def test_cpa_candidates_hide_old_and_already_used_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            auths = root / "auths"
            auths.mkdir()
            config = root / "config.yaml"
            config.write_text("auth-dir: auths\n", encoding="utf-8")
            recent = auths / "codex-recent-plus.json"
            old = auths / "codex-old-plus.json"
            recent_payload = json.dumps({"type": "codex", "access_token": "recent", "refresh_token": "refresh"}).encode()
            old_payload = json.dumps({"type": "codex", "access_token": "old", "refresh_token": "refresh"}).encode()
            recent.write_bytes(recent_payload)
            old.write_bytes(old_payload)
            old_time = time.time() - server.ACCOUNTING_CPA_RECENT_SECONDS - 60
            os.utime(old, (old_time, old_time))

            used_digest = hashlib.sha256(recent_payload).hexdigest()
            used_entry = sample_entry(authFiles=[{
                "id": "auth:used",
                "name": "auth.json",
                "size": len(recent_payload),
                "url": f"/api/accounting-auth/{used_digest}",
            }])
            with (
                patch.object(server, "accounting_cpa_config_paths", return_value=[config.resolve()]),
                patch.object(server, "read_accounting_storage_state", return_value={"entries": []}),
            ):
                self.assertEqual(len(server.public_accounting_cpa_auth_candidates()["candidates"]), 1)
            with (
                patch.object(server, "accounting_cpa_config_paths", return_value=[config.resolve()]),
                patch.object(server, "read_accounting_storage_state", return_value={"entries": [used_entry]}),
            ):
                self.assertEqual(server.public_accounting_cpa_auth_candidates()["candidates"], [])

    def test_cpa_auth_dir_cannot_escape_config_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            child = root / "app"
            outside = root / "outside"
            child.mkdir()
            outside.mkdir()
            config = child / "config.yaml"
            config.write_text("auth-dir: ../outside\n", encoding="utf-8")
            self.assertIsNone(server.accounting_cpa_auth_root(config.resolve()))


if __name__ == "__main__":
    unittest.main()
