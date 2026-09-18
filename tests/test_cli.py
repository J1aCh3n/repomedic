from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import json
import subprocess
import sys
import unittest
from unittest.mock import patch

from langgraph.checkpoint.sqlite import SqliteSaver

from repomedic.__main__ import _parser, main
from repomedic.graph import AgentRunner, ModelFailure, ScriptedModel
from tests.test_graph import FakeSandbox, prepared, repair_turns
from tests.helpers import temporary_directory


class CliTests(unittest.TestCase):
    def test_only_v3_commands_are_exposed(self) -> None:
        help_text = _parser().format_help()
        for name in ("fix", "decide", "status", "eval"):
            self.assertIn(name, help_text)
        self.assertNotIn("start-benchmark", help_text)

    def test_status_uses_no_model_or_api_key(self) -> None:
        with temporary_directory() as directory:
            run = prepared(Path(directory))
            with SqliteSaver.from_conn_string(str(run / "checkpoint.sqlite")) as saver:
                AgentRunner(ScriptedModel(repair_turns()), FakeSandbox(), saver).start(run)
            output = StringIO()
            with patch("repomedic.__main__.openai_model", side_effect=AssertionError("no API")), redirect_stdout(output):
                self.assertEqual(main(["status", str(run)]), 0)
            self.assertEqual(json.loads(output.getvalue())["status"], "awaiting_review")

    def test_cli_unchanged_approval_does_not_construct_a_live_model(self) -> None:
        with temporary_directory() as directory:
            run = prepared(Path(directory))
            with SqliteSaver.from_conn_string(str(run / "checkpoint.sqlite")) as saver:
                AgentRunner(ScriptedModel(repair_turns()), FakeSandbox(), saver).start(run)
            with patch("repomedic.__main__.openai_model", side_effect=AssertionError("no API")), redirect_stdout(StringIO()):
                self.assertEqual(main(["decide", str(run), "approve"]), 0)
            self.assertTrue((run / "patch.diff").exists())

    def test_decide_rejects_a_non_review_run_before_constructing_model(self) -> None:
        with temporary_directory() as directory:
            run = prepared(Path(directory))
            with SqliteSaver.from_conn_string(str(run / "checkpoint.sqlite")) as saver:
                AgentRunner(ScriptedModel([ModelFailure("failed")]), FakeSandbox(), saver).start(run)
            with patch("repomedic.__main__.openai_model", side_effect=AssertionError("no API")):
                with self.assertRaises(SystemExit) as error:
                    main(["decide", str(run), "approve"])
            self.assertEqual(error.exception.code, 2)

    def test_approval_resume_in_a_real_new_process_needs_no_api(self) -> None:
        with temporary_directory() as directory:
            run = prepared(Path(directory))
            with SqliteSaver.from_conn_string(str(run / "checkpoint.sqlite")) as saver:
                AgentRunner(ScriptedModel(repair_turns()), FakeSandbox(), saver).start(run)
            script = ("from pathlib import Path; from langgraph.checkpoint.sqlite import SqliteSaver; "
                      "from repomedic.graph import AgentRunner, ApprovalDecision; "
                      "from repomedic.sandbox import DockerSandbox; import sys; "
                      "run=Path(sys.argv[1]); "
                      "saver=SqliteSaver.from_conn_string(str(run/'checkpoint.sqlite')); "
                      "context=saver.__enter__(); "
                      "print(AgentRunner(None,DockerSandbox(),context).resume('run',ApprovalDecision(action='approve')).status); "
                      "saver.__exit__(None,None,None)")
            process = subprocess.run([sys.executable, "-c", script, str(run)],
                                      capture_output=True, text=True, timeout=30)
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertIn("exported", process.stdout)
