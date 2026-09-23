"""Development task contracts; permissions and budgets belong to the harness."""

from pathlib import Path
from typing import Any, Literal
import re

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
import yaml

from repomedic.changes import normalize_path, resolve_within


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CommandSpec(Contract):
    argv: tuple[str, ...] = Field(min_length=1)
    cwd: Literal["repo", "evaluator"] = "repo"

    @model_validator(mode="after")
    def validate_command(self) -> "CommandSpec":
        if any(not item or "\x00" in item for item in self.argv):
            raise ValueError("command arguments must be non-empty and contain no null bytes")
        if self.argv[0] != "python":
            raise ValueError("Python repositories only")
        return self


class Fixture(Contract):
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
    version: str = Field(min_length=1)


class Task(Contract):
    case_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
    issue: str = Field(min_length=1)
    repo: Path
    public_test: CommandSpec
    evaluator: Path | None = None
    evaluator_test: CommandSpec | None = None
    category: str = "repair"
    fixture: Fixture | None = None
    expected_behavior: tuple[str, ...] = ()
    runnable_entry_point: CommandSpec | None = None

    def agent_context(self) -> dict[str, Any]:
        return {"case_id": self.case_id, "issue": self.issue,
                "public_test": self.public_test.model_dump(mode="json"),
                "expected_behavior": list(self.expected_behavior)}

    @model_validator(mode="after")
    def validate_tests(self) -> "Task":
        if self.public_test.cwd != "repo":
            raise ValueError("public tests must run in repo")
        if (self.evaluator is None) != (self.evaluator_test is None):
            raise ValueError("evaluator path and command must be supplied together")
        if self.evaluator_test is not None and self.evaluator_test.cwd != "evaluator":
            raise ValueError("hidden tests must run in evaluator")
        if self.evaluator is not None:
            argv = self.public_test.argv
            if ("-s" not in argv or argv.index("-s") + 1 >= len(argv)
                    or argv[argv.index("-s") + 1] != "tests"):
                raise ValueError("development grading requires public discovery from tests/")
        for command in (self.public_test, self.evaluator_test):
            if command is not None and command.argv[:3] != ("python", "-m", "unittest"):
                raise ValueError("test commands must invoke python -m unittest")
        return self


class Taskset(Contract):
    suite_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
    split: Literal["development"]
    tasks: tuple[Task, ...] = Field(min_length=1)
    source: Path


def _yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise ValueError(f"cannot load task configuration: {path}") from error
    if not isinstance(value, dict):
        raise ValueError("task configuration must be a mapping")
    return value


def load_task(path: Path) -> Task:
    manifest = path / "manifest.yaml" if path.is_dir() else path
    data = _yaml(manifest)
    if "paths" in data or "limits" in data:
        raise ValueError("legacy paths/limits must be migrated out of task manifests")
    tests = data.pop("tests", {})
    if not isinstance(tests, dict) or set(tests) - {"public", "evaluator"}:
        raise ValueError("invalid tests configuration")
    base = manifest.resolve().parent
    data["repo"] = resolve_within(base, data.get("repo", "repo"))
    data["public_test"] = tests.get("public")
    if tests.get("evaluator") is not None:
        data["evaluator"] = resolve_within(base, data.get("evaluator", "evaluator"))
        data["evaluator_test"] = tests["evaluator"]
    try:
        return Task.model_validate(data)
    except ValidationError as error:
        raise ValueError(f"invalid task manifest: {manifest}: {error}") from error


def load_taskset(path: Path) -> Taskset:
    data = _yaml(path)
    if set(data) - {"suite_id", "description", "split", "cases"}:
        raise ValueError("unknown taskset fields")
    cases = data.get("cases")
    if not isinstance(cases, list) or not cases or not all(isinstance(x, str) for x in cases):
        raise ValueError("taskset cases must be a non-empty list of IDs")
    if len(set(cases)) != len(cases):
        raise ValueError("duplicate task IDs")
    tasks = []
    cases_root = path.resolve().parent.parent / "cases"
    for case_id in cases:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", case_id):
            raise ValueError("invalid task ID")
        normalize_path(case_id)
        task = load_task(resolve_within(cases_root, case_id))
        if task.case_id != case_id:
            raise ValueError("task ID does not match its directory")
        tasks.append(task)
    return Taskset(suite_id=data.get("suite_id"), split=data.get("split"),
                   tasks=tuple(tasks), source=path.resolve())
