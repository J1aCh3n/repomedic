from pathlib import Path
import json
import unittest

from langchain_core.messages import AIMessage
from langgraph.checkpoint.sqlite import SqliteSaver

from repomedic.graph import (
    AgentRunner, ApprovalDecision, ModelFailure, RunLimits, ScriptedModel, prepare_run, tool_turn,
)
from repomedic.sandbox import CommandResult, DEFAULT_DOCKER_IMAGE
from repomedic.task import CommandSpec, Task
from tests.helpers import temporary_directory


class FakeSandbox:
    image = DEFAULT_DOCKER_IMAGE

    def __init__(self, failures: int = 0, command_action=None) -> None:
        self.failures = failures
        self.test_calls = 0
        self.command_calls = 0
        self.command_action = command_action

    def run_tests(self, run_dir, spec, timeout, **kwargs):
        self.test_calls += 1
        failed = self.test_calls <= self.failures
        return CommandResult(kind="public", argv=spec.argv, exit_code=1 if failed else 0,
                             stdout="public test failed" if failed else "public tests passed")

    def exec_command(self, run_dir, command, timeout):
        self.command_calls += 1
        if self.command_action:
            self.command_action(run_dir)
        return CommandResult(kind="command", argv=("bash", "-c", command), exit_code=0,
                             stdout="HEAD" + "x" * 12000 + "TAIL")


def scope_turn():
    return tool_turn("declare_scope", {"paths": ["app.py"], "plan": "Fix the value"})


def repair_turns():
    return [scope_turn(), tool_turn("read_file", {"path": "app.py"}),
            tool_turn("edit_file", {"path": "app.py", "old": "value = 1", "new": "value = 2"}),
            tool_turn("run_tests", {}), tool_turn("submit", {"summary": "Fixed the value"})]


def prepared(root: Path, *, limits=None, mode="fix"):
    repo = root / "source"
    repo.mkdir()
    (repo / "app.py").write_text("value = 1\n", encoding="utf-8")
    (repo / "other.py").write_text("other = 1\n", encoding="utf-8")
    task = Task(case_id="sample", issue="Set value to 2", repo=repo,
                public_test=CommandSpec(argv=("python", "-m", "unittest", "discover", "-s", "tests")))
    return prepare_run(task, root / "runs", limits=limits, mode=mode, run_id="run")


class GraphTests(unittest.TestCase):
    def test_repair_approval_and_source_isolation(self) -> None:
        with temporary_directory() as directory:
            root = Path(directory)
            run = prepared(root)
            with SqliteSaver.from_conn_string(str(run / "checkpoint.sqlite")) as saver:
                runner = AgentRunner(ScriptedModel(repair_turns()), FakeSandbox(), saver)
                result = runner.start(run)
                self.assertEqual(result.status, "awaiting_review")
                self.assertFalse((run / "patch.diff").exists())
                self.assertIn("+value = 2", result.review["diff"])
                result = runner.resume("run", ApprovalDecision(action="approve"))
                self.assertEqual(result.status, "exported")
            self.assertEqual((root / "source" / "app.py").read_text(), "value = 1\n")
            self.assertTrue((run / "patch.diff").is_file())

    def test_denied_edit_then_scope_expansion(self) -> None:
        with temporary_directory() as directory:
            run = prepared(Path(directory))
            turns = [scope_turn(), tool_turn("edit_file", {"path": "other.py", "old": "1", "new": "2"}),
                     tool_turn("update_scope", {"paths": ["other.py"], "reason": "Needed across modules"}),
                     tool_turn("edit_file", {"path": "other.py", "old": "1", "new": "2"}),
                     tool_turn("submit", {"summary": "Fixed"})]
            with SqliteSaver.from_conn_string(str(run / "checkpoint.sqlite")) as saver:
                result = AgentRunner(ScriptedModel(turns), FakeSandbox(), saver).start(run)
                self.assertEqual(result.status, "awaiting_review")
                self.assertEqual(result.review["scope"], ["app.py", "other.py"])
                self.assertEqual(len(result.review["scope_history"]), 2)
                self.assertIn("denied", (run / "observations/0001.txt").read_text())

    def test_failed_check_returns_to_agent(self) -> None:
        with temporary_directory() as directory:
            run = prepared(Path(directory))
            turns = [scope_turn(), tool_turn("submit", {"summary": "first"}),
                     tool_turn("submit", {"summary": "second"})]
            model = ScriptedModel(turns)
            with SqliteSaver.from_conn_string(str(run / "checkpoint.sqlite")) as saver:
                result = AgentRunner(model, FakeSandbox(failures=1), saver).start(run)
                self.assertEqual(result.status, "awaiting_review")
                self.assertIn("Submission checks failed", model.calls[-1][-1].content)

    def test_outside_scope_changes_return_to_agent(self) -> None:
        with temporary_directory() as directory:
            run = prepared(Path(directory))
            def modify(run_dir):
                (run_dir / "workspace/other.py").write_text("other = 2\n")
            turns = [scope_turn(), tool_turn("bash", {"command": "modify other"}),
                     tool_turn("submit", {"summary": "first"}),
                     tool_turn("update_scope", {"paths": ["other.py"], "reason": "Explain actual change"}),
                     tool_turn("submit", {"summary": "second"})]
            with SqliteSaver.from_conn_string(str(run / "checkpoint.sqlite")) as saver:
                result = AgentRunner(ScriptedModel(turns), FakeSandbox(command_action=modify), saver).start(run)
                self.assertEqual(result.status, "awaiting_review")
                self.assertIn('"outside_scope":["other.py"]',
                              (run / "trace.jsonl").read_text())

    def test_protected_shell_change_terminates(self) -> None:
        with temporary_directory() as directory:
            run = prepared(Path(directory))
            def modify(run_dir):
                (run_dir / "workspace/.env.local").write_text("private")
            with SqliteSaver.from_conn_string(str(run / "checkpoint.sqlite")) as saver:
                runner = AgentRunner(ScriptedModel([scope_turn(), tool_turn("bash", {"command": "bad"})]),
                                     FakeSandbox(command_action=modify), saver)
                result = runner.start(run)
                self.assertEqual(result.status, "policy_violation")
                self.assertFalse((run / "patch.diff").exists())

    def test_output_truncated_but_bounded_full_observation_saved(self) -> None:
        with temporary_directory() as directory:
            run = prepared(Path(directory))
            model = ScriptedModel([scope_turn(), tool_turn("bash", {"command": "print"}),
                                   tool_turn("submit", {"summary": "done"})])
            with SqliteSaver.from_conn_string(str(run / "checkpoint.sqlite")) as saver:
                result = AgentRunner(model, FakeSandbox(), saver).start(run)
                self.assertEqual(result.status, "awaiting_review")
                observation = model.calls[-1][-1].content
                self.assertLessEqual(len(observation), 8000)
                self.assertIn("HEAD", observation)
                self.assertIn("TAIL", observation)
                self.assertGreater((run / "observations/0001.txt").stat().st_size, 8000)

    def test_budgets_and_model_failure(self) -> None:
        examples = [
            (RunLimits(max_tokens=1), [tool_turn("declare_scope", {"paths": ["app.py"], "plan": "fix"}, tokens=1)], "budget_exhausted"),
            (RunLimits(max_tool_calls=1), [scope_turn(), tool_turn("read_file", {"path": "app.py"}), tool_turn("submit", {"summary": "done"})], "budget_exhausted"),
            (RunLimits(recursion_limit=6), [scope_turn()] + [AIMessage(content="continue")] * 10, "budget_exhausted"),
            (RunLimits(), [ModelFailure("API failed")], "model_error"),
        ]
        for limits, turns, expected in examples:
            with self.subTest(expected=expected), temporary_directory() as directory:
                run = prepared(Path(directory), limits=limits)
                with SqliteSaver.from_conn_string(str(run / "checkpoint.sqlite")) as saver:
                    result = AgentRunner(ScriptedModel(turns), FakeSandbox(), saver).start(run)
                    self.assertEqual(result.status, expected)
                    self.assertTrue((run / "result.json").exists())
                    self.assertFalse((run / "patch.diff").exists())

    def test_new_connection_resumes_approval_and_rejects_repeat_resume(self) -> None:
        with temporary_directory() as directory:
            run = prepared(Path(directory))
            checkpoint = str(run / "checkpoint.sqlite")
            with SqliteSaver.from_conn_string(checkpoint) as saver:
                AgentRunner(ScriptedModel(repair_turns()), FakeSandbox(), saver).start(run)
            with SqliteSaver.from_conn_string(checkpoint) as saver:
                runner = AgentRunner(None, FakeSandbox(), saver)
                self.assertEqual(runner.inspect("run").status, "awaiting_review")
                self.assertEqual(runner.resume("run", ApprovalDecision(action="approve")).status, "exported")
                with self.assertRaises(ValueError):
                    runner.resume("run", ApprovalDecision(action="approve"))

    def test_revise_and_reject(self) -> None:
        with temporary_directory() as directory:
            run = prepared(Path(directory))
            model = ScriptedModel(repair_turns() + [tool_turn("submit", {"summary": "updated"})])
            with SqliteSaver.from_conn_string(str(run / "checkpoint.sqlite")) as saver:
                runner = AgentRunner(model, FakeSandbox(), saver)
                runner.start(run)
                result = runner.resume("run", ApprovalDecision(action="revise", feedback="Check again"))
                self.assertEqual(result.status, "awaiting_review")
                self.assertIn("Check again", model.calls[-1][-1].content)
                self.assertEqual(runner.resume("run", ApprovalDecision(action="reject")).status, "rejected")
                self.assertFalse((run / "patch.diff").exists())

    def test_changed_workspace_requires_fresh_review(self) -> None:
        with temporary_directory() as directory:
            run = prepared(Path(directory))
            model = ScriptedModel(repair_turns() + [tool_turn("submit", {"summary": "new content"})])
            with SqliteSaver.from_conn_string(str(run / "checkpoint.sqlite")) as saver:
                runner = AgentRunner(model, FakeSandbox(), saver)
                original = runner.start(run)
                (run / "workspace/app.py").write_text("value = 3\n")
                updated = runner.resume("run", ApprovalDecision(action="approve"))
                self.assertEqual(updated.status, "awaiting_review")
                self.assertNotEqual(original.review["diff_hash"], updated.review["diff_hash"])
                self.assertFalse((run / "patch.diff").exists())

    def test_eval_skips_review_and_never_exports(self) -> None:
        with temporary_directory() as directory:
            run = prepared(Path(directory), mode="eval")
            with SqliteSaver.from_conn_string(str(run / "checkpoint.sqlite")) as saver:
                result = AgentRunner(ScriptedModel(repair_turns()), FakeSandbox(), saver).start(run)
                self.assertEqual(result.status, "ready_for_grade")
                self.assertFalse((run / "patch.diff").exists())

    def test_multiple_calls_fail_before_execution(self) -> None:
        with temporary_directory() as directory:
            run = prepared(Path(directory))
            turn = AIMessage(content="", tool_calls=[
                {"name": "bash", "args": {"command": "bad"}, "id": "one", "type": "tool_call"},
                {"name": "submit", "args": {"summary": "done"}, "id": "two", "type": "tool_call"}])
            sandbox = FakeSandbox()
            with SqliteSaver.from_conn_string(str(run / "checkpoint.sqlite")) as saver:
                result = AgentRunner(ScriptedModel([scope_turn(), turn]), sandbox, saver).start(run)
                self.assertEqual(result.status, "model_error")
                self.assertEqual(sandbox.command_calls, 0)

    def test_unknown_tool_and_invalid_arguments_are_observations(self) -> None:
        with temporary_directory() as directory:
            run = prepared(Path(directory))
            turns = [scope_turn(), tool_turn("host_shell", {"command": "bad"}),
                     tool_turn("edit_file", {"path": "app.py", "old": "", "new": "bad"}),
                     tool_turn("read_file", {"path": "../private"}),
                     tool_turn("update_scope", {"paths": [".env.local"], "reason": "bad"}),
                     tool_turn("submit", {"summary": "done"})]
            with SqliteSaver.from_conn_string(str(run / "checkpoint.sqlite")) as saver:
                result = AgentRunner(ScriptedModel(turns), FakeSandbox(), saver).start(run)
                self.assertEqual(result.status, "awaiting_review")
                self.assertEqual(result.usage["tool_calls"], 5)
                for number in range(1, 5):
                    self.assertIn("Error", (run / f"observations/{number:04d}.txt").read_text())

    def test_credential_redaction_cannot_silently_change_an_exported_patch(self) -> None:
        with temporary_directory() as directory:
            run = prepared(Path(directory))
            turns = [scope_turn(), tool_turn("edit_file", {
                "path": "app.py", "old": "value = 1", "new": "password = 'dummy-secret'"}),
                tool_turn("submit", {"summary": "done"})]
            with SqliteSaver.from_conn_string(str(run / "checkpoint.sqlite")) as saver:
                result = AgentRunner(ScriptedModel(turns), FakeSandbox(), saver).start(run)
                self.assertNotEqual(result.status, "awaiting_review")
                self.assertFalse((run / "patch.diff").exists())
                self.assertIn("redaction", (run / "test-results.json").read_text())
