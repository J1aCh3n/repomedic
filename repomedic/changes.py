"""Bounded, content-based snapshots and patches for disposable workspaces."""

from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
import difflib
import hashlib
import json
import os
import shutil
import stat


class SafetyError(ValueError):
    """A path, file type, or repository size violates the runtime boundary."""


class PatchError(ValueError):
    """A change cannot be represented by the supported UTF-8 text patch."""


@dataclass(frozen=True)
class ScanLimits:
    max_entries: int = 10_000
    max_file_bytes: int = 2_000_000


def normalize_path(value: str) -> str:
    text = value.replace("\\", "/")
    posix, windows = PurePosixPath(text), PureWindowsPath(text)
    if (not text or "\x00" in text or posix.is_absolute() or windows.drive
            or windows.is_absolute() or ".." in posix.parts or posix.as_posix() == "."
            or any(ord(c) < 32 or c in ':"<>|?*' for c in text)):
        raise SafetyError("path must name a safe relative file")
    return posix.as_posix()


def protected_path(value: str) -> bool:
    return any(part.lower() in {".git", "evaluator"} or part.lower().startswith(".env")
               for part in PurePosixPath(value.replace("\\", "/")).parts)


def _metadata(path: Path) -> os.stat_result:
    metadata = path.lstat()
    tags = {getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", -1),
            getattr(stat, "IO_REPARSE_TAG_SYMLINK", -2)}
    if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_reparse_tag", 0) in tags:
        raise SafetyError(f"links and junctions are forbidden: {path.name}")
    return metadata


def resolve_within(root: Path, relative: str) -> Path:
    normalized = normalize_path(relative)
    _metadata(root)
    resolved_root = root.resolve()
    path = resolved_root
    for segment in PurePosixPath(normalized).parts:
        path = path / segment
        if path.exists() or path.is_symlink():
            _metadata(path)
    if not path.resolve().is_relative_to(resolved_root):
        raise SafetyError("path escapes configured root")
    return path


def scan_tree(root: Path, limits: ScanLimits = ScanLimits(), *,
              skip_protected: bool = False) -> dict[str, str]:
    """Reject special entries before opening them; hash ordinary files in chunks."""
    if not stat.S_ISDIR(_metadata(root).st_mode):
        raise SafetyError("repository root must be an ordinary directory")
    result: dict[str, str] = {}
    pending = [root]
    entry_count = 0
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                path = Path(entry.path)
                relative = path.relative_to(root).as_posix()
                if skip_protected and protected_path(relative):
                    continue
                if protected_path(relative):
                    raise SafetyError(f"protected path is present: {relative}")
                normalize_path(relative)
                entry_count += 1
                if entry_count > limits.max_entries:
                    raise SafetyError("repository exceeds maximum entries")
                metadata = _metadata(path)
                if stat.S_ISDIR(metadata.st_mode):
                    pending.append(path)
                    continue
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink > 1:
                    raise SafetyError(f"only ordinary, unlinked files are accepted: {relative}")
                if metadata.st_size > limits.max_file_bytes:
                    raise SafetyError(f"file exceeds maximum size: {relative}")
                digest = hashlib.sha256()
                size = 0
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(64 * 1024), b""):
                        size += len(chunk)
                        if size > limits.max_file_bytes:
                            raise SafetyError(f"file exceeds maximum size: {relative}")
                        digest.update(chunk)
                result[relative] = digest.hexdigest()
    return dict(sorted(result.items()))


def copy_repository(source: Path, target: Path, limits: ScanLimits = ScanLimits(), *,
                    skip_protected: bool = True, writable: bool = True) -> None:
    files = scan_tree(source, limits, skip_protected=skip_protected)
    target.mkdir(parents=True, exist_ok=False)
    for directory, names, _ in os.walk(source, followlinks=False):
        names[:] = [name for name in names
                    if not (skip_protected and protected_path(
                        (Path(directory) / name).relative_to(source).as_posix()))]
        relative = Path(directory).relative_to(source)
        (target / relative).mkdir(parents=True, exist_ok=True)
    for relative in files:
        origin = resolve_within(source, relative)
        destination = resolve_within(target, relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(origin, destination)
        # UID 65534 must be able to edit disposable bind mounts on Linux too.
        destination.chmod(0o666 if writable else 0o644)
    for directory, _, _ in os.walk(target):
        Path(directory).chmod(0o777 if writable else 0o755)


def run_path(run_dir: Path, name: str) -> Path:
    """Only fixed, direct children of a trusted run directory may be operated on."""
    path = resolve_within(run_dir, name)
    if path.parent != run_dir.resolve():
        raise SafetyError("run path must be a direct child of the run directory")
    return path


def changed_paths(before: dict[str, str], after: dict[str, str]) -> list[str]:
    return sorted(path for path in before.keys() | after.keys()
                  if before.get(path) != after.get(path))


def tree_hash(snapshot: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()


def diff_hash(diff: str) -> str:
    return hashlib.sha256(diff.encode("utf-8")).hexdigest()


def build_diff(baseline: Path, workspace: Path, limits: ScanLimits = ScanLimits()) -> str:
    before, after = scan_tree(baseline, limits), scan_tree(workspace, limits)
    sections: list[str] = []
    for relative in changed_paths(before, after):
        try:
            old = _read_text(baseline / relative) if relative in before else ""
            new = _read_text(workspace / relative) if relative in after else ""
        except UnicodeDecodeError as error:
            raise PatchError(f"binary patches are unsupported: {relative}") from error
        if "\x00" in old or "\x00" in new:
            raise PatchError(f"binary patches are unsupported: {relative}")
        # diff --git also represents empty file additions/deletions.
        sections.append(f"diff --git a/{relative} b/{relative}\n")
        if relative not in before:
            sections.append("new file mode 100644\n")
        elif relative not in after:
            sections.append("deleted file mode 100644\n")
        for line in difflib.unified_diff(
            _diff_lines(old), _diff_lines(new),
            fromfile=f"a/{relative}" if relative in before else "/dev/null",
            tofile=f"b/{relative}" if relative in after else "/dev/null",
        ):
            sections.append(line if line.endswith("\n") else
                            line + "\n\\ No newline at end of file\n")
    return "".join(sections)


def _diff_lines(text: str) -> list[str]:
    # Git delimits lines with LF; other Unicode separators remain file contents.
    parts = text.split("\n")
    return [part + "\n" for part in parts[:-1]] + ([parts[-1]] if parts[-1] else [])


def _read_text(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as stream:
        return stream.read()
