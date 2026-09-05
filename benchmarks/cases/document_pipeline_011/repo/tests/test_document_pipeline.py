from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from document_pipeline.loader import load_document
from document_pipeline.pipeline import process
from document_pipeline.transform import normalize_whitespace


class DocumentPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)

    def test_process_normalizes_and_exports_markdown(self) -> None:
        source = self.root / "report.txt"
        destination = self.root / "out" / "report.md"
        source.write_text(
            "Quarterly   Report\nFirst\tparagraph.\nSecond   paragraph.\n",
            encoding="utf-8",
        )

        rendered = process(source, destination)

        self.assertEqual(
            rendered,
            "# Quarterly Report\n\nFirst paragraph.\n\n"
            "Second paragraph.\n\n_Source: report.txt_\n",
        )
        self.assertEqual(destination.read_text(encoding="utf-8"), rendered)

    def test_loader_rejects_an_empty_title(self) -> None:
        source = self.root / "untitled.txt"
        source.write_text("\nBody\n", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "title"):
            load_document(source)

    def test_normalize_whitespace_handles_mixed_runs(self) -> None:
        self.assertEqual(normalize_whitespace("  alpha\t beta   gamma  "), "alpha beta gamma")

    def test_failed_input_preserves_existing_destination(self) -> None:
        source = self.root / "untitled.txt"
        destination = self.root / "existing.md"
        source.write_text("\nBody\n", encoding="utf-8")
        destination.write_text("keep me", encoding="utf-8")

        with self.assertRaises(ValueError):
            process(source, destination)

        self.assertEqual(destination.read_text(encoding="utf-8"), "keep me")

    def test_uppercase_title_changes_only_the_title(self) -> None:
        source = self.root / "note.txt"
        destination = self.root / "note.md"
        source.write_text("Mixed Case\nKeep This Body\n", encoding="utf-8")

        rendered = process(source, destination, uppercase_title=True)

        self.assertIn("# MIXED CASE", rendered)
        self.assertIn("Keep This Body", rendered)


if __name__ == "__main__":
    unittest.main()
