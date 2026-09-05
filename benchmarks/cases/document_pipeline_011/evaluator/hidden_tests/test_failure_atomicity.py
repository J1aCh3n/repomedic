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

from document_pipeline.pipeline import process  # noqa: E402


class FailureAtomicityEvaluatorTests(unittest.TestCase):
    def test_invalid_document_does_not_create_destination(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "untitled.txt"
            destination = root / "new" / "output.md"
            source.write_text("\nBody\n", encoding="utf-8")

            with self.assertRaises(ValueError):
                process(source, destination)

            self.assertFalse(destination.exists())

    def test_missing_source_does_not_replace_existing_destination(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "missing.txt"
            destination = root / "output.md"
            destination.write_bytes(b"existing\r\nbytes")

            with self.assertRaises(FileNotFoundError):
                process(source, destination)

            self.assertEqual(destination.read_bytes(), b"existing\r\nbytes")


if __name__ == "__main__":
    unittest.main()
