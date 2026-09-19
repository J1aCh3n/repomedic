"""Network-disabled Docker operations with bounded host-side output capture."""

from pathlib import Path
from threading import Event, Lock, Thread
import re
import subprocess
import time
import uuid

from pydantic import Field

from repomedic.changes import run_path
from repomedic.task import CommandSpec, Contract


DEFAULT_DOCKER_IMAGE = (
    "python:3.11-slim@sha256:9534e5a8e315485d4061ed659af0fd78a284c015f9b73661b41d6bab25604534"
)
MAX_OUTPUT_BYTES = 1_048_576


class SandboxError(ValueError):
    """Docker configuration violates the execution boundary."""


class CommandResult(Contract):
    kind: str
    argv: tuple[str, ...]
    exit_code: int | None = None
    timed_out: bool = False
    output_limited: bool = False
    infrastructure_error: bool = False
    duration_ms: int = Field(default=0, ge=0)
    stdout: str = ""
    stderr: str = ""

    @property
    def passed(self) -> bool:
        return (self.exit_code == 0 and not self.timed_out
                and not self.output_limited and not self.infrastructure_error)


def capture_process(argv: list[str], timeout: float,
                    max_output_bytes: int = MAX_OUTPUT_BYTES) -> CommandResult:
    """Drain both pipes concurrently; exceeding the combined cap kills the process."""
    started = time.monotonic()
    try:
        process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError:
        return CommandResult(kind="command", argv=tuple(argv), infrastructure_error=True,
                             stderr="Docker executable was not found")
    buffers: dict[str, list[bytes]] = {"stdout": [], "stderr": []}
    limited, lock = Event(), Lock()
    captured = 0

    def read_pipe(name: str) -> None:
        nonlocal captured
        pipe = getattr(process, name)
        assert pipe is not None
        try:
            while chunk := pipe.read(8192):
                with lock:
                    remaining = max_output_bytes - captured
                    buffers[name].append(chunk[:remaining])
                    captured += min(len(chunk), remaining)
                    if len(chunk) > remaining:
                        limited.set()
                if limited.is_set():
                    break
        finally:
            pipe.close()

    readers = [Thread(target=read_pipe, args=(name,), daemon=True) for name in buffers]
    for reader in readers:
        reader.start()
    deadline = started + timeout
    timed_out = False
    while process.poll() is None:
        if limited.is_set():
            process.kill()
            break
        if time.monotonic() >= deadline:
            timed_out = True
            process.kill()
            break
        limited.wait(0.02)
    process.wait()
    for reader in readers:
        reader.join(timeout=2)
    return CommandResult(
        kind="command", argv=tuple(argv), exit_code=process.returncode,
        timed_out=timed_out, output_limited=limited.is_set(),
        duration_ms=round((time.monotonic() - started) * 1000),
        stdout=b"".join(buffers["stdout"]).decode("utf-8", errors="replace"),
        stderr=b"".join(buffers["stderr"]).decode("utf-8", errors="replace"),
        infrastructure_error=process.returncode in {125, 126, 127},
    )


class DockerSandbox:
    backend = "docker"

    def __init__(self, image: str = DEFAULT_DOCKER_IMAGE,
                 docker_executable: str = "docker") -> None:
        if (not image or image.startswith("-") or len(image) > 512
                or any(c.isspace() or c == "\x00" for c in image)
                or not re.search(r"@sha256:[0-9a-f]{64}$", image)):
            raise SandboxError("Docker image must be a safe, digest-pinned reference")
        self.image, self.docker_executable = image, docker_executable

    @staticmethod
    def _mount(source: Path, target: str, *, readonly: bool) -> str:
        checked = run_path(source.parent, source.name)
        if not checked.is_dir() or "," in str(checked.resolve()):
            raise SandboxError("invalid bind mount directory")
        value = f"type=bind,source={checked.resolve()},target={target}"
        return value + (",readonly" if readonly else "")

    def build_command(self, *, container_name: str, run_dir: Path,
                      argv: tuple[str, ...], kind: str = "command",
                      workspace_name: str = "workspace", readonly: bool = False,
                      evaluator_dir: Path | None = None) -> list[str]:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,62}", container_name):
            raise SandboxError("unsafe container name")
        if kind not in {"command", "public", "evaluator"}:
            raise SandboxError("unknown command kind")
        if evaluator_dir is not None and kind != "evaluator":
            raise SandboxError("Agent operations must never mount evaluator files")
        if kind == "evaluator" and (evaluator_dir is None or not readonly):
            raise SandboxError("grader requires a separate evaluator and read-only workspace")
        if not argv or any("\x00" in arg for arg in argv):
            raise SandboxError("invalid command arguments")
        workspace = run_path(run_dir, workspace_name)
        scratch = run_path(run_dir, "scratch")
        command = [
            self.docker_executable, "run", "--rm", "--pull", "never", "--init",
            "--name", container_name, "--network", "none", "--read-only",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--user", "65534:65534", "--pids-limit", "64", "--memory", "256m",
            "--cpus", "1.0", "--log-driver", "none",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m,mode=1777",
            "--env", "PYTHONDONTWRITEBYTECODE=1", "--env", "PYTHONHASHSEED=0",
            "--mount", self._mount(workspace, "/workspace", readonly=readonly),
            "--mount", self._mount(scratch, "/scratch", readonly=False),
            "--workdir", "/evaluator" if kind == "evaluator" else "/workspace",
        ]
        if evaluator_dir is not None:
            command += ["--mount", self._mount(evaluator_dir, "/evaluator", readonly=True),
                        "--env", "REPOMEDIC_REPO_UNDER_TEST=/workspace"]
        return command + [self.image, *argv]

    def _run(self, *, run_dir: Path, argv: tuple[str, ...], timeout: int,
             kind: str = "command", workspace_name: str = "workspace",
             readonly: bool = False, evaluator_dir: Path | None = None) -> CommandResult:
        name = f"repomedic-{uuid.uuid4().hex}"
        command = self.build_command(
            container_name=name, run_dir=run_dir, argv=argv, kind=kind,
            workspace_name=workspace_name, readonly=readonly, evaluator_dir=evaluator_dir,
        )
        result = capture_process(command, timeout)
        if result.timed_out or result.output_limited:
            cleanup = capture_process([self.docker_executable, "rm", "-f", name], 10)
            if cleanup.exit_code != 0:
                result = result.model_copy(update={
                    "infrastructure_error": True,
                    "stderr": result.stderr + "\nContainer cleanup failed; inspect Docker manually.",
                })
        return result.model_copy(update={"kind": kind, "argv": argv})

    def exec_command(self, run_dir: Path, command: str, timeout: int, *,
                     readonly: bool = False) -> CommandResult:
        """readonly mounts /workspace read-only; /scratch stays writable either way."""
        if not command.strip() or "\x00" in command:
            raise SandboxError("command must be non-empty and contain no null bytes")
        return self._run(run_dir=run_dir, argv=("bash", "-c", command), timeout=timeout,
                         readonly=readonly)

    def run_tests(self, run_dir: Path, spec: CommandSpec, timeout: int, *,
                  workspace_name: str = "workspace",
                  evaluator_dir: Path | None = None) -> CommandResult:
        if spec.argv[:3] != ("python", "-m", "unittest"):
            raise SandboxError("tests must use the configured unittest command")
        if (spec.cwd == "evaluator") != (evaluator_dir is not None):
            raise SandboxError("test cwd and evaluator mount disagree")
        return self._run(run_dir=run_dir, argv=spec.argv, timeout=timeout,
                         kind="evaluator" if evaluator_dir else "public",
                         workspace_name=workspace_name, readonly=True,
                         evaluator_dir=evaluator_dir)
