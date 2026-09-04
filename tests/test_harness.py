from pathlib import Path
import json
import unittest

from repomedic.harness import DeterministicHarness, RunStateError
from repomedic.models import TestResult
from tests.helpers import temporary_directory


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CASE_ROOT = PROJECT_ROOT / "benchmarks" / "cases" / "order_service_001"


class ScriptedSandbox:
    backend = "scripted"
    image = "scripted:test"

    def __init__(self, modify_forbidden: bool = False, success: bool = False) -> None:
        self.modify_forbidden = modify_forbidden
        self.success = success
        self.calls: list[str] = []

    def run(self, *, kind: str, workspace: Path, **kwargs) -> TestResult:
        self.calls.append(kind)
        self.assert_isolated(workspace)
        if self.modify_forbidden and kind == "public":
            (workspace / "tests" / "test_order_service.py").write_text(
                "# tampered\n", encoding="utf-8"
            )
        return TestResult(
            kind=kind,
            argv=("python", "-m", "unittest"),
            exit_code=0 if self.success or kind == "evaluator" else 1,
            timed_out=False,
            duration_ms=5,
            stdout="one expected failure" if kind == "public" else "ok",
            stderr="",
            infrastructure_error=False,
        )

    def assert_isolated(self, workspace: Path) -> None:
        if (workspace / "evaluator").exists():
            raise AssertionError("evaluator data leaked into Agent workspace")
        if not (workspace / "order_service" / "pricing.py").is_file():
            raise AssertionError("fixture was not copied")


class HarnessTests(unittest.TestCase):
    def test_run_resets_workspace_and_captures_artifacts(self) -> None:
        sandbox = ScriptedSandbox()
        with temporary_directory() as temp_dir:
            outcome = DeterministicHarness(sandbox=sandbox).run_case(
                CASE_ROOT,
                Path(temp_dir),
                run_id="run_001",
            )
            run_dir = Path(temp_dir) / "order_service_001" / "run_001"
            config = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))
            results = json.loads(
                (run_dir / "test-results.json").read_text(encoding="utf-8")
            )

            self.assertEqual(outcome.status, "tests_failed")
            self.assertEqual(sandbox.calls, ["public", "evaluator"])
            self.assertEqual(config["case_id"], "order_service_001")
            self.assertEqual(config["sandbox"]["backend"], "scripted")
            self.assertEqual(len(results["results"]), 2)
            self.assertTrue((run_dir / "policy.json").is_file())
            self.assertTrue((run_dir / "patch.diff").is_file())
            self.assertTrue((run_dir / "trace.jsonl").is_file())
            self.assertTrue((run_dir / "final-report.md").is_file())
            self.assertFalse((run_dir / "workspace" / "evaluator").exists())

    def test_policy_violation_overrides_test_status(self) -> None:
        with temporary_directory() as temp_dir:
            outcome = DeterministicHarness(
                sandbox=ScriptedSandbox(modify_forbidden=True)
            ).run_case(CASE_ROOT, Path(temp_dir), run_id="run_002")

        self.assertEqual(outcome.status, "policy_violation")
        self.assertFalse(outcome.policy.compliant)

    def test_prepare_then_evaluate_captures_an_allowed_repair(self) -> None:
        sandbox = ScriptedSandbox(success=True)
        with temporary_directory() as temp_dir:
            harness = DeterministicHarness(sandbox=sandbox)
            prepared = harness.prepare_case(
                CASE_ROOT,
                Path(temp_dir),
                run_id="run_003",
            )
            pricing = prepared.layout.workspace / "order_service" / "pricing.py"
            pricing.write_text(
                pricing.read_text(encoding="utf-8").replace(
                    "subtotal > BULK_DISCOUNT_THRESHOLD",
                    "subtotal >= BULK_DISCOUNT_THRESHOLD",
                ),
                encoding="utf-8",
            )

            outcome = harness.evaluate(prepared)
            patch = (prepared.layout.run_dir / "patch.diff").read_text(encoding="utf-8")

        self.assertEqual(outcome.status, "verified")
        self.assertTrue(outcome.policy.compliant)
        self.assertIn("+    if subtotal >= BULK_DISCOUNT_THRESHOLD:", patch)

    def test_pretest_policy_violation_prevents_code_execution(self) -> None:
        sandbox = ScriptedSandbox(success=True)
        with temporary_directory() as temp_dir:
            harness = DeterministicHarness(sandbox=sandbox)
            prepared = harness.prepare_case(
                CASE_ROOT,
                Path(temp_dir),
                run_id="run_004",
            )
            (prepared.layout.workspace / ".env").write_text(
                "API_KEY=do-not-run", encoding="utf-8"
            )

            outcome = harness.evaluate(prepared)

        self.assertEqual(outcome.status, "policy_violation")
        self.assertEqual(sandbox.calls, [])

    def test_evaluate_rejects_duplicate_execution(self) -> None:
        with temporary_directory() as temp_dir:
            harness = DeterministicHarness(sandbox=ScriptedSandbox(success=True))
            prepared = harness.prepare_case(
                CASE_ROOT,
                Path(temp_dir),
                run_id="run_005",
            )
            harness.evaluate(prepared)

            with self.assertRaisesRegex(RunStateError, "already been evaluated"):
                harness.evaluate(prepared)


if __name__ == "__main__":
    unittest.main()
