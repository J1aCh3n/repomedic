from datetime import UTC, datetime


def parse_start(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError("start time must be ISO-8601") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("start time must include a timezone")
    return parsed.astimezone(UTC)


def parse_duration(value: str) -> int:
    try:
        duration = int(value)
    except ValueError as error:
        raise ValueError("duration must be an integer") from error
    if duration <= 0:
        raise ValueError("duration must be positive")
    return duration
