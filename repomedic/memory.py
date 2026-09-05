from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
import json
import re
import sqlite3

from repomedic.artifacts import redact_text


class MemoryEvidenceError(ValueError):
    """Raised when a run lacks the evidence required for a memory write."""


class MemoryIntegrityError(RuntimeError):
    """Raised when one run identity maps to conflicting memory content."""


@dataclass(frozen=True)
class MemoryEntry:
    entry_id: str
    fixture_id: str
    fixture_version: str
    case_id: str
    run_id: str
    issue: str
    root_cause: str
    repair_summary: str
    changed_paths: tuple[str, ...]
    evidence_paths: tuple[str, ...]
    created_at: str
    content_hash: str


@dataclass(frozen=True)
class MemoryMatch:
    entry: MemoryEntry
    score: int

    def prompt_value(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry.entry_id,
            "score": self.score,
            "provenance": {
                "fixture_id": self.entry.fixture_id,
                "fixture_version": self.entry.fixture_version,
                "case_id": self.entry.case_id,
                "run_id": self.entry.run_id,
            },
            "lesson": {
                "issue": _prompt_text(self.entry.issue, 300),
                "root_cause": _prompt_text(self.entry.root_cause, 300),
                "repair_summary": _prompt_text(self.entry.repair_summary, 300),
                "changed_paths": [
                    _prompt_text(path, 120) for path in self.entry.changed_paths[:3]
                ],
                "evidence_paths": [
                    _prompt_text(path, 120) for path in self.entry.evidence_paths[:3]
                ],
            },
        }


@dataclass(frozen=True)
class MemorySnapshot:
    entry_count: int
    content_hash: str


_SCHEMA_VERSION = 1
DEFAULT_MEMORY_CONTEXT_BUDGET_CHARS = 2400
MIN_MEMORY_CONTEXT_BUDGET_CHARS = 512
MAX_MEMORY_CONTEXT_BUDGET_CHARS = 20000
_TOKEN = re.compile(r"[A-Za-z0-9_]+")
_STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
}


def _prompt_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


def validate_memory_context_budget(value: int) -> None:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not MIN_MEMORY_CONTEXT_BUDGET_CHARS
        <= value
        <= MAX_MEMORY_CONTEXT_BUDGET_CHARS
    ):
        raise ValueError(
            "memory context budget must be between "
            f"{MIN_MEMORY_CONTEXT_BUDGET_CHARS} and "
            f"{MAX_MEMORY_CONTEXT_BUDGET_CHARS} characters"
        )


def memory_prompt_chars(lessons: Sequence[dict[str, Any]]) -> int:
    if not lessons:
        return 0
    return len(
        json.dumps(
            list(lessons),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def pack_memory_matches(
    matches: Iterable[MemoryMatch],
    *,
    context_budget_chars: int = DEFAULT_MEMORY_CONTEXT_BUDGET_CHARS,
) -> tuple[dict[str, Any], ...]:
    """Build the ranked Planner payload without exceeding a deterministic budget."""
    validate_memory_context_budget(context_budget_chars)
    packed: list[dict[str, Any]] = []
    for match in matches:
        candidate = match.prompt_value()
        if memory_prompt_chars((*packed, candidate)) <= context_budget_chars:
            packed.append(candidate)
    return tuple(packed)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise MemoryEvidenceError(f"cannot read memory evidence {path.name}: {error}") from error
    if not isinstance(value, dict):
        raise MemoryEvidenceError(f"memory evidence {path.name} must be an object")
    return value


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MemoryEvidenceError(f"memory evidence {field} must be non-empty text")
    return redact_text(value.strip())


def _review_summary(review: dict[str, Any]) -> str:
    feedback = review.get("feedback")
    if isinstance(feedback, str) and feedback.strip():
        return _required_text(feedback, "review.feedback")
    reasons = review.get("reasons")
    if isinstance(reasons, list):
        usable = [
            reason.strip()
            for reason in reasons
            if isinstance(reason, str) and reason.strip()
        ]
        if usable:
            return redact_text(" ".join(usable))
    raise MemoryEvidenceError("review must contain feedback or reasons")


def _verified_status(run_dir: Path) -> None:
    try:
        lines = (run_dir / "final-report.md").read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise MemoryEvidenceError(f"cannot read final-report.md: {error}") from error
    if "- Status: `verified`" not in lines:
        raise MemoryEvidenceError("only verified runs may be written to memory")


def _approved(trace_path: Path) -> bool:
    try:
        lines = trace_path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise MemoryEvidenceError(f"cannot read trace.jsonl: {error}") from error
    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise MemoryEvidenceError(f"trace.jsonl contains invalid JSON: {error}") from error
        if (
            record.get("event") == "approval_decided"
            and record.get("data", {}).get("action") == "approve"
        ):
            return True
    return False


def _validated_tests(run_dir: Path) -> None:
    results = _load_json(run_dir / "test-results.json").get("results")
    if not isinstance(results, list):
        raise MemoryEvidenceError("test-results.json must contain a results list")
    kinds: set[str] = set()
    for result in results:
        if not isinstance(result, dict):
            raise MemoryEvidenceError("test-results.json contains an invalid result")
        kind = result.get("kind")
        if isinstance(kind, str):
            kinds.add(kind)
        if (
            result.get("exit_code") != 0
            or result.get("timed_out") is not False
            or result.get("infrastructure_error") is not False
        ):
            raise MemoryEvidenceError("all public and evaluator tests must pass")
    if not {"public", "evaluator"}.issubset(kinds):
        raise MemoryEvidenceError("verified memory requires public and evaluator results")


def _entry_from_run(run_dir: Path) -> MemoryEntry:
    resolved = run_dir.resolve()
    _verified_status(resolved)
    if not _approved(resolved / "trace.jsonl"):
        raise MemoryEvidenceError("verified memory requires an approved patch")
    _validated_tests(resolved)

    config = _load_json(resolved / "config.json")
    policy = _load_json(resolved / "policy.json")
    investigation = _load_json(resolved / "investigation.json")
    review = _load_json(resolved / "review.json")
    if policy.get("compliant") is not True or policy.get("violations") != []:
        raise MemoryEvidenceError("verified memory requires a compliant path policy")

    fixture = config.get("fixture")
    if not isinstance(fixture, dict):
        raise MemoryEvidenceError("config.fixture must be an object")
    changes = policy.get("changes")
    if not isinstance(changes, list) or not changes:
        raise MemoryEvidenceError("verified memory requires at least one changed path")
    changed_paths = tuple(
        sorted(
            _required_text(change.get("path"), "policy.changes[].path")
            for change in changes
            if isinstance(change, dict)
        )
    )
    if len(changed_paths) != len(changes):
        raise MemoryEvidenceError("policy.changes contains an invalid item")

    evidence = investigation.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        raise MemoryEvidenceError("investigation.evidence must be a non-empty list")
    evidence_paths = tuple(
        sorted(
            {
                _required_text(item.get("path"), "investigation.evidence[].path")
                for item in evidence
                if isinstance(item, dict)
            }
        )
    )
    if not evidence_paths:
        raise MemoryEvidenceError("investigation.evidence contains no valid paths")

    values = {
        "fixture_id": _required_text(fixture.get("fixture_id"), "fixture.fixture_id"),
        "fixture_version": _required_text(fixture.get("version"), "fixture.version"),
        "case_id": _required_text(config.get("case_id"), "case_id"),
        "run_id": _required_text(resolved.name, "run_id"),
        "issue": _required_text(config.get("issue"), "issue"),
        "root_cause": _required_text(investigation.get("root_cause"), "root_cause"),
        "repair_summary": _review_summary(review),
        "changed_paths": changed_paths,
        "evidence_paths": evidence_paths,
    }
    canonical = json.dumps(values, sort_keys=True, separators=(",", ":"))
    content_hash = sha256(canonical.encode("utf-8")).hexdigest()
    identity = f"{values['case_id']}\0{values['run_id']}"
    return MemoryEntry(
        entry_id=sha256(identity.encode("utf-8")).hexdigest()[:24],
        created_at=datetime.now(UTC).isoformat(),
        content_hash=content_hash,
        **values,
    )


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in (match.group(0).lower() for match in _TOKEN.finditer(value))
        if token not in _STOP_WORDS and len(token) > 1
    }


class EpisodicMemoryStore:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        if self.path.exists() and not self.path.is_file():
            raise ValueError(f"memory database is not a file: {self.path}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in {0, _SCHEMA_VERSION}:
                raise MemoryIntegrityError(
                    f"unsupported memory schema version: {version}"
                )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_entries (
                    entry_id TEXT PRIMARY KEY,
                    fixture_id TEXT NOT NULL,
                    fixture_version TEXT NOT NULL,
                    case_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    issue TEXT NOT NULL,
                    root_cause TEXT NOT NULL,
                    repair_summary TEXT NOT NULL,
                    changed_paths_json TEXT NOT NULL,
                    evidence_paths_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    UNIQUE(case_id, run_id)
                )
                """
            )
            connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> MemoryEntry:
        return MemoryEntry(
            entry_id=row["entry_id"],
            fixture_id=row["fixture_id"],
            fixture_version=row["fixture_version"],
            case_id=row["case_id"],
            run_id=row["run_id"],
            issue=row["issue"],
            root_cause=row["root_cause"],
            repair_summary=row["repair_summary"],
            changed_paths=tuple(json.loads(row["changed_paths_json"])),
            evidence_paths=tuple(json.loads(row["evidence_paths_json"])),
            created_at=row["created_at"],
            content_hash=row["content_hash"],
        )

    def record_verified_run(self, run_dir: Path) -> MemoryEntry:
        entry = _entry_from_run(run_dir)
        with self._connection() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO memory_entries VALUES (
                    :entry_id, :fixture_id, :fixture_version, :case_id, :run_id,
                    :issue, :root_cause, :repair_summary, :changed_paths_json,
                    :evidence_paths_json, :created_at, :content_hash
                )
                """,
                {
                    **asdict(entry),
                    "changed_paths_json": json.dumps(list(entry.changed_paths)),
                    "evidence_paths_json": json.dumps(list(entry.evidence_paths)),
                },
            )
            row = connection.execute(
                "SELECT * FROM memory_entries WHERE case_id = ? AND run_id = ?",
                (entry.case_id, entry.run_id),
            ).fetchone()
        if row is None:
            raise MemoryIntegrityError("memory insert did not produce an entry")
        stored = self._row_to_entry(row)
        if stored.content_hash != entry.content_hash:
            raise MemoryIntegrityError(
                "one case/run identity maps to conflicting memory content"
            )
        return stored

    def search(
        self,
        issue: str,
        *,
        fixture_id: str | None = None,
        exclude_case_id: str | None = None,
        limit: int = 3,
    ) -> tuple[MemoryMatch, ...]:
        if not 1 <= limit <= 10:
            raise ValueError("memory search limit must be between 1 and 10")
        query_tokens = _tokens(issue)
        with self._connection() as connection:
            rows = connection.execute("SELECT * FROM memory_entries").fetchall()
        matches: list[MemoryMatch] = []
        for row in rows:
            entry = self._row_to_entry(row)
            if entry.case_id == exclude_case_id:
                continue
            searchable = " ".join(
                (
                    entry.issue,
                    entry.root_cause,
                    entry.repair_summary,
                    *entry.changed_paths,
                    *entry.evidence_paths,
                )
            )
            overlap = len(query_tokens & _tokens(searchable))
            fixture_bonus = 3 if fixture_id == entry.fixture_id else 0
            score = overlap + fixture_bonus
            if score:
                matches.append(MemoryMatch(entry=entry, score=score))
        matches.sort(key=lambda item: (-item.score, item.entry.case_id, item.entry.entry_id))
        return tuple(matches[:limit])

    def count(self) -> int:
        with self._connection() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM memory_entries").fetchone()[0])

    def snapshot(self) -> MemorySnapshot:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT entry_id, content_hash FROM memory_entries ORDER BY entry_id"
            ).fetchall()
        identity = [
            {"entry_id": row["entry_id"], "content_hash": row["content_hash"]}
            for row in rows
        ]
        canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"))
        return MemorySnapshot(
            entry_count=len(identity),
            content_hash=sha256(canonical.encode("utf-8")).hexdigest(),
        )
