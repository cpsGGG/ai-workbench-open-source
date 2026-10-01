"""Run syntax and regression checks for the part of the workbench being changed."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULES = {
    "stats": "token-stats",
    "papers": "papers",
    "accounting": "accounting",
    "videos": "videos",
    "tasks": "tasks",
    "health": "health",
    "social": "social",
    "tweets": "tweets",
    "chats": "chats",
}
TEST_PATTERNS = {
    "shell": (),
    "stats": ("test_token_stats_*.py", "test_token_pricing.py"),
    "papers": ("test_paper_trends.py",),
    "accounting": ("test_accounting_storage.py",),
    "all": ("test_*.py",),
}


def syntax_files(scope: str) -> list[Path]:
    files = [Path(__file__), *ROOT.joinpath("src").glob("*.js")]
    if scope == "all":
        files.extend(ROOT.joinpath("src", "modules").rglob("*.js"))
        files.extend((ROOT / "server.py", ROOT / "paper_trends.py"))
    elif scope != "shell":
        files.extend(ROOT.joinpath("src", "modules", MODULES[scope]).glob("*.js"))
        files.append(ROOT / "server.py")
        if scope == "papers":
            files.append(ROOT / "paper_trends.py")
    for pattern in TEST_PATTERNS.get(scope, ()):
        files.extend(ROOT.joinpath("tests").glob(pattern))
    return sorted(set(files))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scope", choices=("shell", *MODULES, "all"), default="stats",
        help="default: stats; use shell for module loading, all for shared backend changes",
    )
    args = parser.parse_args()
    node = shutil.which("node")
    if not node:
        parser.error("Node.js is required for JavaScript syntax and frontend checks.")

    started = time.perf_counter()
    files = syntax_files(args.scope)
    print(f"Scope: {args.scope} ({len(files)} syntax checks)", flush=True)
    for path in files:
        if path.suffix == ".py":
            try:
                compile(path.read_text(encoding="utf-8-sig"), str(path), "exec")
            except (OSError, SyntaxError) as error:
                print(error, file=sys.stderr)
                return 1
        else:
            result = subprocess.run(
                [node, "--check", str(path)], cwd=ROOT, capture_output=True, text=True,
            )
            if result.returncode:
                print(result.stderr, file=sys.stderr)
                return result.returncode
    print(f"Syntax passed in {time.perf_counter() - started:.2f}s", flush=True)

    # The shell is shared by every module, so its lifecycle checks always apply.
    shell_test = ROOT / "tests" / "test_app_loading.mjs"
    if not shell_test.exists():
        print(f"Missing shell regression suite: {shell_test}", file=sys.stderr)
        return 1
    result = subprocess.run(
        [node, "--experimental-vm-modules", "--test", str(shell_test)], cwd=ROOT,
    )
    if result.returncode:
        return result.returncode

    patterns = TEST_PATTERNS.get(args.scope, ())
    if patterns:
        sys.path.insert(0, str(ROOT))
        suite = unittest.TestSuite()
        for pattern in patterns:
            suite.addTests(unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern))
        if not unittest.TextTestRunner(verbosity=1).run(suite).wasSuccessful():
            return 1
    else:
        print("No dedicated Python regression suite in this scope; shell and syntax checked.")
    print(f"All selected checks passed in {time.perf_counter() - started:.2f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    raise SystemExit(main())
