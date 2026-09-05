import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest


DEFAULT_REPO = Path(__file__).resolve().parents[2] / "repo"
REPO_UNDER_TEST = Path(
    os.environ.get("REPOMEDIC_REPO_UNDER_TEST", DEFAULT_REPO)
).resolve()
sys.path.insert(0, str(REPO_UNDER_TEST))

from document_pipeline.exporter import render_markdown  # noqa: E402
from document_pipeline.loader import load_document  # noqa: E402


class SourceContractEvaluatorTests(unittest.TestCase):
    def test_loader_preserves_the_source_path_type(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "nested" / "memo.txt"
            source.parent.mkdir()
            source.write_text("Memo\nBody\n", encoding="utf-8")

            document = load_document(source)

            self.assertIsInstance(document.source, Path)
            self.assertEqual(document.source, source)

    def test_exporter_uses_only_the_source_file_name(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "private" / "notes.txt"
            source.parent.mkdir()
            source.write_text("Notes\nBody\n", encoding="utf-8")

            rendered = render_markdown(load_document(source))

            self.assertIn("_Source: notes.txt_", rendered)
            self.assertNotIn(str(source.parent), rendered)


if __name__ == "__main__":
    unittest.main()
