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


def make_scheduler() -> tuple[Scheduler, InMemoryTaskStore]:
    store = InMemoryTaskStore()
    return Scheduler(store), store


class RescheduleEvaluatorTests(unittest.TestCase):
    def test_task_does_not_conflict_with_itself(self) -> None:
        scheduler, _ = make_scheduler()
        original = scheduler.schedule(
            "Focus",
            datetime(2030, 1, 2, 10, 0, tzinfo=UTC),
            60,
            now=NOW,
        )

        updated = scheduler.reschedule(
            original.task_id,
            datetime(2030, 1, 2, 10, 15, tzinfo=UTC),
            now=NOW,
        )

        self.assertEqual(updated.starts_at, datetime(2030, 1, 2, 10, 15, tzinfo=UTC))

    def test_conflicting_reschedule_leaves_original_unchanged(self) -> None:
        scheduler, store = make_scheduler()
        original = scheduler.schedule(
            "First",
            datetime(2030, 1, 2, 10, 0, tzinfo=UTC),
            30,
            now=NOW,
        )
        scheduler.schedule(
            "Second",
            datetime(2030, 1, 2, 11, 0, tzinfo=UTC),
            30,
            now=NOW,
        )

        with self.assertRaisesRegex(ValueError, "conflict"):
            scheduler.reschedule(
                original.task_id,
                datetime(2030, 1, 2, 11, 15, tzinfo=UTC),
                now=NOW,
            )

        self.assertEqual(store.get(original.task_id), original)

    def test_past_and_unknown_reschedules_have_no_side_effects(self) -> None:
        scheduler, store = make_scheduler()
        original = scheduler.schedule(
            "Existing",
            datetime(2030, 1, 2, 10, 0, tzinfo=UTC),
            30,
            now=NOW,
        )

        with self.assertRaisesRegex(ValueError, "future"):
            scheduler.reschedule(original.task_id, NOW, now=NOW)
        with self.assertRaisesRegex(ValueError, "unknown"):
            scheduler.reschedule(
                "task-999",
                datetime(2030, 1, 2, 12, 0, tzinfo=UTC),
                now=NOW,
            )

        self.assertEqual(store.all(), (original,))


if __name__ == "__main__":
    unittest.main()
