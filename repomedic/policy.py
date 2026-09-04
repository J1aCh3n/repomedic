from pathlib import Path
import difflib
import hashlib
import os

from repomedic.models import Change, PolicyReport, PolicyViolation
from repomedic.workspace import is_link_or_junction


GLOBAL_FORBIDDEN_PATHS = (".git", ".env", "evaluator")


def _file_state(root: Path) -> dict[str, tuple[str, str]]:
    state: dict[str, tuple[str, str]] = {}
    for directory, directory_names, file_names in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        for name in list(directory_names):
            path = directory_path / name
            if is_link_or_junction(path):
                state[path.relative_to(root).as_posix()] = ("symlink", "")
                directory_names.remove(name)
        for name in file_names:
            path = directory_path / name
            relative = path.relative_to(root).as_posix()
            if is_link_or_junction(path):
                state[relative] = ("symlink", "")
            else:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                state[relative] = ("file", digest)
    return state


def collect_changes(baseline: Path, workspace: Path) -> tuple[Change, ...]:
    baseline_state = _file_state(baseline)
    workspace_state = _file_state(workspace)
    changes: list[Change] = []
    for path in sorted(set(baseline_state) | set(workspace_state)):
        before = baseline_state.get(path)
        after = workspace_state.get(path)
        if before == after:
            continue
        if before is None:
            kind = "symlink" if after and after[0] == "symlink" else "added"
        elif after is None:
            kind = "deleted"
        elif after[0] == "symlink":
            kind = "symlink"
        else:
            kind = "modified"
        changes.append(Change(path=path, kind=kind))
    return tuple(changes)


def _matches_rule(path: str, rule: str) -> bool:
    normalized = rule.rstrip("/")
    return path == normalized or path.startswith(f"{normalized}/")


def evaluate_policy(
    changes: tuple[Change, ...],
    *,
    allowed: tuple[str, ...],
    forbidden: tuple[str, ...],
) -> PolicyReport:
    forbidden_rules = tuple(dict.fromkeys((*GLOBAL_FORBIDDEN_PATHS, *forbidden)))
    violations: list[PolicyViolation] = []
    for change in changes:
        if change.kind == "symlink":
            violations.append(
                PolicyViolation(change.path, "symlinks and junctions are forbidden")
            )
        elif any(_matches_rule(change.path, rule) for rule in forbidden_rules):
            violations.append(PolicyViolation(change.path, "path is explicitly forbidden"))
        elif not any(_matches_rule(change.path, rule) for rule in allowed):
            violations.append(PolicyViolation(change.path, "path is outside the allowlist"))
    return PolicyReport(
        compliant=not violations,
        changes=changes,
        violations=tuple(violations),
    )


def _text_lines(path: Path) -> list[str] | None:
    if not path.exists():
        return []
    try:
        return path.read_text(encoding="utf-8").splitlines(keepends=True)
    except UnicodeDecodeError:
        return None


def build_patch(
    baseline: Path,
    workspace: Path,
    changes: tuple[Change, ...],
) -> str:
    sections: list[str] = []
    for change in changes:
        if change.kind == "symlink":
            sections.append(f"Binary or unsafe change: {change.path} (symlink)\n")
            continue
        before = _text_lines(baseline / change.path)
        after = _text_lines(workspace / change.path)
        if before is None or after is None:
            sections.append(f"Binary change: {change.path}\n")
            continue
        sections.extend(
            difflib.unified_diff(
                before,
                after,
                fromfile=f"a/{change.path}" if change.kind != "added" else "/dev/null",
                tofile=f"b/{change.path}" if change.kind != "deleted" else "/dev/null",
            )
        )
    return "".join(sections)
