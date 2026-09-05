from collections import deque
from dataclasses import replace
from pathlib import Path
import json
import unittest

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver

from repomedic.agent_graph import AgentGraphRunner
from repomedic.agent_schemas import ApprovalDecision
from repomedic.harness import DeterministicHarness
from repomedic.memory import EpisodicMemoryStore, MemorySnapshot
from repomedic.model_clients import ScriptedModel
from repomedic.models import TestResult
from tests.helpers import temporary_directory


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CASE_ROOT = PROJECT_ROOT / "benchmarks" / "cases" / "order_service_001"


PLAN = {
    "acceptance_criteria": ["The threshold receives the discount."],
    "investigation_tasks": ["Inspect pricing threshold comparison."],
    "candidate_paths": ["order_service/pricing.py"],
    "repair_steps": ["Make the comparison inclusive."],
}
SELECT = {
    "searches": ["subtotal > BULK_DISCOUNT_THRESHOLD"],
    "reads": ["order_service/pricing.py"],
}
INVESTIGATION = {
    "root_cause": "The strict comparison excludes exactly 100.00.",
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
PROPOSAL = {
    "summary": "Include the exact threshold.",
    "edits": [
        {
            "path": "order_service/pricing.py",
            "old": "subtotal > BULK_DISCOUNT_THRESHOLD",
            "new": "subtotal >= BULK_DISCOUNT_THRESHOLD",
            "rationale": "The issue requires an inclusive threshold.",
        }
    ],
}
SECOND_PROPOSAL = {
    "summary": "Clarify the repaired behavior.",
    "edits": [
        {
            "path": "order_service/pricing.py",
            "old": "Return the order total after the bulk discount",
            "new": "Return the order total after any bulk discount",
            "rationale": "Keep the behavior explicit.",
        }
    ],
}


def review(verdict: str) -> dict[str, object]:
    return {
        "verdict": verdict,
        "reasons": [f"Reviewer selected {verdict}."],
        "feedback": "Try another minimal edit." if verdict != "pass" else "",
        "requested_paths": (
            ["order_service/pricing.py"]
            if verdict in {"revise", "replan"}
            else []
        ),
    }


def script(*, reviews: list[str], proposals: list[dict] | None = None) -> dict:
    return {
        "planner": [PLAN, PLAN],
        "investigator_select": [SELECT, SELECT],
        "investigator": [INVESTIGATION, INVESTIGATION],
        "coder": proposals or [PROPOSAL, SECOND_PROPOSAL],
        "reviewer": [review(item) for item in reviews],
    }


class SequenceSandbox:
    backend = "scripted"
    image = "scripted:graph"

    def __init__(self, public_results: list[bool]) -> None:
        self.public_results = deque(public_results)
        self.calls: list[str] = []

    def run(self, *, kind: str, **kwargs) -> TestResult:
        self.calls.append(kind)
        success = True if kind == "evaluator" else self.public_results.popleft()
        return TestResult(
            kind=kind,
            argv=("python", "-m", "unittest"),
            exit_code=0 if success else 1,
            timed_out=False,
            duration_ms=1,
            stdout="ok" if success else "failed",
            stderr="",
            infrastructure_error=False,
        )


class CapturingScriptedModel(ScriptedModel):
    def __init__(self, responses: dict) -> None:
        super().__init__(responses)
        self.inputs: list[tuple[str, dict]] = []

    def generate(self, **kwargs):
        self.inputs.append((kwargs["agent"], kwargs["input_data"]))
        return super().generate(**kwargs)


class StubMemoryEntry:
    entry_id = "prior-entry"
    case_id = "order_service_prior"
    run_id = "prior-run"


class StubMemoryMatch:
    entry = StubMemoryEntry()
    score = 7

    def prompt_value(self) -> dict:
        return {
            "entry_id": self.entry.entry_id,
            "score": self.score,
            "provenance": {
                "fixture_id": "order_service",
                "fixture_version": "order-service-v1",
                "case_id": self.entry.case_id,
                "run_id": self.entry.run_id,
            },
            "lesson": {
                "issue": "A prior threshold bug.",
                "root_cause": "A strict comparison excluded the boundary.",
                "repair_summary": "Use an inclusive comparison.",
                "changed_paths": ["order_service/pricing.py"],
                "evidence_paths": ["order_service/pricing.py"],
            },
        }


class StubMemoryStore:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self.searches: list[dict] = []
        self.writes: list[Path] = []

    def search(self, issue: str, **kwargs) -> tuple[StubMemoryMatch, ...]:
        self.searches.append({"issue": issue, **kwargs})
        return (StubMemoryMatch(),)

    def snapshot(self):
        return MemorySnapshot(entry_count=1, content_hash="snapshot-hash")

    def record_verified_run(self, run_dir: Path) -> StubMemoryEntry:
        self.writes.append(run_dir.resolve())
        return StubMemoryEntry()


class AgentGraphTests(unittest.TestCase):
    def _start(self, temp_dir: str, model: ScriptedModel, sandbox: SequenceSandbox):
        harness = DeterministicHarness(sandbox=sandbox)
        prepared = harness.prepare_case(CASE_ROOT, Path(temp_dir), run_id="graph_run")
        runner = AgentGraphRunner(model, harness, InMemorySaver())
        return runner, runner.start(prepared)

    def test_direct_pass_pauses_then_finishes_with_evidence(self) -> None:
        with temporary_directory() as temp_dir:
            runner, paused = self._start(
                temp_dir,
                ScriptedModel(script(reviews=["pass"])),
                SequenceSandbox([True, True]),
            )
            self.assertTrue(paused.awaiting_approval)
            self.assertIn("subtotal >=", paused.proposal_diff or "")
            self.assertEqual((paused.usage or {})["model_calls"], 4)

            result = runner.resume(
                "graph_run", ApprovalDecision(action="approve", feedback="")
            )
            run_dir = Path(result.run_dir)

            self.assertEqual(result.status, "verified")
            self.assertFalse(result.awaiting_approval)
            config = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))
            self.assertFalse(config["memory"]["enabled"])
            for name in (
                "plan.json",
                "investigation.json",
                "review.json",
                "usage.json",
                "patch.diff",
                "final-report.md",
            ):
                self.assertTrue((run_dir / name).is_file(), name)

    def test_memory_retrieval_reaches_planner_and_verified_run_is_recorded(self) -> None:
        with temporary_directory() as temp_dir:
            sandbox = SequenceSandbox([True, True])
            harness = DeterministicHarness(sandbox=sandbox)
            prepared = harness.prepare_case(
                CASE_ROOT, Path(temp_dir), run_id="memory_run"
            )
            model = CapturingScriptedModel(script(reviews=["pass"]))
            store = StubMemoryStore(Path(temp_dir) / "memory.sqlite")
            runner = AgentGraphRunner(
                model,
                harness,
                InMemorySaver(),
                memory_store=store,
                memory_limit=2,
                memory_context_budget_chars=1800,
            )

            paused = runner.start(prepared)

            planner_input = next(
                input_data
                for agent, input_data in model.inputs
                if agent == "planner"
            )
            self.assertEqual(
                planner_input["memory_lessons"], [StubMemoryMatch().prompt_value()]
            )
            self.assertEqual(store.searches[0]["exclude_case_id"], "order_service_001")
            self.assertEqual(store.searches[0]["limit"], 2)
            config = json.loads(
                (Path(paused.run_dir) / "config.json").read_text(encoding="utf-8")
            )
            self.assertEqual(config["memory"]["retrieved_entry_ids"], ["prior-entry"])
            self.assertEqual(config["memory"]["context_budget_chars"], 1800)
            self.assertGreater(config["memory"]["context_chars"], 0)
            self.assertEqual(config["memory"]["corpus"]["entry_count"], 1)
            self.assertEqual(
                config["memory"]["corpus"]["content_hash"], "snapshot-hash"
            )
            self.assertIsNone((paused.memory or {})["write"])

            result = runner.resume(
                "memory_run", ApprovalDecision(action="approve", feedback="")
            )

            self.assertEqual(result.status, "verified")
            self.assertEqual(store.writes, [Path(result.run_dir).resolve()])
            self.assertEqual((result.memory or {})["write"]["entry_id"], "prior-entry")
            artifact = json.loads(
                (Path(result.run_dir) / "memory.json").read_text(encoding="utf-8")
            )
            self.assertEqual(artifact["write"]["status"], "stored")

    def test_verified_graph_run_writes_real_memory_entry(self) -> None:
        with temporary_directory() as temp_dir:
            sandbox = SequenceSandbox([True, True])
            harness = DeterministicHarness(sandbox=sandbox)
            prepared = harness.prepare_case(
                CASE_ROOT, Path(temp_dir), run_id="real_memory_run"
            )
            store = EpisodicMemoryStore(Path(temp_dir) / "memory.sqlite")
            runner = AgentGraphRunner(
                ScriptedModel(script(reviews=["pass"])),
                harness,
                InMemorySaver(),
                memory_store=store,
            )

            runner.start(prepared)
            result = runner.resume(
                "real_memory_run", ApprovalDecision(action="approve", feedback="")
            )

            self.assertEqual(result.status, "verified")
            self.assertEqual(store.count(), 1)
            match = store.search(
                "bulk threshold discount",
                fixture_id="order_service",
                exclude_case_id="another_case",
                limit=1,
            )[0]
            self.assertEqual(match.entry.case_id, "order_service_001")
            self.assertEqual(match.entry.run_id, "real_memory_run")

    def test_revision_and_replan_routes_return_to_expected_agent(self) -> None:
        for first_verdict, expected_agent in (("revise", "coder"), ("replan", "planner")):
            with self.subTest(first_verdict=first_verdict):
                with temporary_directory() as temp_dir:
                    model = ScriptedModel(script(reviews=[first_verdict, "pass"]))
                    runner, paused = self._start(
                        temp_dir, model, SequenceSandbox([False, True, True])
                    )
                    self.assertTrue(paused.awaiting_approval)
                    paused_again = runner.resume(
                        "graph_run", ApprovalDecision(action="approve", feedback="")
                    )
                    self.assertTrue(paused_again.awaiting_approval)
                    self.assertEqual(model.calls.count(expected_agent), 2)
                    result = runner.resume(
                        "graph_run", ApprovalDecision(action="approve", feedback="")
                    )
                    self.assertEqual(result.status, "verified")

    def test_reviewer_cannot_request_forbidden_edit_paths(self) -> None:
        with temporary_directory() as temp_dir:
            responses = script(reviews=[])
            responses["reviewer"] = [
                {
                    "verdict": "revise",
                    "reasons": ["Add another public test."],
                    "feedback": "Edit the tests.",
                    "requested_paths": ["tests/test_order_service.py"],
                }
            ]
            model = ScriptedModel(responses)
            runner, paused = self._start(
                temp_dir, model, SequenceSandbox([True])
            )

            result = runner.resume(
                "graph_run", ApprovalDecision(action="approve", feedback="")
            )

            self.assertEqual(result.status, "review_error")
            self.assertIn("outside the edit policy", result.error or "")
            self.assertEqual(model.calls.count("coder"), 1)

    def test_human_revision_returns_to_coder_without_applying_proposal(self) -> None:
        with temporary_directory() as temp_dir:
            model = ScriptedModel(script(reviews=[], proposals=[PROPOSAL, PROPOSAL]))
            runner, paused = self._start(temp_dir, model, SequenceSandbox([]))
            pricing = Path(paused.run_dir) / "workspace" / "order_service" / "pricing.py"

            paused_again = runner.resume(
                "graph_run",
                ApprovalDecision(action="revise", feedback="Please reconsider."),
            )

            self.assertTrue(paused_again.awaiting_approval)
            self.assertEqual(model.calls.count("coder"), 2)
            self.assertIn(
                "subtotal > BULK_DISCOUNT_THRESHOLD",
                pricing.read_text(encoding="utf-8"),
            )

    def test_rejection_stops_without_modifying_workspace(self) -> None:
        with temporary_directory() as temp_dir:
            runner, paused = self._start(
                temp_dir,
                ScriptedModel(script(reviews=[])),
                SequenceSandbox([]),
            )
            pricing = Path(paused.run_dir) / "workspace" / "order_service" / "pricing.py"
            before = pricing.read_text(encoding="utf-8")

            result = runner.resume(
                "graph_run", ApprovalDecision(action="reject", feedback="unsafe")
            )

            self.assertEqual(result.status, "rejected")
            self.assertEqual(pricing.read_text(encoding="utf-8"), before)

    def test_malformed_output_and_tool_error_are_explicit(self) -> None:
        cases = (
            (
                ScriptedModel({"planner": [{"acceptance_criteria": []}]}),
                "model_error",
            ),
            (
                ScriptedModel(
                    {
                        "planner": [PLAN],
                        "investigator_select": [
                            {"searches": [], "reads": ["../evaluator/secret.py"]}
                        ],
                    }
                ),
                "tool_error",
            ),
        )
        for model, expected in cases:
            with self.subTest(expected=expected):
                with temporary_directory() as temp_dir:
                    _, result = self._start(temp_dir, model, SequenceSandbox([]))
                    self.assertEqual(result.status, expected)
                    self.assertFalse(result.awaiting_approval)
                    if expected == "model_error":
                        self.assertEqual((result.usage or {})["model_calls"], 1)
                        self.assertEqual((result.usage or {})["tool_calls"], 1)

    def test_tool_failure_preserves_completed_model_and_tool_calls(self) -> None:
        with temporary_directory() as temp_dir:
            sandbox = SequenceSandbox([True])
            harness = DeterministicHarness(sandbox=sandbox)
            prepared = harness.prepare_case(
                CASE_ROOT, Path(temp_dir), run_id="budget_run"
            )
            prepared = replace(
                prepared,
                manifest=replace(
                    prepared.manifest,
                    limits=replace(prepared.manifest.limits, tool_calls=7),
                ),
            )
            model = ScriptedModel(script(reviews=["revise"]))
            runner = AgentGraphRunner(model, harness, InMemorySaver())
            paused = runner.start(prepared)

            result = runner.resume(
                "budget_run", ApprovalDecision(action="approve", feedback="")
            )

            self.assertTrue(paused.awaiting_approval)
            self.assertEqual(result.status, "tool_error")
            self.assertEqual((result.usage or {})["model_calls"], 6)
            self.assertEqual((result.usage or {})["tool_calls"], 7)

    def test_iteration_limit_stops_repeated_revision(self) -> None:
        with temporary_directory() as temp_dir:
            runner, _ = self._start(
                temp_dir,
                ScriptedModel(script(reviews=["revise", "revise"])),
                SequenceSandbox([False, False]),
            )
            second_pause = runner.resume(
                "graph_run", ApprovalDecision(action="approve", feedback="")
            )
            self.assertTrue(second_pause.awaiting_approval)
            result = runner.resume(
                "graph_run", ApprovalDecision(action="approve", feedback="")
            )
            self.assertEqual(result.status, "iteration_exhausted")

    def test_sqlite_checkpoint_resumes_with_a_new_runner(self) -> None:
        with temporary_directory() as temp_dir:
            sandbox = SequenceSandbox([True, True])
            harness = DeterministicHarness(sandbox=sandbox)
            prepared = harness.prepare_case(
                CASE_ROOT, Path(temp_dir), run_id="sqlite_run"
            )
            database = prepared.layout.run_dir / "checkpoint.sqlite"
            with SqliteSaver.from_conn_string(str(database)) as saver:
                first = AgentGraphRunner(
                    ScriptedModel(script(reviews=[])), harness, saver
                ).start(prepared)
                self.assertTrue(first.awaiting_approval)

            with SqliteSaver.from_conn_string(str(database)) as saver:
                resumed = AgentGraphRunner(
                    ScriptedModel({"reviewer": [review("pass")]}), harness, saver
                ).resume(
                    "sqlite_run", ApprovalDecision(action="approve", feedback="")
                )

            self.assertEqual(resumed.status, "verified")


if __name__ == "__main__":
    unittest.main()
