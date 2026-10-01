"""Export a reviewed file allowlist to a fresh, history-free local release.

This command reads only the allowlisted files. It never starts the application,
reads browser storage, initializes Git, or uploads anything. Pattern scanning is
an additional guard; a human must still review source, fixtures, and image pixels.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import stat
import struct
import sys
import uuid
import zipfile
import zlib


ROOT = Path(__file__).absolute().parents[1]
ALLOWLIST = "scripts/open-source-allowlist.json"
MANIFEST = "EXPORT_MANIFEST.json"
MAX_FILE_BYTES = 32 * 1024 * 1024
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
TEXT_SUFFIXES = {".py", ".js", ".mjs", ".css", ".html", ".md", ".txt", ".json", ".webmanifest", ".cmd", ".ps1"}
FORBIDDEN_COMPONENTS = {".git", ".cache", ".release", "__pycache__", "_backups", "backups", "data", "research-screenshots", ".review-shots", ".playwright-mcp", "node_modules"}
# Exact existing synthetic fixtures, not a blanket exemption for test files.
FAKE_EMAILS = {"user@example.com"}
FAKE_WINDOWS_USERS = {("c", "test"), ("c", "tester"), ("c", "example")}
FAKE_ABSOLUTE_PATHS = {"c:/old", "c:/current", "c:/indexed", "c:/server.py", "d:/projects/demo/", "d:/projects/%e6%bc%94%e7%a4%ba/"}
EMAIL = re.compile(r"(?<![\w.+-])[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+(?![\w.-])")
WINDOWS_USER = re.compile(r"(?i)(?<![A-Za-z])([A-Za-z]):[\\/]+Users[\\/]+([^\\/\s\"'<>]+)")
UNIX_USER = re.compile(r"(?<![A-Za-z]:)/(?:home|Users)/([^/\s\"'<>]+)")
WINDOWS_ABSOLUTE = re.compile(r"(?i)(?<![A-Za-z])([A-Za-z]):[\\/]+([^\s\"'<>|),;]+)")
SECRET_PATTERNS = (
    ("private-key", re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----")),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("github-token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b")),
    ("api-key", re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}\b")),
    ("google-api-key", re.compile(r"\bAIza[A-Za-z0-9_-]{35}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
    ("stripe-key", re.compile(r"\b(?:sk|rk)_live_[A-Za-z0-9]{16,}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    ("literal-credential", re.compile(r"(?i)(?<![\w-])[\"']?\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|secret)\b[\"']?\s*[:=]\s*[\"']([^\"'\r\n]{12,})[\"']")),
    ("bearer-token", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._-]{20,}")),
)


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    category: str

    def diagnostic(self) -> str:
        return f"{self.path}:{self.line}: {self.category}"


class ExportError(Exception):
    """A safe error that never includes source values or absolute local paths."""


def _check_components(path: Path, *, allow_missing: bool = False) -> None:
    """Check every ancestor, including Windows reparse points/junctions."""
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            if allow_missing:
                continue
            raise ExportError("required-path-missing") from None
        except OSError:
            raise ExportError("path-unreadable") from None
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            raise ExportError("symlink-or-reparse-point")


def _relative_name(value: object) -> str:
    if not isinstance(value, str) or not value or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ExportError("invalid-allowlist-path")
    win = PureWindowsPath(value)
    if "\\" in value or win.drive or win.root or value.startswith("/"):
        raise ExportError("absolute-or-backslash-allowlist-path")
    parts = value.split("/")
    if any(part in {"", ".", ".."} or part.endswith((".", " ")) or any(c in part for c in '*?:<>|') for part in parts):
        raise ExportError("traversal-or-wildcard-allowlist-path")
    if any(part.casefold() in FORBIDDEN_COMPONENTS for part in parts):
        raise ExportError("forbidden-private-or-runtime-path")
    if any(part.startswith(".") for part in parts) and value != ".gitignore":
        raise ExportError("forbidden-hidden-path")
    folded = value.casefold()
    if folded == "agents.md" or folded == "docs/claude-token-cache-storage-guide.html" or folded.endswith(("-source.png", "-source.jpg", "-source.jpeg", "-source.webp", "-generation.json")):
        raise ExportError("forbidden-private-or-generation-file")
    return str(PurePosixPath(value))


def _read_regular(root: Path, name: str) -> bytes:
    path = root.joinpath(*name.split("/"))
    _check_components(path)
    try:
        before = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink > 1:
            raise ExportError("not-an-unlinked-regular-file")
        if before.st_size > MAX_FILE_BYTES:
            raise ExportError("file-too-large")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if (before.st_dev, before.st_ino, before.st_size) != (opened.st_dev, opened.st_ino, opened.st_size):
                raise ExportError("source-changed-during-preflight")
            data = handle.read(MAX_FILE_BYTES + 1)
        _check_components(path)
        after = path.stat(follow_symlinks=False)
        if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) or len(data) != opened.st_size:
            raise ExportError("source-changed-during-preflight")
        return data
    except ExportError:
        raise
    except OSError:
        raise ExportError("file-unreadable") from None


def scan_text(name: str, text: str) -> list[Finding]:
    findings = []
    for line_number, line in enumerate(text.splitlines(), 1):
        categories = set()
        for category, pattern in SECRET_PATTERNS:
            for match in pattern.finditer(line):
                # Only exact generic placeholders are harmless assignments.
                if category == "literal-credential" and match.group(1).lower() in {"placeholder", "example.invalid", "test-password", "your-api-key", "test-only-secret"}:
                    continue
                categories.add(category)
        for match in EMAIL.finditer(line):
            email = match.group(0).lower()
            domain = email.rsplit("@", 1)[1]
            if email not in FAKE_EMAILS and domain not in {"example.invalid", "example.test"}:
                categories.add("email-address")
        for match in WINDOWS_USER.finditer(line):
            if (match.group(1).lower(), match.group(2).lower()) not in FAKE_WINDOWS_USERS:
                categories.add("personal-user-path")
        for match in UNIX_USER.finditer(line):
            if match.group(1).lower() not in {"test", "tester"}:
                categories.add("personal-user-path")
        for match in WINDOWS_ABSOLUTE.finditer(line):
            normalized = re.sub(r"[\\/]+", "/", match.group(0)).lower()
            if normalized in FAKE_ABSOLUTE_PATHS or re.match(r"^" + "c" + r":/users/(?:test|tester|example)(?:/|$)", normalized):
                continue
            if re.match(r"^" + "c" + r":/(?:windows|program files(?: \(x86\))?)(?:/|$)", normalized):
                continue
            categories.add("absolute-local-path")
        findings.extend(Finding(name, line_number, category) for category in sorted(categories))
    return findings


def _scan_png(name: str, data: bytes) -> list[Finding]:
    if not data.startswith(PNG_SIGNATURE):
        raise ExportError("invalid-png")
    offset = len(PNG_SIGNATURE)
    findings = []
    seen_header = False
    while offset < len(data):
        if offset + 12 > len(data):
            raise ExportError("invalid-png-chunk")
        length = struct.unpack_from(">I", data, offset)[0]
        kind = data[offset + 4:offset + 8]
        end = offset + length + 12
        if end > len(data):
            raise ExportError("invalid-png-chunk")
        payload = data[offset + 8:offset + 8 + length]
        expected_crc = struct.unpack_from(">I", data, offset + 8 + length)[0]
        if zlib.crc32(kind + payload) & 0xffffffff != expected_crc:
            raise ExportError("invalid-png-crc")
        if not seen_header and (kind != b"IHDR" or length != 13):
            raise ExportError("invalid-png-header")
        seen_header = True
        # Refuse text and EXIF entirely: prose/author/GPS may evade key patterns.
        if kind in {b"tEXt", b"zTXt", b"iTXt", b"eXIf", b"iCCP"}:
            findings.append(Finding(name, 1, "image-text-or-exif-metadata"))
        # Unknown ancillary chunks can contain arbitrary private payloads.
        if kind not in {b"IHDR", b"PLTE", b"IDAT", b"IEND", b"tRNS", b"cHRM", b"gAMA", b"sRGB", b"pHYs", b"sBIT", b"bKGD", b"hIST", b"tIME", b"acTL", b"fcTL", b"fdAT", b"tEXt", b"zTXt", b"iTXt", b"eXIf", b"iCCP"}:
            findings.append(Finding(name, 1, "unknown-image-metadata"))
        offset = end
        if kind == b"IEND":
            if length or offset != len(data):
                raise ExportError("png-trailing-payload")
            return findings
    raise ExportError("png-missing-end")


def _scan_ico(name: str, data: bytes) -> list[Finding]:
    if len(data) < 6:
        raise ExportError("invalid-ico")
    reserved, image_type, count = struct.unpack_from("<HHH", data)
    if reserved or image_type != 1 or not count or 6 + count * 16 > len(data):
        raise ExportError("invalid-ico")
    findings = []
    spans = []
    for index in range(count):
        length, start = struct.unpack_from("<II", data, 6 + index * 16 + 8)
        if start < 6 + count * 16 or not length or start + length > len(data):
            raise ExportError("invalid-ico-image")
        spans.append((start, start + length))
        payload = data[start:start + length]
        if payload.startswith(PNG_SIGNATURE):
            findings.extend(_scan_png(name, payload))
        else:
            # Only reviewed PNG entries are supported; DIB may hide extra data.
            raise ExportError("unsupported-ico-bitmap-metadata")
    cursor = 6 + count * 16
    for start, end in sorted(spans):
        if start != cursor:
            raise ExportError("ico-unreferenced-or-overlapping-payload")
        cursor = end
    if cursor != len(data):
        raise ExportError("ico-trailing-payload")
    return findings


def scan_file(name: str, data: bytes) -> list[Finding]:
    suffix = PurePosixPath(name).suffix.lower()
    if suffix == ".png":
        return _scan_png(name, data)
    if suffix == ".ico":
        return _scan_ico(name, data)
    if suffix not in TEXT_SUFFIXES and name not in {".gitignore", "LICENSE"}:
        raise ExportError("unsupported-file-type")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ExportError("text-file-must-be-utf8") from None
    if "\x00" in text:
        raise ExportError("binary-payload-in-text-file")
    return scan_text(name, text)


def _output_path(root: Path, output: str | Path | None) -> Path:
    if output is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = root / ".release" / f"open-source-{stamp}-{uuid.uuid4().hex[:8]}"
    else:
        path = Path(output)
        if ".." in path.parts:
            raise ExportError("output-traversal-not-allowed")
        if not path.is_absolute():
            path = root / path
        path = Path(os.path.abspath(path))
    if path.parent != root / ".release" or path.name in {"", ".", ".."}:
        raise ExportError("output-must-be-a-fresh-direct-child-of-release")
    _check_components(path, allow_missing=True)
    if path.exists():
        raise ExportError("output-already-exists")
    return path


def preflight(root: Path, output: str | Path | None = None) -> tuple[Path, dict[str, bytes]]:
    root = Path(os.path.abspath(root))
    _check_components(root)
    destination = _output_path(root, output)
    raw_allowlist = _read_regular(root, ALLOWLIST)
    try:
        config = json.loads(raw_allowlist.decode("utf-8-sig"))
    except (UnicodeError, ValueError):
        raise ExportError("invalid-allowlist-json") from None
    if not isinstance(config, dict) or set(config) != {"schema_version", "files"} or config["schema_version"] != 1 or not isinstance(config["files"], list) or not config["files"]:
        raise ExportError("invalid-allowlist-schema")
    names = [_relative_name(value) for value in config["files"]]
    if len({name.casefold() for name in names}) != len(names):
        raise ExportError("duplicate-allowlist-path")
    files = {}
    findings = []
    for name in sorted(names):
        try:
            data = raw_allowlist if name == ALLOWLIST else _read_regular(root, name)
            findings.extend(scan_file(name, data))
        except ExportError as error:
            # Only approved relative paths and categories are exposed.
            raise ExportError(f"{name}:1: {error}") from None
        files[name] = data
    if findings:
        raise ExportError("privacy-preflight-failed\n" + "\n".join(finding.diagnostic() for finding in findings))
    return destination, files


def _zip_member(archive: zipfile.ZipFile, name: str, data: bytes) -> None:
    # Generic metadata avoids copying local owner names, permissions, timestamps.
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    archive.writestr(info, data)


def export_project(root: Path, output: str | Path | None = None) -> Path:
    destination, files = preflight(root, output)
    manifest = {
        "schema_version": 1,
        "files": [{"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()} for name, data in files.items()],
    }
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    # Nothing is written until every file, metadata check and output check passes.
    _check_components(destination, allow_missing=True)
    try:
        destination.parent.mkdir(exist_ok=True)
        _check_components(destination.parent)
        destination.mkdir(exist_ok=False)
        snapshot = destination / "snapshot"
        snapshot.mkdir()
        for name, data in {**files, MANIFEST: manifest_bytes}.items():
            target = snapshot.joinpath(*name.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as handle:
                handle.write(data)
        temporary_zip = destination / "source.zip.incomplete"
        with zipfile.ZipFile(temporary_zip, mode="x") as archive:
            for name, data in {**files, MANIFEST: manifest_bytes}.items():
                _zip_member(archive, name, data)
        # Validate compressed bytes and SHA256 before naming a publishable zip.
        with zipfile.ZipFile(temporary_zip) as archive:
            if archive.testzip() is not None or archive.namelist() != [*files, MANIFEST]:
                raise ExportError("archive-verification-failed")
            for item in manifest["files"]:
                data = archive.read(item["path"])
                if len(data) != item["bytes"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
                    raise ExportError("archive-sha256-verification-failed")
        # Windows/POSIX exclusive link prevents replacing a concurrently-created zip.
        os.link(temporary_zip, destination / "source.zip")
        temporary_zip.unlink()
        return destination
    except FileExistsError:
        raise ExportError("output-already-exists") from None
    except ExportError:
        raise
    except OSError:
        raise ExportError("export-write-failed-no-upload-performed") from None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", help="new directory directly under .release (never overwritten)")
    parser.add_argument("--check", action="store_true", help="run preflight only; create no release files")
    args = parser.parse_args()
    try:
        if args.check:
            _, files = preflight(ROOT, args.output)
            print(f"Privacy preflight passed: {len(files)} allowlisted files. Nothing written.")
        else:
            destination = export_project(ROOT, args.output)
            print(f"Local export verified: {destination.relative_to(ROOT).as_posix()}/source.zip")
            print("Review the snapshot before publishing. No Git history or upload was performed.")
        return 0
    except ExportError as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
