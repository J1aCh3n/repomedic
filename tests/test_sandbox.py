from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from repomedic.sandbox import DockerSandbox, SandboxError, capture_process
from tests.helpers import temporary_directory


class SandboxTests(unittest.TestCase):
    def test_command_mounts_only_disposable_workspace_and_scratch(self) -> None:
        with temporary_directory() as directory:
            run = Path(directory)
            (run / "workspace").mkdir()
            (run / "scratch").mkdir()
            sandbox = DockerSandbox()
            command = sandbox.build_command(container_name="test-run", run_dir=run,
                                             argv=("bash", "-c", "echo test"))
            self.assertIn("none", command)
            for argument in ("--read-only", "--cap-drop", "--pids-limit", "--memory", "--cpus", "--init"):
                self.assertIn(argument, command)
            self.assertIn("65534:65534", command)
            self.assertIn("never", command)
            mounts = [command[index + 1] for index, arg in enumerate(command) if arg == "--mount"]
            self.assertEqual(len(mounts), 2)
            self.assertTrue(all("readonly" not in mount for mount in mounts))
            self.assertNotIn("/evaluator", " ".join(command))
            with self.assertRaises(SandboxError):
                sandbox.build_command(container_name="test-run", run_dir=run,
                                      argv=("bash", "-c", "bad"), evaluator_dir=run)

    def test_unsafe_or_unpinned_images_rejected(self) -> None:
        for image in ("--privileged", "python:latest", "bad image", ""):
            with self.subTest(image=image), self.assertRaises(SandboxError):
                DockerSandbox(image)

    def test_output_cap_drains_both_pipes_without_deadlock(self) -> None:
        script = "import sys; sys.stdout.write('x'*200000); sys.stdout.flush(); sys.stderr.write('y'*200000)"
        result = capture_process([sys.executable, "-c", script], 5, 10000)
        self.assertTrue(result.output_limited)
        self.assertLessEqual(len(result.stdout.encode()) + len(result.stderr.encode()), 10000)

    def test_timeout_and_missing_executable_are_real_failures(self) -> None:
        result = capture_process([sys.executable, "-c", "import time; time.sleep(5)"], 0.1)
        self.assertTrue(result.timed_out)
        result = capture_process(["this-command-does-not-exist-repomedic"], 1)
        self.assertTrue(result.infrastructure_error)

    def test_timeout_cleans_up_docker_container(self) -> None:
        with temporary_directory() as directory:
            run = Path(directory)
            (run / "workspace").mkdir()
            (run / "scratch").mkdir()
            from repomedic.sandbox import CommandResult
            expired = CommandResult(kind="command", argv=(), timed_out=True)
            cleanup = CommandResult(kind="command", argv=(), exit_code=0)
            with patch("repomedic.sandbox.capture_process", side_effect=[expired, cleanup]) as capture:
                result = DockerSandbox().exec_command(run, "sleep 100", 1)
            self.assertTrue(result.timed_out)
            self.assertEqual(capture.call_args_list[-1].args[0][1:3], ["rm", "-f"])
