from task_scheduler.models import Task


class InMemoryTaskStore:
    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}
        self._next_number = 1

    def next_task_id(self) -> str:
        task_id = f"task-{self._next_number}"
        self._next_number += 1
        return task_id

    def save(self, task: Task) -> None:
        self._tasks[task.task_id] = task

    def get(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    def all(self) -> tuple[Task, ...]:
        return tuple(self._tasks.values())
