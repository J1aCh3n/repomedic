from datetime import UTC, datetime
import os
from pathlib import Path
import sys
import unittest


DEFAULT_REPO = Path(__file__).resolve().parents[2] / "repo"
REPO_UNDER_TEST = Path(
    os.environ.get("REPOMEDIC_REPO_UNDER_TEST", DEFAULT_REPO)
).resolve()
sys.path.insert(0, str(REPO_UNDER_TEST))

from task_scheduler.scheduler import Scheduler  # noqa: E402
from task_scheduler.storage import InMemoryTaskStore  # noqa: E402


NOW = datetime(2030, 1, 1, 9, 0, tzinfo=UTC)


class TaskIdAtomicityEvaluatorTests(unittest.TestCase):
    def test_past_and_invalid_duration_do_not_consume_ids(self) -> None:
        scheduler = Scheduler(InMemoryTaskStore())

        with self.assertRaises(ValueError):
            scheduler.schedule("Past", NOW, 30, now=NOW)
        with self.assertRaises(ValueError):
            scheduler.schedule(
                "Bad duration",
                datetime(2030, 1, 2, 10, 0, tzinfo=UTC),
                0,
                now=NOW,
            )

        task = scheduler.schedule(
            "Valid",
            datetime(2030, 1, 2, 10, 0, tzinfo=UTC),
            30,
            now=NOW,
        )
        self.assertEqual(task.task_id, "task-1")

    def test_conflict_does_not_create_a_gap(self) -> None:
        scheduler = Scheduler(InMemoryTaskStore())
        first = scheduler.schedule(
            "First",
            datetime(2030, 1, 2, 10, 0, tzinfo=UTC),
            30,
            now=NOW,
        )
        with self.assertRaisesRegex(ValueError, "conflict"):
            scheduler.schedule(
                "Overlap",
                datetime(2030, 1, 2, 10, 15, tzinfo=UTC),
                15,
                now=NOW,
            )

        second = scheduler.schedule(
            "Second",
            datetime(2030, 1, 2, 10, 30, tzinfo=UTC),
            30,
            now=NOW,
        )
        self.assertEqual((first.task_id, second.task_id), ("task-1", "task-2"))


if __name__ == "__main__":
    unittest.main()
