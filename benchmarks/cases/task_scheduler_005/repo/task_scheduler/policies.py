from datetime import datetime, timedelta
from typing import Iterable

from task_scheduler.models import Task


def ensure_future(starts_at: datetime, now: datetime) -> None:
    if starts_at <= now:
        raise ValueError("task must start in the future")


def overlaps(starts_at: datetime, duration_minutes: int, task: Task) -> bool:
    ends_at = starts_at + timedelta(minutes=duration_minutes)
    return starts_at <= task.ends_at and task.starts_at <= ends_at


def ensure_no_conflict(
    starts_at: datetime,
    duration_minutes: int,
    existing_tasks: Iterable[Task],
) -> None:
    if any(overlaps(starts_at, duration_minutes, task) for task in existing_tasks):
        raise ValueError("task conflicts with an existing task")
