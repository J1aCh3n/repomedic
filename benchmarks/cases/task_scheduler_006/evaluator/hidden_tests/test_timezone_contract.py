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

from task_scheduler.parser import parse_start  # noqa: E402
from task_scheduler.scheduler import Scheduler  # noqa: E402
from task_scheduler.storage import InMemoryTaskStore  # noqa: E402


NOW = datetime(2030, 1, 1, 9, 0, tzinfo=UTC)


class TimezoneContractEvaluatorTests(unittest.TestCase):
    def test_non_hour_offset_is_normalized_to_utc(self) -> None:
        parsed = parse_start("2030-01-02T12:15:00+05:30")

        self.assertIs(parsed.tzinfo, UTC)
        self.assertEqual(parsed, datetime(2030, 1, 2, 6, 45, tzinfo=UTC))

    def test_scheduler_persists_parsed_time_in_utc(self) -> None:
        store = InMemoryTaskStore()
        scheduler = Scheduler(store)

        task = scheduler.schedule(
            "Remote handoff",
            parse_start("2030-01-02T09:00:00-08:00"),
            30,
            now=NOW,
        )

        self.assertIs(task.starts_at.tzinfo, UTC)
        self.assertIs(store.get(task.task_id).starts_at.tzinfo, UTC)

    def test_naive_input_remains_invalid(self) -> None:
        with self.assertRaisesRegex(ValueError, "timezone"):
            parse_start("2030-01-02T09:00:00")


if __name__ == "__main__":
    unittest.main()
