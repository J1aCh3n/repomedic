"""Tool schemas and fixed host operations; only bash runs arbitrary code, in Docker."""

from pathlib import Path
from typing import Annotated, Any
import json
import os

from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command
from pydantic import Field, model_validator

from repomedic.artifacts import ArtifactWriter
from repomedic.changes import (
    SafetyError, ScanLimits, changed_paths, normalize_path, protected_path,
    resolve_within, run_path, scan_tree,
)
from repomedic.sandbox import CommandResult, DockerSandbox
from repomedic.task import CommandSpec, Contract


class ToolDenied(ValueError):
    """A recoverable tool request is outside its declared scope or invalid."""


MAX_MODEL_OBSERVATION_CHARS = 8000


class ScopePlan(Contract):
    paths: list[str] = Field(min_length=1, max_length=100)
    plan: str = Field(min_length=1, max_length=4000)

    @model_validator(mode="after")
    def validate_paths(self) -> "ScopePlan":
        self.paths = validate_scope(self.paths)
        return self


def validate_scope(paths: list[str]) -> list[str]:
    normalized = sorted(set(normalize_path(path) for path in paths))
    if not normalized or any(protected_path(path) for path in normalized):
        raise ToolDenied("scope must name files outside protected paths")
    return normalized


def truncate_output(value: str, limit: int = MAX_MODEL_OBSERVATION_CHARS) -> str:
    if len(value) <= limit:
        return value
    marker = "\n... [output truncated; bounded full observation saved] ...\n"
    half = (limit - len(marker)) // 2
    return value[:half] + marker + value[-half:]


def scan_limits(state: dict[str, Any]) -> ScanLimits:
    return ScanLimits(state["limits"]["max_entries"], state["limits"]["max_file_bytes"])


def workspace_snapshot(state: dict[str, Any]) -> dict[str, str]:
    return scan_tree(run_path(Path(state["run_dir"]), "workspace"), scan_limits(state))


def workspace_changes(state: dict[str, Any]) -> list[str]:
    return changed_paths(state["baseline_snapshot"], workspace_snapshot(state))


def _file(state: dict[str, Any], path: str, *, editing: bool = False) -> Path:
    try:
        relative = normalize_path(path)
        if protected_path(relative):
            raise ToolDenied("protected path access denied")
        if editing and relative not in state["scope"]:
            raise ToolDenied("edit denied: file is outside the declared scope; use update_scope")
        root = run_path(Path(state["run_dir"]), "workspace")
        resolved = resolve_within(root, relative)
        if not resolved.is_file():
            raise ToolDenied("file does not exist; use bash for creating a scoped file")
        if resolved.stat().st_size > state["limits"]["max_file_bytes"]:
            raise ToolDenied("file exceeds maximum size")
        return resolved
    except SafetyError as error:
        raise ToolDenied(str(error)) from error


def make_tools(sandbox: DockerSandbox) -> list[BaseTool]:
    @tool
    def bash(command: Annotated[str, Field(min_length=1, max_length=20000)],
             state: Annotated[dict[str, Any], InjectedState],
             tool_call_id: Annotated[str, InjectedToolCallId]) -> Command:
        """Run a shell command in a fresh, network-disabled Docker container."""
        # Model-correctable input is rejected here; remaining SandboxErrors are infrastructure faults.
        if not command.strip() or "\x00" in command:
            raise ToolDenied("command must be non-empty and contain no null bytes")
        result = sandbox.exec_command(Path(state["run_dir"]), command,
                                      state["limits"]["command_timeout"])
        return _command_observation(state, tool_call_id, "bash", result.model_dump(mode="json"))

    @tool
    def read_file(path: str,
                  state: Annotated[dict[str, Any], InjectedState],
                  start: Annotated[int | None, Field(ge=1)] = None,
                  end: Annotated[int | None, Field(ge=1)] = None) -> str:
        """Read a UTF-8 workspace file, with line numbers and optional inclusive range."""
        try:
            lines = _file(state, path).read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError as error:
            raise ToolDenied("file is not UTF-8 text") from error
        first, last = start or 1, end or len(lines)
        if last < first:
            raise ToolDenied("end must be at least start")
        return "\n".join(f"{index}: {line}" for index, line in enumerate(lines, 1)
                         if first <= index <= last)

    @tool
    def edit_file(path: str,
                  old: Annotated[str, Field(min_length=1, max_length=20000)],
                  new: Annotated[str, Field(max_length=20000)],
                  state: Annotated[dict[str, Any], InjectedState]) -> str:
        """Replace one exact text occurrence in an existing file in the declared scope."""
        file = _file(state, path, editing=True)
        try:
            with file.open("r", encoding="utf-8", newline="") as stream:
                content = stream.read()
        except UnicodeDecodeError as error:
            raise ToolDenied("file is not UTF-8 text") from error
        if old == new or content.count(old) != 1:
            raise ToolDenied("old must match exactly once and differ from new")
        updated = content.replace(old, new, 1)
        if len(updated.encode("utf-8")) > state["limits"]["max_file_bytes"]:
            raise ToolDenied("edited file would exceed maximum size")
        temporary = file.with_name(f".{file.name}.repomedic.tmp")
        if temporary.exists() or temporary.is_symlink():
            raise ToolDenied("edit temporary path already exists")
        temporary.write_text(updated, encoding="utf-8", newline="")
        temporary.chmod(0o666)
        os.replace(temporary, file)
        return f"Edited {normalize_path(path)}"

    @tool
    def run_tests(state: Annotated[dict[str, Any], InjectedState],
                  tool_call_id: Annotated[str, InjectedToolCallId]) -> Command:
        """Run the configured public tests in Docker; evaluator files are never mounted."""
        result = sandbox.run_tests(Path(state["run_dir"]),
                                   CommandSpec.model_validate(state["public_test"]),
                                   state["limits"]["command_timeout"])
        return _command_observation(state, tool_call_id, "run_tests", result.model_dump(mode="json"))

    @tool
    def update_scope(paths: Annotated[list[str], Field(min_length=1, max_length=100)],
                     reason: Annotated[str, Field(min_length=1, max_length=2000)],
                     state: Annotated[dict[str, Any], InjectedState],
                     tool_call_id: Annotated[str, InjectedToolCallId]) -> Command:
        """Add exact file paths to the scope, with a reason saved for human review."""
        try:
            additions = validate_scope(paths)
            root = run_path(Path(state["run_dir"]), "workspace")
            for relative in additions:
                path = resolve_within(root, relative)
                if path.is_dir():
                    raise ToolDenied("scope paths must name exact files, not directories")
        except SafetyError as error:
            raise ToolDenied(str(error)) from error
        expanded = sorted(set(state["scope"]) | set(additions))
        if len(expanded) > 100:
            raise ToolDenied("scope exceeds 100 files")
        history = state["scope_history"] + [{"paths": additions, "reason": reason}]
        ArtifactWriter(Path(state["run_dir"])).append_trace("scope_updated", history[-1])
        return Command(update={"scope": expanded, "scope_history": history,
                               "messages": [ToolMessage(content=json.dumps({"scope": expanded}),
                                                         tool_call_id=tool_call_id)]})

    @tool
    def submit(summary: Annotated[str, Field(min_length=1, max_length=4000)],
               tool_call_id: Annotated[str, InjectedToolCallId]) -> Command:
        """Submit the workspace for deterministic scope/test checks and human review."""
        return Command(update={"submitted": True, "summary": summary,
                               "messages": [ToolMessage(content="Submitted for checks.",
                                                         tool_call_id=tool_call_id)]})

    return [bash, read_file, edit_file, run_tests, update_scope, submit]


def _command_observation(state: dict[str, Any], call_id: str, name: str,
                         result: dict[str, Any]) -> Command:
    writer = ArtifactWriter(Path(state["run_dir"]))
    writer.append_trace("command_completed", {"tool": name, **result})
    return Command(update={"last_command": result,
                           "messages": [ToolMessage(content=_format_command_result(CommandResult.model_validate(result)),
                                                     tool_call_id=call_id, name=name)]})


def _format_command_result(result: CommandResult) -> str:
    prefix = (f"exit_code: {result.exit_code if result.exit_code is not None else 'unavailable'}\n"
              f"timed_out: {str(result.timed_out).lower()}\n"
              f"output_limited: {str(result.output_limited).lower()}\n"
              f"infrastructure_error: {str(result.infrastructure_error).lower()}\n"
              f"duration_ms: {result.duration_ms}\n"
              "--- stdout ---\n")
    separator = "\n--- stderr ---\n"
    available = MAX_MODEL_OBSERVATION_CHARS - len(prefix) - len(separator)
    # Share the budget between streams, returning unused space to the larger one.
    stdout_limit = min(len(result.stdout), available // 2)
    stderr_limit = min(len(result.stderr), available - stdout_limit)
    stdout_limit = available - stderr_limit
    return (prefix + truncate_output(result.stdout, stdout_limit)
            + separator + truncate_output(result.stderr, stderr_limit))
