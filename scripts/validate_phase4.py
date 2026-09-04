from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from repomedic.agent_graph import AgentGraphRunner
from repomedic.agent_schemas import ApprovalDecision
from repomedic.harness import DeterministicHarness
from repomedic.model_clients import ScriptedModel


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CASE_ROOT = PROJECT_ROOT / "benchmarks" / "cases" / "order_service_001"
RUNS_ROOT = PROJECT_ROOT / "runs"


def _model() -> ScriptedModel:
    return ScriptedModel(
        {
            "planner": [
                {
                    "acceptance_criteria": [
                        "Exactly 100.00 receives the existing discount.",
                        "Behavior above and below the threshold is preserved.",
                    ],
                    "investigation_tasks": ["Inspect the pricing comparison."],
                    "candidate_paths": ["order_service/pricing.py"],
                    "repair_steps": ["Make the threshold comparison inclusive."],
                }
            ],
            "investigator_select": [
                {
                    "searches": ["subtotal > BULK_DISCOUNT_THRESHOLD"],
                    "reads": ["order_service/pricing.py"],
                }
            ],
            "investigator": [
                {
                    "root_cause": "A strict comparison excludes exactly 100.00.",
                    "evidence": [
                        {
                            "path": "order_service/pricing.py",
                            "line_start": 13,
                            "line_end": 13,
                            "excerpt": "if subtotal > BULK_DISCOUNT_THRESHOLD:",
                        }
                    ],
                    "relevant_files": ["order_service/pricing.py"],
                }
            ],
            "coder": [
                {
                    "summary": "Apply the discount at the exact threshold.",
                    "edits": [
                        {
                            "path": "order_service/pricing.py",
                            "old": "subtotal > BULK_DISCOUNT_THRESHOLD",
                            "new": "subtotal >= BULK_DISCOUNT_THRESHOLD",
                            "rationale": "The required threshold is inclusive.",
                        }
                    ],
                }
            ],
            "reviewer": [
                {
                    "verdict": "pass",
                    "reasons": ["The public tests pass and the diff is minimal."],
                    "feedback": "",
                }
            ],
        }
    )


def main() -> None:
    harness = DeterministicHarness()
    prepared = harness.prepare_case(CASE_ROOT, RUNS_ROOT)
    database = prepared.layout.run_dir / "checkpoint.sqlite"
    with SqliteSaver.from_conn_string(str(database)) as saver:
        runner = AgentGraphRunner(_model(), harness, saver)
        paused = runner.start(prepared)
        if not paused.awaiting_approval:
            raise RuntimeError(f"expected approval pause, received {paused.status}")
        outcome = runner.resume(
            prepared.layout.run_id,
            ApprovalDecision(action="approve", feedback="phase 4 gate approval"),
        )
    if outcome.status != "verified":
        raise RuntimeError(f"phase 4 gate failed: {outcome.status}")
    print(f"status={outcome.status}")
    print(f"run_dir={outcome.run_dir}")


if __name__ == "__main__":
    main()
