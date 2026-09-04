from pathlib import Path
import unittest
from unittest.mock import patch

from repomedic.workspace import (
    PathSafetyError,
    RunLayout,
    reset_workspace,
    resolve_within,
)
from tests.helpers import temporary_directory


class WorkspaceTests(unittest.TestCase):
    def test_reset_replaces_disposable_workspace_from_source(self) -> None:
        with temporary_directory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            source.mkdir()
            (source / "value.txt").write_text("baseline", encoding="utf-8")
            layout = RunLayout.create(root / "runs", "case_001", "run_001")

            reset_workspace(source, layout)
            (layout.workspace / "value.txt").write_text("changed", encoding="utf-8")
            reset_workspace(source, layout)

            self.assertEqual(
                (layout.workspace / "value.txt").read_text(encoding="utf-8"),
                "baseline",
            )

    def test_resolve_within_rejects_escape_and_absolute_paths(self) -> None:
        with temporary_directory() as temp_dir:
            root = Path(temp_dir)

            with self.assertRaises(PathSafetyError):
                resolve_within(root, "../outside")
            with self.assertRaises(PathSafetyError):
                resolve_within(root, str(root / "absolute"))

    def test_reset_rejects_source_symlink(self) -> None:
        with temporary_directory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            source.mkdir()
            (source / "link.txt").write_text("outside", encoding="utf-8")
            layout = RunLayout.create(root / "runs", "case_001", "run_001")

            with patch("repomedic.workspace.is_link_or_junction", return_value=True):
                with self.assertRaisesRegex(PathSafetyError, "symlink"):
                    reset_workspace(source, layout)


if __name__ == "__main__":
    unittest.main()
