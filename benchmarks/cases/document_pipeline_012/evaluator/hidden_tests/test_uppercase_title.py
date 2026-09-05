from contextlib import redirect_stdout
from io import StringIO
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

from document_pipeline.cli import main  # noqa: E402
from document_pipeline.pipeline import process  # noqa: E402


class UppercaseTitleEvaluatorTests(unittest.TestCase):
    def test_cli_flag_uppercases_only_the_title(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "brief.txt"
            destination = root / "brief.md"
            source.write_text("Mixed   title\nKeep Mixed Body\n", encoding="utf-8")

            with redirect_stdout(StringIO()):
                exit_code = main(
                    [str(source), str(destination), "--uppercase-title"]
                )

            self.assertEqual(exit_code, 0)
            self.assertEqual(
                destination.read_text(encoding="utf-8"),
                "# MIXED TITLE\n\nKeep Mixed Body\n\n_Source: brief.txt_\n",
            )

    def test_programmatic_default_preserves_title_case(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "brief.txt"
            destination = root / "brief.md"
            source.write_text("Mixed Title\nBody\n", encoding="utf-8")

            rendered = process(source, destination)

            self.assertTrue(rendered.startswith("# Mixed Title\n"))


if __name__ == "__main__":
    unittest.main()
