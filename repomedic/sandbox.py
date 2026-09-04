from pathlib import Path
import re
import subprocess
import time

from repomedic.models import CommandSpec, TestResult


class SandboxError(ValueError):
    """Raised when a sandbox command violates a fixed execution contract."""


DEFAULT_DOCKER_IMAGE = (
    "python:3.11-slim@sha256:9534e5a8e315485d4061ed659af0fd78a284c015f9b73661b41d6bab25604534"
)


class DockerSandbox:
    backend = "docker"

    def __init__(
        self,
        image: str = DEFAULT_DOCKER_IMAGE,
        docker_executable: str = "docker",
    ) -> None:
        self.image = image
        self.docker_executable = docker_executable

    @staticmethod
    def _mount(source: Path, target: str, *, readonly: bool) -> str:
        resolved = str(source.resolve())
        if "," in resolved:
            raise SandboxError("Docker bind source paths may not contain commas")
        value = f"type=bind,source={resolved},target={target}"
        return f"{value},readonly" if readonly else value

    def build_command(
        self,
        *,
        container_name: str,
        workspace: Path,
        evaluator_dir: Path | None,
        spec: CommandSpec,
        kind: str,
    ) -> list[str]:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,62}", container_name):
            raise SandboxError("unsafe Docker container name")
        if spec.argv[:3] != ("python", "-m", "unittest"):
            raise SandboxError("only fixed python unittest commands are permitted")
        if kind not in {"public", "evaluator"}:
            raise SandboxError(f"unknown test kind: {kind}")
        if kind == "public" and (spec.cwd != "repo" or evaluator_dir is not None):
            raise SandboxError("public tests must run from the isolated repository")
        if kind == "evaluator" and (spec.cwd != "evaluator" or evaluator_dir is None):
            raise SandboxError("evaluator tests require the isolated evaluator mount")

        command = [
            self.docker_executable,
            "run",
            "--rm",
            "--pull",
            "never",
            "--init",
            "--name",
            container_name,
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--user",
            "65534:65534",
            "--pids-limit",
            "64",
            "--memory",
            "256m",
            "--cpus",
            "1.0",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m,mode=1777",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--env",
            "PYTHONHASHSEED=0",
        ]
        if kind == "public":
            command.extend(
                [
                    "--mount",
                    self._mount(workspace, "/workspace", readonly=True),
                    "--workdir",
                    "/workspace",
                ]
            )
        else:
            assert evaluator_dir is not None
            command.extend(
                [
                    "--mount",
                    self._mount(workspace, "/workspace", readonly=True),
                    "--mount",
                    self._mount(evaluator_dir, "/evaluator", readonly=True),
                    "--workdir",
                    "/evaluator",
                    "--env",
                    "REPOMEDIC_REPO_UNDER_TEST=/workspace",
                ]
            )
        command.extend([self.image, *spec.argv])
        return command

    def run(
        self,
        *,
        case_id: str,
        run_id: str,
        workspace: Path,
        evaluator_dir: Path | None,
        spec: CommandSpec,
        kind: str,
        timeout_seconds: int,
    ) -> TestResult:
        container_name = re.sub(
            r"[^a-z0-9_.-]",
            "-",
            f"repomedic-{case_id}-{run_id}-{kind}".lower(),
        )[:63]
        command = self.build_command(
            container_name=container_name,
            workspace=workspace,
            evaluator_dir=evaluator_dir,
            spec=spec,
            kind=kind,
        )
        started = time.monotonic()
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            subprocess.run(
                [self.docker_executable, "rm", "-f", container_name],
                capture_output=True,
                text=True,
                check=False,
            )
            duration_ms = round((time.monotonic() - started) * 1000)
            return TestResult(
                kind=kind,
                argv=spec.argv,
                exit_code=None,
                timed_out=True,
                duration_ms=duration_ms,
                stdout=error.stdout or "",
                stderr=error.stderr or "test command timed out",
                infrastructure_error=True,
            )
        except FileNotFoundError as error:
            duration_ms = round((time.monotonic() - started) * 1000)
            return TestResult(
                kind=kind,
                argv=spec.argv,
                exit_code=None,
                timed_out=False,
                duration_ms=duration_ms,
                stdout="",
                stderr=str(error),
                infrastructure_error=True,
            )

        duration_ms = round((time.monotonic() - started) * 1000)
        return TestResult(
            kind=kind,
            argv=spec.argv,
            exit_code=completed.returncode,
            timed_out=False,
            duration_ms=duration_ms,
            stdout=completed.stdout,
            stderr=completed.stderr,
            infrastructure_error=completed.returncode in {125, 126, 127},
        )
