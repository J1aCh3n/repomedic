"""Zero-API Docker gate: scripted repair, approval resume, and separate grading."""

from argparse import ArgumentParser
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from repomedic.artifacts import ArtifactWriter
from repomedic.graph import AgentRunner, ApprovalDecision, ScriptedModel, prepare_run, tool_turn
from repomedic.grader import grade_run
from repomedic.sandbox import DockerSandbox
from repomedic.task import load_task


def responses():
    return [tool_turn("declare_scope", {"paths": ["order_service/pricing.py"],
                                       "plan": "Make the bulk discount threshold inclusive."}),
            tool_turn("bash", {"command": "test ! -e /evaluator && python -c \"from pathlib import Path; Path('/scratch/repro.txt').write_text('scratch is writable'); print('isolated sandbox ready')\""}),
            tool_turn("read_file", {"path": "order_service/pricing.py"}),
            tool_turn("edit_file", {"path": "order_service/pricing.py",
                                   "old": "subtotal > BULK_DISCOUNT_THRESHOLD",
                                   "new": "subtotal >= BULK_DISCOUNT_THRESHOLD"}),
            tool_turn("run_tests", {}),
            tool_turn("submit", {"summary": "Inclusive discount threshold, preserving other behavior."})]


def main() -> None:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--runs-root", type=Path, default=Path("runs/tool-loop-gates"))
    args = parser.parse_args()
    task = load_task(Path("benchmarks/cases/order_service_001"))
    sandbox = DockerSandbox()
    run = prepare_run(task, args.runs_root / "fix", model_id="scripted:test", image=sandbox.image)
    checkpoint = str(run / "checkpoint.sqlite")
    with SqliteSaver.from_conn_string(checkpoint) as saver:
        result = AgentRunner(ScriptedModel(responses()), sandbox, saver).start(run)
    if result.status != "awaiting_review":
        raise RuntimeError(f"expected review pause, got {result.status}: {result.error}; run={run}")
    if not (run / "scratch/repro.txt").is_file():
        raise RuntimeError(f"sandbox shell/scratch gate failed; run={run}")
    print(result.review["diff"])
    ArtifactWriter(run).append_trace("gate_approval", {"source": "scripted_validation", "action": "approve"})
    with SqliteSaver.from_conn_string(checkpoint) as saver:
        result = AgentRunner(None, sandbox, saver).resume(run.name, ApprovalDecision(action="approve"))
    if result.status != "exported":
        raise RuntimeError(f"expected approved export, got {result.status}; run={run}")
    eval_run = prepare_run(task, args.runs_root / "eval", mode="eval", model_id="scripted:test", image=sandbox.image)
    with SqliteSaver.from_conn_string(str(eval_run / "checkpoint.sqlite")) as saver:
        eval_result = AgentRunner(ScriptedModel(responses()), sandbox, saver).start(eval_run)
    score = grade_run(task, eval_result, sandbox)
    if not score["success"] or (eval_run / "patch.diff").exists():
        raise RuntimeError(f"evaluation gate failed: {score['status']}; run={eval_run}")
    print("status=verified")
    print(f"fix_run={run}")
    print(f"eval_run={eval_run}")


if __name__ == "__main__":
    main()
