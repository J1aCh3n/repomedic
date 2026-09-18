"""Evaluator-only grading, never exposed through Agent tools or exported patches."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
import hashlib
import shutil
import uuid

from langgraph.checkpoint.sqlite import SqliteSaver

from repomedic.artifacts import ArtifactWriter
from repomedic.changes import ScanLimits, copy_repository, run_path, scan_tree, tree_hash
from repomedic.graph import AgentRunner, ChatModel, RunLimits, RunResult, prepare_run
from repomedic.prompt import PROMPT_VERSION
from repomedic.sandbox import DockerSandbox
from repomedic.task import Task, Taskset


def grade_run(task: Task, result: RunResult, sandbox: DockerSandbox,
              limits: RunLimits | None = None) -> dict[str, Any]:
    limits = limits or RunLimits()
    run_dir = Path(result.run_dir)
    writer = ArtifactWriter(run_dir)
    score: dict[str, Any] = {"case_id": task.case_id, "agent_status": result.status,
                              "success": False, "results": [], "usage": result.usage}
    if result.status != "ready_for_grade":
        score["status"] = "agent_failed"
    elif task.evaluator is None or task.evaluator_test is None:
        score["status"] = "invalid_task"
    else:
        scans = ScanLimits(limits.max_entries, limits.max_file_bytes)
        # This copy is created only after the Agent graph has terminated.
        grading = run_path(run_dir, "grading")
        copy_repository(run_path(run_dir, "workspace"), grading, scans, writable=False)
        baseline = run_path(run_dir, "baseline")
        # Current development tasks use tests/. Reject other layouts rather than
        # silently grading against Agent-modified public tests.
        command = task.public_test.argv
        if "-s" not in command or command[command.index("-s") + 1] != "tests":
            raise ValueError("development grading requires public unittest discovery from tests/")
        tests = run_path(grading, "tests")
        if tests.exists():
            # Verified resolved direct child of grading, itself inside run_dir.
            if not tests.resolve().is_relative_to(run_dir.resolve()):
                raise ValueError("grading test path escapes run directory")
            shutil.rmtree(tests)
        copy_repository(run_path(baseline, "tests"), tests, scans, writable=False)
        evaluator = run_path(run_dir, "grading-evaluator")
        copy_repository(task.evaluator, evaluator, scans, writable=False)
        score["evaluator_hash"] = tree_hash(scan_tree(evaluator, scans))
        score["graded_fixture_hash"] = tree_hash(scan_tree(grading, scans))
        public = sandbox.run_tests(run_dir, task.public_test, limits.command_timeout,
                                   workspace_name="grading")
        hidden = sandbox.run_tests(run_dir, task.evaluator_test, limits.command_timeout,
                                   workspace_name="grading", evaluator_dir=evaluator)
        hidden_record = hidden.model_dump(mode="json")
        # Tracebacks and arbitrary test prints may contain evaluator-only source.
        # Keep real exit/timing/command evidence and output hashes, not that text.
        hidden_record.update({"stdout": "[evaluator output withheld]",
                              "stderr": "[evaluator output withheld]",
                              "output_hash": hashlib.sha256(
                                  (hidden.stdout + "\x00" + hidden.stderr).encode("utf-8")).hexdigest()})
        score["results"] = [public.model_dump(mode="json"), hidden_record]
        score["success"] = public.passed and hidden.passed
        score["status"] = ("infrastructure_error" if public.infrastructure_error or hidden.infrastructure_error
                           else "verified" if score["success"] else "tests_failed")
    writer.write_json("grade.json", score)
    writer.append_trace("grading_completed", score)
    return score


def run_eval(taskset: Taskset, *, model: ChatModel, sandbox: DockerSandbox,
             runs_root: Path, limits: RunLimits | None = None,
             reasoning_effort: str | None = None) -> dict[str, Any]:
    limits = limits or RunLimits()
    runs_root = runs_root.resolve()
    runs_root.mkdir(parents=True, exist_ok=True)
    suite_root = run_path(runs_root, taskset.suite_id)
    suite_root.mkdir(exist_ok=True)
    identity = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    run_dir = run_path(suite_root, identity)
    run_dir.mkdir()
    writer = ArtifactWriter(run_dir)
    model_id = getattr(model, "model_name", "unknown")
    summary: dict[str, Any] = {"run_dir": str(run_dir), "suite_id": taskset.suite_id,
                              "protocol_version": "tool-loop-eval-v3", "prompt_version": PROMPT_VERSION,
                              "reasoning_effort": reasoning_effort,
                              "split": taskset.split, "task_count": len(taskset.tasks),
                              "complete": False, "success_count": 0, "success_rate": 0.0,
                              "model": model_id, "tasks": []}
    writer.write_json("config.json", {"protocol_version": "tool-loop-eval-v3",
                                       "prompt_version": PROMPT_VERSION,
                                       "taskset": str(taskset.source), "model": model_id,
                                       "reasoning_effort": reasoning_effort,
                                       "limits": limits.model_dump(),
                                       "case_ids": [task.case_id for task in taskset.tasks]})
    for task in taskset.tasks:
        case_run = prepare_run(task, run_dir / "tasks", limits=limits, model_id=model_id,
                               reasoning_effort=reasoning_effort, mode="eval", image=sandbox.image)
        with SqliteSaver.from_conn_string(str(case_run / "checkpoint.sqlite")) as saver:
            result = AgentRunner(model, sandbox, saver).start(case_run)
        score = grade_run(task, result, sandbox, limits)
        summary["tasks"].append({**score, "run_dir": str(case_run)})
        summary["success_count"] = sum(row["success"] for row in summary["tasks"])
        summary["success_rate"] = summary["success_count"] / summary["task_count"]
        writer.write_json("summary.json", summary)
    summary["complete"] = True
    writer.write_json("summary.json", summary)
    writer.write_text("summary.md", f"# Development evaluation: {taskset.suite_id}\n\n"
                      f"- Model: `{model_id}`\n- Tasks: `{summary['task_count']}`\n"
                      f"- Successful: `{summary['success_count']}`\n"
                      f"- Success rate: `{summary['success_rate']:.1%}`\n\n"
                      + "\n".join(f"- `{row['case_id']}`: `{row['agent_status']}` / `{row['status']}`"
                                    for row in summary["tasks"]) + "\n")
    return summary
