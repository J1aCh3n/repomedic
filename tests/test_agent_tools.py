from pathlib import Path
import unittest

from repomedic.agent_schemas import TextReplacement
from repomedic.agent_tools import RepositoryTools, ToolExecutionError
from tests.helpers import temporary_directory


class AgentToolTests(unittest.TestCase):
    def test_search_read_and_atomic_idempotent_edit(self) -> None:
        with temporary_directory() as temp_dir:
            root = Path(temp_dir)
            source = root / "pkg" / "service.py"
            source.parent.mkdir()
            source.write_text("if total > 100:\n    return 90\n", encoding="utf-8")
            tools = RepositoryTools(root, allowed_paths=("pkg/service.py",))

            matches = tools.search("total >")
            self.assertEqual(matches[0]["path"], "pkg/service.py")
            self.assertIn("return 90", tools.read("pkg/service.py"))

            edit = TextReplacement(
                path="pkg/service.py",
                old="total > 100",
                new="total >= 100",
                rationale="include the threshold",
            )
            tools.apply((edit,))
            tools.apply((edit,))
            self.assertIn("total >= 100", source.read_text(encoding="utf-8"))

    def test_rejects_escape_and_non_allowlisted_edit(self) -> None:
        with temporary_directory() as temp_dir:
            root = Path(temp_dir)
            (root / "safe.py").write_text("safe = True\n", encoding="utf-8")
            tools = RepositoryTools(root, allowed_paths=("safe.py",))

            with self.assertRaises(ToolExecutionError):
                tools.read("../secret.txt")
            with self.assertRaises(ToolExecutionError):
                tools.apply(
                    (
                        TextReplacement(
                            path="other.py",
                            old="x",
                            new="y",
                            rationale="out of scope",
                        ),
                    )
                )


if __name__ == "__main__":
    unittest.main()
