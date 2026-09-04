from dataclasses import dataclass
from pathlib import Path
import os
import re
import shutil
import stat


class PathSafetyError(ValueError):
    """Raised when a path escapes a configured boundary or contains a link."""


_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")


def resolve_within(root: Path, relative: str) -> Path:
    candidate_text = Path(relative)
    if candidate_text.is_absolute() or candidate_text.drive:
        raise PathSafetyError("path must be relative")
    resolved_root = root.resolve()
    candidate = (resolved_root / candidate_text).resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError as error:
        raise PathSafetyError("path escapes configured root") from error
    return candidate


def _safe_segment(value: str, label: str) -> str:
    if not _SAFE_SEGMENT.fullmatch(value):
        raise PathSafetyError(f"unsafe {label}: {value!r}")
    return value


def is_link_or_junction(path: Path) -> bool:
    if path.is_symlink():
        return True
    metadata = path.stat(follow_symlinks=False)
    reparse_tag = getattr(metadata, "st_reparse_tag", 0)
    unsafe_tags = {
        getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", -1),
        getattr(stat, "IO_REPARSE_TAG_SYMLINK", -2),
    }
    return reparse_tag in unsafe_tags


def _copy_source_tree(source_root: Path, source: Path, destination: Path) -> None:
    destination.mkdir()
    with os.scandir(source) as entries:
        for entry in sorted(entries, key=lambda item: item.name):
            path = Path(entry.path)
            if is_link_or_junction(path):
                raise PathSafetyError(f"source contains a symlink or junction: {path}")
            relative = path.relative_to(source_root).as_posix()
            if relative == ".git" or relative.startswith(".git/"):
                raise PathSafetyError("source repository must not contain .git")
            if relative == ".env":
                raise PathSafetyError("source repository must not contain .env")
            destination_path = destination / entry.name
            if entry.is_dir(follow_symlinks=False):
                _copy_source_tree(source_root, path, destination_path)
            elif entry.is_file(follow_symlinks=False):
                shutil.copyfile(path, destination_path)
            else:
                raise PathSafetyError(f"unsupported source entry: {path}")


def _remove_tree_inside(run_dir: Path, target: Path) -> None:
    resolved_run = run_dir.resolve()
    resolved_target = target.resolve()
    try:
        resolved_target.relative_to(resolved_run)
    except ValueError as error:
        raise PathSafetyError("refusing to remove a path outside the run directory") from error
    if resolved_target == resolved_run:
        raise PathSafetyError("refusing to remove the run directory itself")
    shutil.rmtree(resolved_target)


@dataclass(frozen=True)
class RunLayout:
    run_root: Path
    run_dir: Path
    workspace: Path
    case_id: str
    run_id: str

    @classmethod
    def create(cls, run_root: Path, case_id: str, run_id: str) -> "RunLayout":
        safe_case = _safe_segment(case_id, "case id")
        safe_run = _safe_segment(run_id, "run id")
        resolved_root = run_root.resolve()
        resolved_root.mkdir(parents=True, exist_ok=True)
        case_dir = resolve_within(resolved_root, safe_case)
        case_dir.mkdir(exist_ok=True)
        run_dir = resolve_within(case_dir, safe_run)
        run_dir.mkdir(exist_ok=False)
        workspace = resolve_within(run_dir, "workspace")
        return cls(
            run_root=resolved_root,
            run_dir=run_dir,
            workspace=workspace,
            case_id=safe_case,
            run_id=safe_run,
        )


def reset_workspace(source: Path, layout: RunLayout) -> None:
    resolved_source = source.resolve()
    if not resolved_source.is_dir():
        raise PathSafetyError(f"source repository does not exist: {resolved_source}")
    if layout.workspace.exists():
        _remove_tree_inside(layout.run_dir, layout.workspace)
    _copy_source_tree(resolved_source, resolved_source, layout.workspace)
