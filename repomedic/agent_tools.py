from pathlib import Path
import difflib
import os

from repomedic.agent_schemas import TextReplacement
from repomedic.workspace import PathSafetyError, is_link_or_junction, resolve_within


class ToolExecutionError(RuntimeError):
    """Raised when a bounded repository operation cannot be completed safely."""


class ToolBudgetExceeded(ToolExecutionError):
    """Raised before an operation would exceed the manifest tool budget."""


_FORBIDDEN = (".git", ".env", "evaluator")
_MAX_FILE_BYTES = 128_000


def _matches(path: str, rule: str) -> bool:
    normalized = rule.rstrip("/")
    return path == normalized or path.startswith(f"{normalized}/")


class RepositoryTools:
    def __init__(self, root: Path, *, allowed_paths: tuple[str, ...]) -> None:
        self.root = root.resolve()
        self.allowed_paths = allowed_paths

    def _path(self, relative: str) -> Path:
        normalized = relative.replace("\\", "/")
        if any(_matches(normalized, rule) for rule in _FORBIDDEN):
            raise ToolExecutionError(f"access to {normalized!r} is forbidden")
        try:
            path = resolve_within(self.root, normalized)
        except PathSafetyError as error:
            raise ToolExecutionError(str(error)) from error
        if not path.is_file():
            raise ToolExecutionError(f"file does not exist: {normalized}")
        if is_link_or_junction(path):
            raise ToolExecutionError(f"links are forbidden: {normalized}")
        if path.stat().st_size > _MAX_FILE_BYTES:
            raise ToolExecutionError(f"file is too large to inspect: {normalized}")
        return path

    def list_files(self) -> tuple[str, ...]:
        files: list[str] = []
        for directory, directory_names, file_names in os.walk(
            self.root, followlinks=False
        ):
            directory_path = Path(directory)
            safe_directories: list[str] = []
            for name in sorted(directory_names):
                path = directory_path / name
                relative = path.relative_to(self.root).as_posix()
                if any(_matches(relative, rule) for rule in _FORBIDDEN):
                    continue
                if is_link_or_junction(path):
                    raise ToolExecutionError(f"repository contains a link: {relative}")
                safe_directories.append(name)
            directory_names[:] = safe_directories
            for name in sorted(file_names):
                path = directory_path / name
                relative = path.relative_to(self.root).as_posix()
                if any(_matches(relative, rule) for rule in _FORBIDDEN):
                    continue
                if is_link_or_junction(path):
                    raise ToolExecutionError(f"repository contains a link: {relative}")
                files.append(relative)
        return tuple(files)

    def read(self, relative: str) -> str:
        path = self._path(relative)
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError as error:
            raise ToolExecutionError(f"file is not UTF-8 text: {relative}") from error

    def search(self, query: str) -> tuple[dict[str, object], ...]:
        if not query or len(query) > 500:
            raise ToolExecutionError("search query must contain 1 to 500 characters")
        matches: list[dict[str, object]] = []
        for relative in self.list_files():
            try:
                content = self.read(relative)
            except ToolExecutionError as error:
                if "not UTF-8" in str(error) or "too large" in str(error):
                    continue
                raise
            for line_number, line in enumerate(content.splitlines(), start=1):
                if query in line:
                    matches.append(
                        {"path": relative, "line": line_number, "text": line[:1000]}
                    )
                    if len(matches) == 50:
                        return tuple(matches)
        return tuple(matches)

    def _updated_files(
        self, edits: tuple[TextReplacement, ...]
    ) -> dict[Path, tuple[str, str]]:
        updated: dict[Path, tuple[str, str]] = {}
        for edit in edits:
            normalized = edit.path.replace("\\", "/")
            if not any(_matches(normalized, rule) for rule in self.allowed_paths):
                raise ToolExecutionError(f"edit path is outside the allowlist: {normalized}")
            path = self._path(normalized)
            if path in updated:
                original, current = updated[path]
            else:
                original = self.read(normalized)
                current = original
            occurrences = current.count(edit.old)
            if occurrences == 1:
                replacement = current.replace(edit.old, edit.new, 1)
            elif occurrences == 0 and current.count(edit.new) == 1:
                replacement = current
            else:
                raise ToolExecutionError(
                    f"expected one exact match in {normalized}, found {occurrences}"
                )
            updated[path] = (original, replacement)
        return updated

    def preview(self, edits: tuple[TextReplacement, ...]) -> str:
        sections: list[str] = []
        for path, (before, after) in self._updated_files(edits).items():
            relative = path.relative_to(self.root).as_posix()
            sections.extend(
                difflib.unified_diff(
                    before.splitlines(keepends=True),
                    after.splitlines(keepends=True),
                    fromfile=f"a/{relative}",
                    tofile=f"b/{relative}",
                )
            )
        return "".join(sections)

    def apply(self, edits: tuple[TextReplacement, ...]) -> None:
        updated = self._updated_files(edits)
        for path, (_, content) in updated.items():
            temporary = path.with_name(f".{path.name}.repomedic.tmp")
            temporary.write_text(content, encoding="utf-8", newline="")
            os.replace(temporary, path)
