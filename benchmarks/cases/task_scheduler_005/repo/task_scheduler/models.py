from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class Task:
    task_id: str
    title: str
    starts_at: datetime
    duration_minutes: int

    @property
    def ends_at(self) -> datetime:
        return self.starts_at + timedelta(minutes=self.duration_minutes)
