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


class AdjacentTaskEvaluatorTests(unittest.TestCase):
    def test_earlier_task_may_end_when_existing_task_starts(self) -> None:
        scheduler = Scheduler(InMemoryTaskStore())
        scheduler.schedule(
            "Later",
            datetime(2030, 1, 2, 10, 30, tzinfo=UTC),
            30,
            now=NOW,
        )

        earlier = scheduler.schedule(
            "Earlier",
            datetime(2030, 1, 2, 10, 0, tzinfo=UTC),
            30,
            now=NOW,
        )

        self.assertEqual(earlier.task_id, "task-2")

    def test_one_minute_overlap_is_still_rejected(self) -> None:
        scheduler = Scheduler(InMemoryTaskStore())
        scheduler.schedule(
            "Existing",
            datetime(2030, 1, 2, 10, 30, tzinfo=UTC),
            30,
            now=NOW,
        )

        with self.assertRaisesRegex(ValueError, "conflict"):
            scheduler.schedule(
                "Overlap",
                datetime(2030, 1, 2, 10, 1, tzinfo=UTC),
                30,
                now=NOW,
            )


if __name__ == "__main__":
    unittest.main()
