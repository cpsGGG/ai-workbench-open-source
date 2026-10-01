import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TweetStorageTests(unittest.TestCase):
    def test_in_memory_frontend_storage_regressions(self):
        completed = subprocess.run(
            ["node", "--experimental-vm-modules", "--test", "tests/test_tweets_storage.mjs"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        self.assertEqual(
            completed.returncode,
            0,
            f"Tweet storage regression tests failed:\n{completed.stdout}\n{completed.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
