"""Docker fixture gate: clean inputs, injected failures, and maintainer reference repairs."""

from argparse import ArgumentParser
from datetime import UTC, datetime
from pathlib import Path
import os
import subprocess
import uuid

from repomedic.artifacts import ArtifactWriter
from repomedic.changes import run_path
from repomedic.graph import RunResult, prepare_run
from repomedic.grader import grade_run
from repomedic.sandbox import DockerSandbox
from repomedic.task import load_taskset


def main() -> None:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("taskset", type=Path)
    parser.add_argument("--runs-root", type=Path, default=Path("runs/fixture-gates"))
    args = parser.parse_args()
    taskset = load_taskset(args.taskset)
    args.runs_root.mkdir(parents=True, exist_ok=True)
    identity = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    gate = run_path(args.runs_root.resolve(), identity)
    gate.mkdir()
    sandbox = DockerSandbox()
    fixtures = {}
    rows = []
    benchmark_root = taskset.source.parent.parent
    for task in taskset.tasks:
        if task.fixture and task.fixture.id not in fixtures:
            clean_task = task.model_copy(update={"case_id": task.fixture.id + "_clean",
                                                 "repo": benchmark_root / "fixtures" / task.fixture.id})
            clean = prepare_run(clean_task, gate / "clean", mode="eval")
            fixtures[task.fixture.id] = sandbox.run_tests(clean, task.public_test, 60).model_dump(mode="json")
        faulty = prepare_run(task, gate / "faulty", mode="eval")
        result = RunResult(run_id=faulty.name, run_dir=str(faulty), case_id=task.case_id,
                           status="ready_for_grade", usage={})
        faulty_score = grade_run(task, result, sandbox)
        repaired = prepare_run(task, gate / "reference", mode="eval")
        workspace = run_path(repaired, "workspace")
        environment = {name: os.environ[name] for name in ("PATH", "SYSTEMROOT", "TEMP", "TMP") if name in os.environ}
        environment["GIT_CEILING_DIRECTORIES"] = str(workspace.parent.resolve())
        applied = subprocess.run(["git", "apply", str((task.evaluator / "reference.patch").resolve())],
                                  cwd=workspace, env=environment, capture_output=True,
                                  text=True, encoding="utf-8", errors="replace", check=False, timeout=30)
        if applied.returncode:
            raise RuntimeError(f"reference patch failed for {task.case_id}: {applied.stderr}")
        repaired_score = grade_run(task, result.model_copy(update={"run_id": repaired.name, "run_dir": str(repaired)}), sandbox)
        rows.append({"case_id": task.case_id, "faulty_status": faulty_score["status"],
                     "reference_status": repaired_score["status"],
                     "faulty_run": str(faulty), "reference_run": str(repaired)})
        print(f"{task.case_id}: faulty={faulty_score['status']} reference={repaired_score['status']}", flush=True)
        ArtifactWriter(gate).write_json("progress.json", {"cases": rows, "fixtures": fixtures})
    passed = (all(result["exit_code"] == 0 and not result["infrastructure_error"] for result in fixtures.values())
              and all(row["faulty_status"] == "tests_failed" and row["reference_status"] == "verified" for row in rows))
    summary = {"status": "verified" if passed else "failed", "suite_id": taskset.suite_id,
               "split": taskset.split, "case_count": len(rows), "fixtures": fixtures, "cases": rows,
               "sandbox": {"image": sandbox.image, "network": "none"}}
    ArtifactWriter(gate).write_json("summary.json", summary)
    print(f"status={summary['status']}")
    print(f"run_dir={gate}")
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
