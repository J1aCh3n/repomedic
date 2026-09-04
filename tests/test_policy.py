from pathlib import Path
import shutil
import unittest

from repomedic.policy import build_patch, collect_changes, evaluate_policy
from tests.helpers import temporary_directory


class PolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = temporary_directory()
        root = Path(self.temp_dir.__enter__())
        self.baseline = root / "baseline"
        self.workspace = root / "workspace"
        (self.baseline / "order_service").mkdir(parents=True)
        (self.baseline / "tests").mkdir()
        (self.baseline / "order_service" / "pricing.py").write_text(
            "if total > 100:\n    pass\n",
            encoding="utf-8",
        )
        (self.baseline / "tests" / "test_pricing.py").write_text(
            "# public test\n",
            encoding="utf-8",
        )
        shutil.copytree(self.baseline, self.workspace)

    def tearDown(self) -> None:
        self.temp_dir.__exit__(None, None, None)

    def test_allows_only_declared_change_and_builds_diff(self) -> None:
        (self.workspace / "order_service" / "pricing.py").write_text(
            "if total >= 100:\n    pass\n",
            encoding="utf-8",
        )

        changes = collect_changes(self.baseline, self.workspace)
        report = evaluate_policy(
            changes,
            allowed=("order_service/pricing.py",),
            forbidden=("tests", ".git", ".env", "evaluator"),
        )
        patch = build_patch(self.baseline, self.workspace, changes)

        self.assertTrue(report.compliant)
        self.assertEqual([change.path for change in changes], ["order_service/pricing.py"])
        self.assertIn("-if total > 100:", patch)
        self.assertIn("+if total >= 100:", patch)

    def test_rejects_out_of_scope_and_forbidden_changes(self) -> None:
        (self.workspace / "tests" / "test_pricing.py").write_text(
            "# weakened test\n",
            encoding="utf-8",
        )
        (self.workspace / ".env").write_text("API_KEY=secret", encoding="utf-8")

        report = evaluate_policy(
            collect_changes(self.baseline, self.workspace),
            allowed=("order_service/pricing.py",),
            forbidden=("tests", ".git", ".env", "evaluator"),
        )

        self.assertFalse(report.compliant)
        self.assertEqual(len(report.violations), 2)


if __name__ == "__main__":
    unittest.main()
