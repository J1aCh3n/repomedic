from argparse import ArgumentParser
from datetime import UTC, datetime

from task_scheduler.parser import parse_duration, parse_start
from task_scheduler.scheduler import Scheduler
from task_scheduler.storage import InMemoryTaskStore


def main() -> None:
    parser = ArgumentParser(description="Schedule one in-memory task")
    parser.add_argument("title")
    parser.add_argument("starts_at")
    parser.add_argument("duration_minutes")
    args = parser.parse_args()

    task = Scheduler(InMemoryTaskStore()).schedule(
        args.title,
        parse_start(args.starts_at),
        parse_duration(args.duration_minutes),
        now=datetime.now(UTC),
    )
    print(f"{task.task_id}: {task.title} at {task.starts_at.isoformat()}")


if __name__ == "__main__":
    main()
