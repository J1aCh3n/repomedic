from pathlib import Path
import json
import unittest

from repomedic.sandbox import CommandResult
from repomedic.tools import _command_observation
from tests.helpers import temporary_directory


class CommandObservationTests(unittest.TestCase):
    def test_plaintext_preserves_newlines_status_and_structured_logs(self) -> None:
        with temporary_directory() as directory:
            result = CommandResult(kind="command", argv=("bash", "-c", "example"),
                                   timed_out=True, output_limited=True, infrastructure_error=True,
                                   duration_ms=123, stdout="line 1\nline 2\n", stderr="problem\npassword = 'dummy-secret'\n")
            command = _command_observation({"run_dir": directory}, "call_example", "bash", result.model_dump(mode="json"))
            message = command.update["messages"][0]
            self.assertEqual(message.tool_call_id, "call_example")
            self.assertEqual(message.name, "bash")
            self.assertIn("exit_code: unavailable\n", message.content)
            for flag in ("timed_out", "output_limited", "infrastructure_error"):
                self.assertIn(f"{flag}: true\n", message.content)
            self.assertIn("duration_ms: 123\n", message.content)
            self.assertIn("--- stdout ---\nline 1\nline 2\n", message.content)
            self.assertIn("--- stderr ---\nproblem\npassword = 'dummy-secret'\n", message.content)
            self.assertNotIn("\\n", message.content)
            self.assertEqual(command.update["last_command"], result.model_dump(mode="json"))
            trace = json.loads((Path(directory) / "trace.jsonl").read_text())
            self.assertEqual(trace["data"]["stdout"], result.stdout)
            self.assertNotIn("dummy-secret", trace["data"]["stderr"])

    def test_truncation_keeps_each_stream_head_tail_and_metadata(self) -> None:
        with temporary_directory() as directory:
            result = CommandResult(kind="command", argv=("bash", "-c", "example"), exit_code=1,
                                   stdout="OUT_HEAD\n" + "x" * 12000 + "\nOUT_TAIL",
                                   stderr="ERR_HEAD\n" + "y" * 12000 + "\nERR_TAIL")
            command = _command_observation({"run_dir": directory}, "call_example", "bash", result.model_dump(mode="json"))
            content = command.update["messages"][0].content
            self.assertLessEqual(len(content), 8000)
            self.assertTrue(content.startswith("exit_code: 1\n"))
            for marker in ("--- stdout ---", "OUT_HEAD", "OUT_TAIL", "--- stderr ---", "ERR_HEAD", "ERR_TAIL"):
                self.assertIn(marker, content)
            self.assertEqual(content.count("[output truncated;"), 2)
            self.assertEqual(command.update["last_command"]["stdout"], result.stdout)
            self.assertEqual(command.update["last_command"]["stderr"], result.stderr)
