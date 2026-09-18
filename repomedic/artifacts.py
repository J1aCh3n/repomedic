from pathlib import Path
from typing import Any
import json
import os
import re

from repomedic.changes import resolve_within


_ASSIGNMENT_SECRET = re.compile(
    r"(?i)"
    r"(?P<label>\b(?:[a-z0-9]+[_-])*(?:api[_-]?key|token|secret|password|authorization)\b)"
    r"(?P<separator>[\"']?\s*[:=]\s*)"
    r"(?P<quote>[\"']?)"
    r"(?P<value>[^\s,;}\"']+)"
    r"(?P=quote)"
)
_BEARER_SECRET = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_PROVIDER_TOKEN = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_-]{20,})\b"
)
_SENSITIVE_KEY = re.compile(
    r"(?i)(?:^|[_-])(?:api[_-]?key|token|secret|password|authorization)$"
)


def redact_text(text: str) -> str:
    redacted = _ASSIGNMENT_SECRET.sub(
        lambda match: (
            f"{match.group('label')}{match.group('separator')}"
            f"{match.group('quote')}[REDACTED]{match.group('quote')}"
        ),
        text,
    )
    redacted = _BEARER_SECRET.sub("Bearer [REDACTED]", redacted)
    return _PROVIDER_TOKEN.sub("[REDACTED]", redacted)


def sanitize(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            text_key = str(key)
            sanitized[text_key] = (
                "[REDACTED]" if _SENSITIVE_KEY.search(text_key) else sanitize(item)
            )
        return sanitized
    if isinstance(value, (list, tuple)):
        return [sanitize(item) for item in value]
    return value


class ArtifactWriter:
    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir.resolve()
        self.run_dir.mkdir(parents=True, exist_ok=True)

    def _atomic_write(self, name: str, content: str) -> None:
        path = resolve_within(self.run_dir, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_text(content, encoding="utf-8", newline="\n")
        os.replace(temporary, path)

    def write_json(self, name: str, value: Any) -> None:
        content = json.dumps(sanitize(value), indent=2, sort_keys=True)
        self._atomic_write(name, f"{content}\n")

    def write_text(self, name: str, content: str) -> None:
        self._atomic_write(name, redact_text(content))

    def append_trace(self, event: str, data: dict[str, Any]) -> None:
        self.append_jsonl("trace.jsonl", {"event": event, "data": data})

    def append_jsonl(self, name: str, value: dict[str, Any]) -> None:
        path = resolve_within(self.run_dir, name)
        record = json.dumps(
            sanitize(value),
            sort_keys=True,
            separators=(",", ":"),
        )
        with path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(f"{record}\n")
