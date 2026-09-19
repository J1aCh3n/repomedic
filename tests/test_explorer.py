from pathlib import Path
import json
import unittest

from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ValidationError

from repomedic.explorer import Explorer, ExplorerReport, make_explorer_tools, verify_findings
from repomedic.graph import AgentRunner, RunLimits, ScriptedModel, prepare_run, tool_turn
from repomedic.sandbox import CommandResult, DEFAULT_DOCKER_IMAGE
from repomedic.task import CommandSpec, Task
from tests.helpers import temporary_directory


class ExplorerSandbox:
    """Records how commands were run; optionally mutates the workspace to simulate a breach."""
    image = DEFAULT_DOCKER_IMAGE

    def __init__(self, breach: bool = False) -> None:
        self.readonly_flags: list[bool] = []
        self.breach = breach

    def exec_command(self, run_dir, command, timeout, *, readonly=False):
        self.readonly_flags.append(readonly)
        if self.breach:
            (Path(run_dir) / "workspace" / "app.py").write_text("value = 99\n", encoding="utf-8")
        return CommandResult(kind="command", argv=("bash", "-c", command), exit_code=0, stdout="ok")

    def run_tests(self, run_dir, spec, timeout, **kwargs):
        return CommandResult(kind="public", argv=spec.argv, exit_code=0, stdout="passed")


def prepared(root: Path, *, agents="explorer", limits=None) -> Path:
    repo = root / "source"
    repo.mkdir()
    (repo / "app.py").write_text("value = 1\n", encoding="utf-8")
    task = Task(case_id="sample", issue="Set value to 2", repo=repo,
                public_test=CommandSpec(argv=("python", "-m", "unittest", "discover", "-s", "tests")))
    return prepare_run(task, root / "runs", run_id="run", agents=agents, limits=limits)


def scope():
    return tool_turn("declare_scope", {"paths": ["app.py"], "plan": "Fix the value"}, tokens=100)


def report(**extra):
    return tool_turn("report", {"answer": "app.py line 1 sets value", "confidence": "high",
                                "findings": [{"path": "app.py", "start_line": 1, "end_line": 1,
                                              "claim": "value assigned"}], **extra}, tokens=300)


def delegation_turns():
    return [scope(),
            tool_turn("delegate_explore", {"question": "Where is value set?"}, tokens=100),
            tool_turn("read_file", {"path": "app.py"}, tokens=300),
            report(),
            tool_turn("edit_file", {"path": "app.py", "old": "value = 1", "new": "value = 2"}, tokens=100),
            tool_turn("submit", {"summary": "Set value to 2"}, tokens=100)]


def explorer_state(root: Path) -> dict:
    (root / "workspace").mkdir()
    (root / "workspace" / "app.py").write_text("a = 1\nb = 2\n", encoding="utf-8")
    return {"run_dir": str(root), "limits": {"max_file_bytes": 2_000_000, "command_timeout": 60},
            "scope": []}


class ExplorerUnitTests(unittest.TestCase):
    def test_unverified_findings_are_marked(self) -> None:
        with temporary_directory() as directory:
            state = explorer_state(Path(directory))
            checked = verify_findings(ExplorerReport(answer="x", confidence="low", findings=[
                {"path": "app.py", "start_line": 2, "end_line": 2, "claim": "real"},
                {"path": "app.py", "start_line": 3, "end_line": 9, "claim": "invented lines"},
                {"path": "ghost.py", "start_line": 1, "end_line": 1, "claim": "invented file"},
                {"path": "../outside.py", "start_line": 1, "end_line": 1, "claim": "escape"},
            ]), state)
        self.assertEqual([item["verified"] for item in checked], [True, False, False, False])
        self.assertIn("only 2 lines", checked[1]["reason"])

    def test_report_accepts_json_string_arrays_but_still_validates(self) -> None:
        # Found in a live Qwen run: nested arrays arrived as JSON strings and every report failed.
        report_tool = next(tool for tool in make_explorer_tools(ExplorerSandbox()) if tool.name == "report")
        findings = [{"path": "a.py", "start_line": 1, "end_line": 2, "claim": "c"}]
        def call(value, questions=None):
            return report_tool.invoke({"name": "report", "id": "c1", "type": "tool_call", "args": {
                "answer": "x", "confidence": "high", "findings": value, "open_questions": questions}})
        self.assertEqual(call(json.dumps(findings)).update["report"]["findings"], findings)
        self.assertEqual(call(findings, json.dumps(["q?"])).update["report"]["open_questions"], ["q?"])
        for bad in ("not json", json.dumps([{"path": "a.py"}])):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                call(bad)
        advertised = report_tool.tool_call_schema.model_json_schema()["properties"]["findings"]
        self.assertEqual(advertised["anyOf"][0]["type"], "array")

    def _run(self, root: Path, turns, *, max_tool_calls=5, sandbox=None):
        state = explorer_state(root)
        return Explorer(ScriptedModel(turns), sandbox or ExplorerSandbox()).run(
            question="Where is b set?", issue="b is wrong", files=["app.py"],
            run_dir=state["run_dir"], limits=state["limits"], max_tool_calls=max_tool_calls,
            max_tokens=50_000, delegation_id="d1")

    def test_explorer_bash_is_readonly(self) -> None:
        with temporary_directory() as directory:
            sandbox = ExplorerSandbox()
            result = self._run(Path(directory), [tool_turn("bash", {"command": "grep -n b app.py"}),
                                                  report()], sandbox=sandbox)
        self.assertEqual(result["status"], "done")
        self.assertEqual(sandbox.readonly_flags, [True])

    def test_final_notice_salvages_report(self) -> None:
        with temporary_directory() as directory:
            # Build a fresh message per turn: add_messages would treat a reused object as the same message.
            model_turns = [tool_turn("read_file", {"path": "app.py"}) for _ in range(2)] + [report()]
            result = self._run(Path(directory), model_turns, max_tool_calls=2)
        self.assertEqual(result["status"], "done")
        self.assertIsNotNone(result["report"])

    def test_token_threshold_also_triggers_final_notice(self) -> None:
        # Found in a live run: tokens ran out long before the tool budget, losing all work.
        def run(final_tokens: int):
            with temporary_directory() as directory:
                state = explorer_state(Path(directory))
                small_report = report()
                small_report.usage_metadata = {"input_tokens": final_tokens, "output_tokens": 0,
                                               "total_tokens": final_tokens}
                model = ScriptedModel([tool_turn("read_file", {"path": "app.py"}, tokens=400)
                                       for _ in range(2)] + [small_report])
                return model, Explorer(model, ExplorerSandbox()).run(
                    question="q", issue="i", files=["app.py"], run_dir=state["run_dir"],
                    limits=state["limits"], max_tool_calls=15, max_tokens=1000, delegation_id="d1")
        model, result = run(final_tokens=100)
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["tool_calls"], 3)   # two reads, then report after 800 >= 750 tokens
        self.assertIn("nearly exhausted", model.calls[-1][-1].content)
        # The cap stays hard: a final call that itself crosses max_tokens is not accepted.
        _, over = run(final_tokens=300)
        self.assertEqual(over["status"], "budget_exhausted")

    def test_final_notice_without_report_is_budget_exhausted(self) -> None:
        with temporary_directory() as directory:
            result = self._run(Path(directory), [tool_turn("read_file", {"path": "app.py"}) for _ in range(3)],
                               max_tool_calls=2)
        self.assertEqual(result["status"], "budget_exhausted")
        self.assertIsNone(result["report"])


class DelegationTests(unittest.TestCase):
    def test_explorer_context_is_isolated(self) -> None:
        with temporary_directory() as directory:
            run = prepared(Path(directory))
            model = ScriptedModel(delegation_turns())
            result = AgentRunner(model, ExplorerSandbox(), InMemorySaver(), agents="explorer").start(run)
        self.assertEqual(result.status, "awaiting_review")
        explorer_first_call = model.calls[2]
        # System prompt + exactly one human message; none of the main agent's history.
        self.assertEqual(len(explorer_first_call), 2)
        self.assertEqual(json.loads(explorer_first_call[1].content),
                         {"issue": "Set value to 2", "question": "Where is value set?", "files": ["app.py"]})
        self.assertNotIn("Fix the value", str(explorer_first_call))
        # The main agent receives only the formatted report, not the explorer's file reads.
        returned = model.calls[4][-1]
        self.assertEqual(returned.name, "delegate_explore")
        self.assertIn("[verified]", returned.content)
        self.assertNotIn("1: value = 1", str(model.calls[4]))

    def test_usage_merges_into_run_budget(self) -> None:
        with temporary_directory() as directory:
            run = prepared(Path(directory))
            result = AgentRunner(ScriptedModel(delegation_turns()), ExplorerSandbox(),
                                 InMemorySaver(), agents="explorer").start(run)
            trace = [json.loads(line) for line in (run / "trace.jsonl").read_text().splitlines()]
        self.assertEqual(result.usage["total_tokens"], 1000)
        self.assertEqual(result.usage["explorer_tokens"], 600)
        self.assertEqual(result.usage["model_calls"], 6)
        self.assertEqual(result.usage["tool_calls"], 3)          # main agent only
        self.assertEqual(result.usage["explorer_tool_calls"], 2)
        self.assertEqual(result.usage["delegations"], 1)
        agents = [event["data"]["agent"] for event in trace if event["event"] == "model_completed"]
        self.assertEqual(agents, ["main", "main", "explorer", "explorer", "main", "main"])
        finished = next(event for event in trace if event["event"] == "delegation_finished")
        self.assertEqual(finished["data"]["delegation_id"], "d1")

    def test_explorer_budget_capped_by_remaining_run_budget(self) -> None:
        with temporary_directory() as directory:
            run = prepared(Path(directory), limits=RunLimits(max_tokens=450, explorer_max_tokens=60_000))
            model = ScriptedModel(delegation_turns())
            result = AgentRunner(model, ExplorerSandbox(), InMemorySaver(), agents="explorer").start(run)
            started = next(json.loads(line) for line in (run / "trace.jsonl").read_text().splitlines()
                           if json.loads(line)["event"] == "delegation_started")
        self.assertEqual(started["data"]["token_budget"], 250)   # 450 - 200 already spent
        self.assertEqual(result.status, "budget_exhausted")

    def test_workspace_change_during_delegation_is_policy_violation(self) -> None:
        with temporary_directory() as directory:
            run = prepared(Path(directory))
            model = ScriptedModel([scope(), tool_turn("delegate_explore", {"question": "q"}),
                                   tool_turn("bash", {"command": "ls"}), report()])
            result = AgentRunner(model, ExplorerSandbox(breach=True), InMemorySaver(),
                                 agents="explorer").start(run)
        self.assertEqual(result.status, "policy_violation")
        self.assertIn("read-only", result.error)

    def test_delegation_limit(self) -> None:
        with temporary_directory() as directory:
            run = prepared(Path(directory), limits=RunLimits(max_delegations=1))
            turns = delegation_turns()
            turns.insert(4, tool_turn("delegate_explore", {"question": "again?"}))
            model = ScriptedModel(turns)
            result = AgentRunner(model, ExplorerSandbox(), InMemorySaver(), agents="explorer").start(run)
        self.assertEqual(result.status, "awaiting_review")
        denied = model.calls[5][-1]
        self.assertEqual(denied.status, "error")
        self.assertIn("delegation limit", denied.content)
        self.assertEqual(result.usage["delegations"], 1)

    def test_single_mode_has_no_delegate_tool(self) -> None:
        with temporary_directory() as directory:
            run = prepared(Path(directory), agents="single")
            runner = AgentRunner(ScriptedModel([]), ExplorerSandbox(), InMemorySaver())
            self.assertEqual(json.loads((run / "config.json").read_text())["agents"], "single")
        self.assertNotIn("delegate_explore", [tool.name for tool in runner.tools])
        self.assertNotIn("delegate_explore", runner.agent_prompt)
        explorer = AgentRunner(ScriptedModel([]), ExplorerSandbox(), InMemorySaver(), agents="explorer")
        self.assertIn("delegate_explore", [tool.name for tool in explorer.tools])
        self.assertTrue(explorer.agent_prompt.startswith(runner.agent_prompt))

    def test_agents_mode_must_match_config(self) -> None:
        with temporary_directory() as directory:
            run = prepared(Path(directory), agents="explorer")
            with self.assertRaisesRegex(ValueError, "agents"):
                AgentRunner(ScriptedModel([]), ExplorerSandbox(), InMemorySaver()).start(run)


if __name__ == "__main__":
    unittest.main()
