from datetime import UTC, datetime
import unittest

from task_scheduler.parser import parse_duration, parse_start
from task_scheduler.scheduler import Scheduler
from task_scheduler.storage import InMemoryTaskStore


NOW = datetime(2030, 1, 1, 9, 0, tzinfo=UTC)


class ParserTests(unittest.TestCase):
    def test_start_is_normalized_to_utc(self) -> None:
        parsed = parse_start("2030-01-02T09:30:00-08:00")

        self.assertIs(parsed.tzinfo, UTC)
        self.assertEqual(parsed, datetime(2030, 1, 2, 17, 30, tzinfo=UTC))

    def test_duration_must_be_positive(self) -> None:
        with self.assertRaisesRegex(ValueError, "positive"):
            parse_duration("0")


class SchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryTaskStore()
        self.scheduler = Scheduler(self.store)

    def test_schedule_persists_a_trimmed_task(self) -> None:
        task = self.scheduler.schedule(
            "  Team sync  ",
            datetime(2030, 1, 2, 10, 0, tzinfo=UTC),
            30,
            now=NOW,
        )

        self.assertEqual(task.task_id, "task-1")
        self.assertEqual(task.title, "Team sync")
        self.assertEqual(self.store.get(task.task_id), task)

    def test_overlapping_task_is_rejected(self) -> None:
        self.scheduler.schedule(
            "First",
            datetime(2030, 1, 2, 10, 0, tzinfo=UTC),
            60,
            now=NOW,
        )

        with self.assertRaisesRegex(ValueError, "conflict"):
            self.scheduler.schedule(
                "Overlap",
                datetime(2030, 1, 2, 10, 30, tzinfo=UTC),
                30,
                now=NOW,
            )

    def test_adjacent_tasks_are_allowed(self) -> None:
        self.scheduler.schedule(
            "First",
            datetime(2030, 1, 2, 10, 0, tzinfo=UTC),
            30,
            now=NOW,
        )

        second = self.scheduler.schedule(
            "Second",
            datetime(2030, 1, 2, 10, 30, tzinfo=UTC),
            15,
            now=NOW,
        )

        self.assertEqual(second.task_id, "task-2")


if __name__ == "__main__":
    unittest.main()
