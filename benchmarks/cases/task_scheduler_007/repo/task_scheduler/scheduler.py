from datetime import datetime

from task_scheduler.models import Task
from task_scheduler.policies import ensure_future, ensure_no_conflict
from task_scheduler.storage import InMemoryTaskStore


class Scheduler:
    def __init__(self, store: InMemoryTaskStore) -> None:
        self._store = store

    def schedule(
        self,
        title: str,
        starts_at: datetime,
        duration_minutes: int,
        *,
        now: datetime,
    ) -> Task:
        task_id = self._store.next_task_id()
        if not title.strip():
            raise ValueError("title must not be empty")
        if duration_minutes <= 0:
            raise ValueError("duration must be positive")
        ensure_future(starts_at, now)
        ensure_no_conflict(starts_at, duration_minutes, self._store.all())

        task = Task(
            task_id=task_id,
            title=title.strip(),
            starts_at=starts_at,
            duration_minutes=duration_minutes,
        )
        self._store.save(task)
        return task

    def upcoming(self, after: datetime) -> tuple[Task, ...]:
        return tuple(
            sorted(
                (task for task in self._store.all() if task.starts_at >= after),
                key=lambda task: (task.starts_at, task.task_id),
            )
        )
