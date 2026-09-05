from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import yaml

from repomedic.agent_schemas import (
    MAX_INVESTIGATION_READS,
    MAX_INVESTIGATION_SEARCHES,
    MAX_RELEVANT_FILES,
)
from repomedic.models import (
    CaseManifest,
    CommandSpec,
    FixtureIdentity,
    Limits,
)


class ManifestError(ValueError):
    """Raised when a benchmark manifest violates its schema or safety rules."""


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ManifestError(f"{field} must be a mapping")
    return value


def _string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{field} must be a non-empty string")
    return value.strip()


def _positive_int(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ManifestError(f"{field} must be a positive integer")
    return value


def _relative_path(value: Any, field: str, *, allow_dot: bool = False) -> str:
    text = _string(value, field).replace("\\", "/")
    path = PurePosixPath(text)
    windows_path = PureWindowsPath(text)
    if (
        path.is_absolute()
        or path.drive
        or windows_path.is_absolute()
        or windows_path.drive
        or ".." in path.parts
    ):
        raise ManifestError(f"{field} must be a safe relative path")
    normalized = path.as_posix()
    if normalized == "." and not allow_dot:
        raise ManifestError(f"{field} must name a relative path")
    return normalized


def _string_list(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ManifestError(f"{field} must be a non-empty list")
    return tuple(_string(item, f"{field}[]") for item in value)


def _path_list(value: Any, field: str) -> tuple[str, ...]:
    return tuple(
        _relative_path(item, f"{field}[]") for item in _string_list(value, field)
    )


def _command(value: Any, field: str, *, required_cwd: str | None = None) -> CommandSpec:
    data = _mapping(value, field)
    argv = _string_list(data.get("argv"), f"{field}.argv")
    if argv[:3] != ("python", "-m", "unittest") and field.startswith("tests."):
        raise ManifestError(f"{field}.argv must be a fixed python unittest command")
    if argv[0] != "python":
        raise ManifestError(f"{field}.argv must invoke python")
    if any("\x00" in argument for argument in argv):
        raise ManifestError(f"{field}.argv contains a null byte")
    cwd = _relative_path(data.get("cwd"), f"{field}.cwd", allow_dot=True)
    if required_cwd is not None and cwd != required_cwd:
        raise ManifestError(f"{field}.cwd must be {required_cwd!r}")
    return CommandSpec(argv=argv, cwd=cwd)


def load_manifest(path: Path) -> CaseManifest:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise ManifestError(f"cannot load manifest: {error}") from error

    data = _mapping(raw, "manifest")
    fixture = _mapping(data.get("fixture"), "fixture")
    tests = _mapping(data.get("tests"), "tests")
    paths = _mapping(data.get("paths"), "paths")
    limits = _mapping(data.get("limits"), "limits")

    manifest = CaseManifest(
        case_id=_string(data.get("case_id"), "case_id"),
        category=_string(data.get("category"), "category"),
        fixture=FixtureIdentity(
            fixture_id=_string(fixture.get("id"), "fixture.id"),
            version=_string(fixture.get("version"), "fixture.version"),
        ),
        issue=_string(data.get("issue"), "issue"),
        runnable_entry_point=_command(
            data.get("runnable_entry_point"), "runnable_entry_point"
        ),
        public_test=_command(
            tests.get("public"), "tests.public", required_cwd="repo"
        ),
        evaluator_test=_command(
            tests.get("evaluator"), "tests.evaluator", required_cwd="evaluator"
        ),
        allowed_paths=_path_list(paths.get("allowed"), "paths.allowed"),
        forbidden_paths=_path_list(paths.get("forbidden"), "paths.forbidden"),
        limits=Limits(
            wall_time_seconds=_positive_int(
                limits.get("wall_time_seconds"), "limits.wall_time_seconds"
            ),
            tool_calls=_positive_int(limits.get("tool_calls"), "limits.tool_calls"),
            repair_iterations=_positive_int(
                limits.get("repair_iterations"), "limits.repair_iterations"
            ),
        ),
        expected_behavior=_string_list(
            data.get("expected_behavior"), "expected_behavior"
        ),
    )

    if not manifest.case_id.replace("_", "").isalnum():
        raise ManifestError("case_id may contain only letters, digits, and underscores")
    minimum_tool_calls = (
        1
        + MAX_INVESTIGATION_SEARCHES
        + MAX_INVESTIGATION_READS
        + manifest.limits.repair_iterations * (MAX_RELEVANT_FILES + 2)
    )
    if manifest.limits.tool_calls < minimum_tool_calls:
        raise ManifestError(
            "limits.tool_calls must be at least "
            f"{minimum_tool_calls} for the declared repair iterations"
        )
    return manifest
