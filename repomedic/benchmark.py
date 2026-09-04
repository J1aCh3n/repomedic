from dataclasses import dataclass
from pathlib import Path
from typing import Any
import re

import yaml

from repomedic.manifest import load_manifest
from repomedic.models import CaseManifest


class SuiteError(ValueError):
    """Raised when a benchmark suite definition is invalid."""


@dataclass(frozen=True)
class SuiteCase:
    case_id: str
    case_dir: Path
    manifest: CaseManifest


@dataclass(frozen=True)
class BenchmarkSuite:
    suite_id: str
    description: str
    split: str
    cases: tuple[SuiteCase, ...]
    suite_path: Path


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SuiteError(f"{field} must be a non-empty string")
    return value.strip()


def load_suite(
    path: Path, *, case_ids: tuple[str, ...] | None = None
) -> BenchmarkSuite:
    resolved = path.resolve()
    try:
        data = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise SuiteError(f"cannot load suite: {error}") from error
    if not isinstance(data, dict):
        raise SuiteError("suite must be a mapping")

    suite_id = _text(data.get("suite_id"), "suite_id")
    if not _SAFE_ID.fullmatch(suite_id):
        raise SuiteError("suite_id contains unsafe characters")
    split = _text(data.get("split"), "split")
    if split not in {"development", "holdout"}:
        raise SuiteError("split must be development or holdout")
    raw_ids = data.get("cases") if case_ids is None else list(case_ids)
    if not isinstance(raw_ids, list) or not raw_ids:
        raise SuiteError("cases must be a non-empty list")
    normalized_ids = tuple(_text(item, "cases[]") for item in raw_ids)
    if len(set(normalized_ids)) != len(normalized_ids):
        raise SuiteError("suite contains duplicate case IDs")

    benchmark_root = resolved.parent.parent
    cases_root = (benchmark_root / "cases").resolve()
    cases: list[SuiteCase] = []
    for case_id in normalized_ids:
        if not _SAFE_ID.fullmatch(case_id):
            raise SuiteError(f"unsafe case ID: {case_id!r}")
        case_dir = (cases_root / case_id).resolve()
        if case_dir.parent != cases_root:
            raise SuiteError(f"case escapes cases directory: {case_id!r}")
        manifest = load_manifest(case_dir / "manifest.yaml")
        if manifest.case_id != case_id:
            raise SuiteError(
                f"case ID mismatch: suite has {case_id}, manifest has {manifest.case_id}"
            )
        cases.append(SuiteCase(case_id, case_dir, manifest))

    return BenchmarkSuite(
        suite_id=suite_id,
        description=_text(data.get("description"), "description"),
        split=split,
        cases=tuple(cases),
        suite_path=resolved,
    )
