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
from repomedic.graph import AgentRunner, ModelFailure, ScriptedModel, tool_turn
from tests.test_graph import FakeSandbox, prepared, repair_turns, scope_turn
from tests.helpers import temporary_directory


class CliTests(unittest.TestCase):
    def test_only_v3_commands_are_exposed(self) -> None:
        help_text = _parser().format_help()
        for name in ("fix", "decide", "status", "eval"):
            self.assertIn(name, help_text)
        self.assertNotIn("start-benchmark", help_text)

    def test_qwen_fix_revision_and_approval_use_frozen_provider_settings(self) -> None:
        with temporary_directory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "app.py").write_text("value = 1\n")
            with patch("repomedic.__main__.openai_model", return_value=ScriptedModel(repair_turns())) as factory, \
                    patch("repomedic.__main__.DockerSandbox", return_value=FakeSandbox()), redirect_stdout(StringIO()):
                self.assertEqual(main(["fix", str(source), "--issue", "repair", "--model", "qwen3.7-plus",
                    "--provider", "qwen", "--base-url", "https://example.invalid/compatible-mode/v1",
                    "--no-thinking", "--runs-root", str(root / "runs")]), 0)
            settings = factory.call_args.kwargs["settings"]
            self.assertEqual(settings.provider, "qwen")
            self.assertFalse(settings.enable_thinking)
            run = next((root / "runs/repair").iterdir())
            config = json.loads((run / "config.json").read_text())
            self.assertEqual(config["model_settings"], settings.model_dump())
            with patch("repomedic.__main__.openai_model", return_value=ScriptedModel([
                    tool_turn("submit", {"summary": "rechecked"})])) as resumed_factory, \
                    patch("repomedic.__main__.DockerSandbox", return_value=FakeSandbox()), redirect_stdout(StringIO()):
                self.assertEqual(main(["decide", str(run), "revise", "--feedback", "recheck"]), 0)
            self.assertEqual(resumed_factory.call_args.kwargs["settings"], settings)
            self.assertEqual(resumed_factory.call_args.args[0], "qwen3.7-plus")
            with patch("repomedic.__main__.openai_model", side_effect=AssertionError("no API")), redirect_stdout(StringIO()):
                self.assertEqual(main(["decide", str(run), "approve"]), 0)
            self.assertTrue((run / "patch.diff").exists())

    def test_qwen_eval_passes_provider_settings_to_factory_and_runner(self) -> None:
        with patch("repomedic.__main__.openai_model", return_value=ScriptedModel([])) as factory, \
                patch("repomedic.__main__.run_eval", return_value={"complete": True, "success_count": 12, "task_count": 12}) as evaluate, \
                redirect_stdout(StringIO()):
            self.assertEqual(main(["eval", "benchmarks/suites/initial_12.yaml", "--model", "qwen3.7-plus", "--provider", "qwen"]), 0)
        self.assertEqual(factory.call_args.kwargs["settings"].provider, "qwen")
        self.assertTrue(factory.call_args.kwargs["settings"].enable_thinking)
        self.assertEqual(evaluate.call_args.kwargs["model_settings"], factory.call_args.kwargs["settings"])

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

    def test_status_displays_the_exact_diff_that_will_be_exported(self) -> None:
        with temporary_directory() as directory:
            run = prepared(Path(directory))
            turns = [scope_turn(), tool_turn("edit_file", {"path": "app.py", "old": "value = 1",
                     "new": "def f(token: str):\n    return token"}), tool_turn("submit", {"summary": "done"})]
            with SqliteSaver.from_conn_string(str(run / "checkpoint.sqlite")) as saver:
                original = AgentRunner(ScriptedModel(turns), FakeSandbox(), saver).start(run)
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["status", str(run)]), 0)
            displayed = json.loads(output.getvalue())["review"]["diff"]
            self.assertEqual(displayed, original.review["diff"])
            self.assertIn("+def f(token: str):", displayed)
            with redirect_stdout(StringIO()):
                self.assertEqual(main(["decide", str(run), "approve"]), 0)
            self.assertEqual((run / "patch.diff").read_bytes(), displayed.encode("utf-8"))

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
