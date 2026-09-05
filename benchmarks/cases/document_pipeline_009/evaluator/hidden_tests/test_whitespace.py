import os
from pathlib import Path
import sys
import unittest


DEFAULT_REPO = Path(__file__).resolve().parents[2] / "repo"
REPO_UNDER_TEST = Path(
    os.environ.get("REPOMEDIC_REPO_UNDER_TEST", DEFAULT_REPO)
).resolve()
sys.path.insert(0, str(REPO_UNDER_TEST))

from document_pipeline.transform import normalize_whitespace  # noqa: E402


class WhitespaceEvaluatorTests(unittest.TestCase):
    def test_newlines_and_form_feeds_are_collapsed(self) -> None:
        normalized = normalize_whitespace("\nalpha\n\t beta\f gamma\r\n")

        self.assertEqual(normalized, "alpha beta gamma")

    def test_non_whitespace_content_is_preserved(self) -> None:
        normalized = normalize_whitespace("  MiXeD   punctuation: yes!  ")

        self.assertEqual(normalized, "MiXeD punctuation: yes!")


if __name__ == "__main__":
    unittest.main()
