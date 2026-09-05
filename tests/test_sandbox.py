from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from repomedic.models import CommandSpec
from repomedic.sandbox import DockerSandbox, SandboxError
from tests.helpers import temporary_directory


class DockerSandboxTests(unittest.TestCase):
    def test_rejects_an_image_name_that_docker_would_parse_as_an_option(self) -> None:
        with self.assertRaisesRegex(SandboxError, "image"):
            DockerSandbox(image="--privileged")

    @patch("repomedic.sandbox.is_link_or_junction", return_value=True)
    def test_rejects_a_link_as_a_bind_mount_source(self, _link_mock) -> None:
        with temporary_directory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            workspace.mkdir()

            with self.assertRaisesRegex(SandboxError, "link"):
                DockerSandbox().build_command(
                    container_name="repomedic-case-run-public",
                    workspace=workspace,
                    evaluator_dir=None,
                    spec=CommandSpec(
                        argv=("python", "-m", "unittest"),
                        cwd="repo",
                    ),
                    kind="public",
                )

    def test_public_command_has_required_isolation_flags(self) -> None:
        with temporary_directory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            workspace.mkdir()
            sandbox = DockerSandbox(image="python:3.11-slim")

            command = sandbox.build_command(
                container_name="repomedic-case-run-public",
                workspace=workspace,
                evaluator_dir=None,
                spec=CommandSpec(
                    argv=("python", "-m", "unittest", "discover", "-s", "tests", "-v"),
                    cwd="repo",
                ),
                kind="public",
            )

        joined = " ".join(command)
        self.assertIn("--pull never", joined)
        self.assertIn("--init", command)
        self.assertIn("--network none", joined)
        self.assertIn("--read-only", command)
        self.assertIn("--cap-drop ALL", joined)
        self.assertIn("no-new-privileges", joined)
        self.assertIn("--user 65534:65534", joined)
        self.assertIn("--pids-limit 64", joined)
        self.assertIn("--memory 256m", joined)
        self.assertIn("--cpus 1.0", joined)
        self.assertIn("target=/workspace", joined)
        self.assertIn("readonly", next(value for value in command if "target=/workspace" in value))
        self.assertEqual(
            command[-7:],
            ["python", "-m", "unittest", "discover", "-s", "tests", "-v"],
        )

    def test_evaluator_mounts_tests_and_workspace_read_only(self) -> None:
        with temporary_directory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "workspace"
            evaluator = root / "evaluator"
            workspace.mkdir()
            evaluator.mkdir()
            sandbox = DockerSandbox()

            command = sandbox.build_command(
                container_name="repomedic-case-run-evaluator",
                workspace=workspace,
                evaluator_dir=evaluator,
                spec=CommandSpec(
                    argv=("python", "-m", "unittest", "discover", "-s", "hidden_tests", "-v"),
                    cwd="evaluator",
                ),
                kind="evaluator",
            )

        mounts = [value for value in command if value.startswith("type=bind")]
        self.assertEqual(len(mounts), 2)
        self.assertTrue(all("readonly" in mount for mount in mounts))
        self.assertIn("REPOMEDIC_REPO_UNDER_TEST=/workspace", command)

    @patch("repomedic.sandbox.subprocess.run")
    def test_records_real_process_result(self, run_mock) -> None:
        run_mock.return_value = subprocess.CompletedProcess(
            args=["docker"], returncode=1, stdout="failed test", stderr=""
        )
        with temporary_directory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            workspace.mkdir()
            result = DockerSandbox().run(
                case_id="case_001",
                run_id="run_001",
                workspace=workspace,
                evaluator_dir=None,
                spec=CommandSpec(argv=("python", "-m", "unittest"), cwd="repo"),
                kind="public",
                timeout_seconds=10,
            )

        self.assertEqual(result.exit_code, 1)
        self.assertFalse(result.timed_out)
        self.assertEqual(result.stdout, "failed test")

    @patch("repomedic.sandbox.subprocess.run")
    def test_timeout_forcibly_removes_container(self, run_mock) -> None:
        run_mock.side_effect = [
            subprocess.TimeoutExpired(
                cmd=["docker", "run"],
                timeout=1,
                output="partial output",
                stderr="",
            ),
            subprocess.CompletedProcess(args=["docker"], returncode=0),
        ]
        with temporary_directory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            workspace.mkdir()
            result = DockerSandbox().run(
                case_id="case_001",
                run_id="run_001",
                workspace=workspace,
                evaluator_dir=None,
                spec=CommandSpec(argv=("python", "-m", "unittest"), cwd="repo"),
                kind="public",
                timeout_seconds=1,
            )

        cleanup_command = run_mock.call_args_list[1].args[0]
        self.assertTrue(result.timed_out)
        self.assertTrue(result.infrastructure_error)
        self.assertEqual(cleanup_command[:3], ["docker", "rm", "-f"])


if __name__ == "__main__":
    unittest.main()
