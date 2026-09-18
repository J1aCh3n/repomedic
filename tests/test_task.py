from pathlib import Path
import unittest

from repomedic.task import load_task, load_taskset
from tests.helpers import temporary_directory


class TaskTests(unittest.TestCase):
    def test_twelve_development_tasks_have_no_manifest_permissions_or_limits(self) -> None:
        taskset = load_taskset(Path("benchmarks/suites/initial_12.yaml"))
        self.assertEqual(len(taskset.tasks), 12)
        self.assertEqual(taskset.split, "development")
        for task in taskset.tasks:
            self.assertTrue(task.repo.is_dir())
            self.assertNotIn("allowed_paths", type(task).model_fields)
            self.assertNotIn("limits", type(task).model_fields)
            self.assertNotIn("evaluator", task.agent_context())

    def test_taskset_rejects_traversal_and_duplicates(self) -> None:
        with temporary_directory() as directory:
            root = Path(directory)
            suite = root / "suite.yaml"
            for cases in ("[../private]", "[case, case]"):
                suite.write_text(f"suite_id: dev\nsplit: development\ncases: {cases}\n")
                with self.assertRaises(ValueError):
                    load_taskset(suite)

    def test_legacy_permissions_are_rejected_instead_of_silently_used(self) -> None:
        with temporary_directory() as directory:
            path = Path(directory) / "manifest.yaml"
            path.write_text("case_id: old\npaths: {allowed: [app.py]}\n")
            with self.assertRaises(ValueError):
                load_task(path)
