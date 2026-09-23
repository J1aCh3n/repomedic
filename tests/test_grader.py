from pathlib import Path
import json
import unittest

from repomedic.grader import grade_run, run_eval
from repomedic.graph import ModelSettings, RunResult, ScriptedModel, prepare_run, tool_turn
from repomedic.task import CommandSpec, Task, Taskset
from repomedic.sandbox import CommandResult
from tests.helpers import temporary_directory
from tests.test_graph import FakeSandbox, scope_turn


class InspectingGraderSandbox(FakeSandbox):
    def run_tests(self, run_dir, spec, timeout, **kwargs):
        self.assertion = (run_dir / kwargs["workspace_name"] / "tests/test_app.py").read_text()
        self.evaluator_mount = kwargs.get("evaluator_dir")
        return super().run_tests(run_dir, spec, timeout, **kwargs)


def task_fixture(root):
    repo = root / "source"
    (repo / "tests").mkdir(parents=True)
    (repo / "app.py").write_text("value = 1\n")
    (repo / "tests/test_app.py").write_text("original tests\n")
    evaluator = root / "evaluator"
    evaluator.mkdir()
    (evaluator / "test_hidden.py").write_text("private evaluator\n")
    return Task(case_id="sample", issue="Repair", repo=repo,
                public_test=CommandSpec(argv=("python", "-m", "unittest", "discover", "-s", "tests")),
                evaluator=evaluator,
                evaluator_test=CommandSpec(argv=("python", "-m", "unittest", "discover", "-s", "hidden_tests"), cwd="evaluator"))


class GraderTests(unittest.TestCase):
    def test_eval_records_qwen_settings_in_summary_and_each_case(self) -> None:
        with temporary_directory() as directory:
            root = Path(directory)
            task = task_fixture(root)
            taskset = Taskset(suite_id="dev", split="development", tasks=(task,), source=root / "dev.yaml")
            settings = ModelSettings(provider="qwen", enable_thinking=False)
            summary = run_eval(taskset, model=ScriptedModel([scope_turn(), tool_turn("submit", {"summary": "done"})]),
                               sandbox=FakeSandbox(), runs_root=root / "evals", model_settings=settings)
            self.assertEqual(summary["model_settings"], settings.model_dump())
            for run in (Path(summary["run_dir"]), Path(summary["tasks"][0]["run_dir"])):
                config = json.loads((run / "config.json").read_text())
                self.assertEqual(config["model_settings"], settings.model_dump())
                self.assertNotIn("api_key", config["model_settings"])

    def test_restores_tests_in_separate_copy_and_does_not_export(self) -> None:
        with temporary_directory() as directory:
            root = Path(directory)
            task = task_fixture(root)
            run = prepare_run(task, root / "runs", mode="eval")
            (run / "workspace/tests/test_app.py").write_text("tampered tests\n")
            (run / "workspace/tests/test_added.py").write_text("fake test\n")
            result = RunResult(run_id=run.name, run_dir=str(run), case_id="sample",
                               status="ready_for_grade", usage={})
            sandbox = InspectingGraderSandbox()
            score = grade_run(task, result, sandbox)
            self.assertTrue(score["success"])
            self.assertEqual(sandbox.assertion, "original tests\n")
            self.assertEqual((run / "workspace/tests/test_app.py").read_text(), "tampered tests\n")
            self.assertFalse((run / "grading/tests/test_added.py").exists())
            self.assertNotEqual(sandbox.evaluator_mount, task.evaluator)
            self.assertTrue(sandbox.evaluator_mount.is_relative_to(run))
            self.assertFalse((run / "patch.diff").exists())
            self.assertEqual((task.repo / "tests/test_app.py").read_text(), "original tests\n")

    def test_failed_agent_is_preserved_and_cannot_score_as_success(self) -> None:
        with temporary_directory() as directory:
            root = Path(directory)
            task = task_fixture(root)
            run = prepare_run(task, root / "runs", mode="eval")
            result = RunResult(run_id=run.name, run_dir=str(run), case_id="sample",
                               status="budget_exhausted", usage={})
            sandbox = FakeSandbox()
            score = grade_run(task, result, sandbox)
            self.assertFalse(score["success"])
            self.assertEqual(score["agent_status"], "budget_exhausted")
            self.assertEqual(sandbox.test_calls, 0)
            self.assertTrue((run / "grade.json").exists())

    def test_evaluator_traceback_source_is_not_logged(self) -> None:
        class SourcePrintingSandbox(FakeSandbox):
            def run_tests(self, run_dir, spec, timeout, **kwargs):
                return CommandResult(kind="evaluator" if kwargs.get("evaluator_dir") else "public",
                                     argv=spec.argv, exit_code=0,
                                     stdout="evaluator-only-source" if kwargs.get("evaluator_dir") else "OK")
        with temporary_directory() as directory:
            root = Path(directory)
            task = task_fixture(root)
            run = prepare_run(task, root / "runs", mode="eval")
            result = RunResult(run_id=run.name, run_dir=str(run), case_id="sample",
                               status="ready_for_grade", usage={})
            score = grade_run(task, result, SourcePrintingSandbox())
            self.assertTrue(score["success"])
            self.assertNotIn("evaluator-only-source", json.dumps(score))
            self.assertEqual(len(score["results"][1]["output_hash"]), 64)
            self.assertNotIn("evaluator-only-source", (run / "trace.jsonl").read_text())

    def test_eval_summary_includes_every_failure(self) -> None:
        with temporary_directory() as directory:
            root = Path(directory)
            first = task_fixture(root)
            second = first.model_copy(update={"case_id": "second"})
            taskset = Taskset(suite_id="dev", split="development", tasks=(first, second), source=root / "dev.yaml")
            model = ScriptedModel([scope_turn(), tool_turn("submit", {"summary": "done"})])
            summary = run_eval(taskset, model=model, sandbox=FakeSandbox(), runs_root=root / "evals")
            self.assertEqual(summary["task_count"], 2)
            self.assertEqual(summary["success_count"], 1)
            self.assertEqual(summary["tasks"][1]["agent_status"], "model_error")
            self.assertFalse(any(Path(summary["run_dir"]).rglob("patch.diff")))
            saved = json.loads((Path(summary["run_dir"]) / "summary.json").read_text())
            self.assertEqual(saved["success_rate"], 0.5)
            self.assertIn("expected_behavior", (Path(summary["run_dir"]) / "summary.md").read_text())
