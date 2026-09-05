from pathlib import Path
import unittest
import json

from repomedic.benchmark import load_suite
from repomedic.benchmark_run import _pass_at_k, start_benchmark, summarize_benchmark
from repomedic.harness import DeterministicHarness
from repomedic.memory import EpisodicMemoryStore
from repomedic.model_clients import ScriptedModel
from tests.helpers import temporary_directory


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SUITE_PATH = PROJECT_ROOT / "benchmarks" / "suites" / "order_service_4.yaml"


class UnusedSandbox:
    backend = "unused"
    image = "unused:test"

    def run(self, **kwargs):
        raise AssertionError("sandbox should not run after a planner schema failure")


class BenchmarkRunTests(unittest.TestCase):
    def test_pass_at_k_uses_all_independent_attempts(self) -> None:
        rows = [
            {"case_id": "case_001", "status": "tests_failed"},
            {"case_id": "case_001", "status": "verified"},
            {"case_id": "case_001", "status": "tests_failed"},
        ]

        self.assertAlmostEqual(_pass_at_k(rows, ("case_001",), 1), 1 / 3)
        self.assertEqual(_pass_at_k(rows, ("case_001",), 3), 1.0)

    def test_rejects_attempt_count_outside_supported_range(self) -> None:
        suite = load_suite(SUITE_PATH, case_ids=("order_service_001",))
        with temporary_directory() as temp_dir:
            with self.assertRaisesRegex(ValueError, "between 1 and 3"):
                start_benchmark(
                    suite,
                    model=ScriptedModel({}),
                    harness=DeterministicHarness(sandbox=UnusedSandbox()),
                    runs_root=Path(temp_dir),
                    attempts_per_case=4,
                )

    def test_records_every_case_and_aggregates_terminal_statuses(self) -> None:
        suite = load_suite(SUITE_PATH)
        model = ScriptedModel(
            {"planner": [{"acceptance_criteria": []} for _ in suite.cases]}
        )
        with temporary_directory() as temp_dir:
            started = start_benchmark(
                suite,
                model=model,
                harness=DeterministicHarness(sandbox=UnusedSandbox()),
                runs_root=Path(temp_dir),
                run_id="benchmark_run",
            )
            for result in started.case_results:
                usage_path = Path(result.run_dir, "usage.json")
                usage = json.loads(usage_path.read_text(encoding="utf-8"))
                usage.update(model_calls=2, latency_ms=20)
                usage_path.write_text(json.dumps(usage), encoding="utf-8")
            summary = summarize_benchmark(started.run_dir)

            self.assertEqual(len(started.case_results), 4)
            self.assertEqual(summary["status_counts"], {"model_error": 4})
            self.assertTrue(summary["complete"])
            self.assertEqual(summary["verified"], 0)
            self.assertEqual(summary["case_count"], 4)
            self.assertEqual(summary["run_count"], 4)
            self.assertEqual(summary["pass_at_1"], 0.0)
            self.assertIsNone(summary["pass_at_3"])
            self.assertEqual(summary["usage"]["model_calls"], 8)
            self.assertEqual(summary["usage"]["latency_ms"], 80)
            self.assertEqual(summary["usage"]["average_model_latency_ms"], 10)
            for result in started.case_results:
                self.assertTrue(Path(result.run_dir, "checkpoint.sqlite").is_file())

    def test_aggregates_checkpoint_usage_while_awaiting_approval(self) -> None:
        suite = load_suite(SUITE_PATH, case_ids=("order_service_001",))
        model = ScriptedModel(
            {
                "planner": [
                    {
                        "acceptance_criteria": ["The threshold is inclusive."],
                        "investigation_tasks": ["Inspect pricing."],
                        "candidate_paths": ["order_service/pricing.py"],
                        "repair_steps": ["Use an inclusive comparison."],
                    }
                ],
                "investigator_select": [
                    {
                        "searches": ["BULK_DISCOUNT_THRESHOLD"],
                        "reads": ["order_service/pricing.py"],
                    }
                ],
                "investigator": [
                    {
                        "root_cause": "The comparison is strict.",
                        "evidence": [
                            {
                                "path": "order_service/pricing.py",
                                "line_start": 13,
                                "line_end": 13,
                                "excerpt": "subtotal > BULK_DISCOUNT_THRESHOLD",
                            }
                        ],
                        "relevant_files": ["order_service/pricing.py"],
                    }
                ],
                "coder": [
                    {
                        "summary": "Make the threshold inclusive.",
                        "edits": [
                            {
                                "path": "order_service/pricing.py",
                                "old": "subtotal > BULK_DISCOUNT_THRESHOLD",
                                "new": "subtotal >= BULK_DISCOUNT_THRESHOLD",
                                "rationale": "Include exactly 100.00.",
                            }
                        ],
                    }
                ],
            }
        )
        with temporary_directory() as temp_dir:
            memory_store = EpisodicMemoryStore(Path(temp_dir) / "memory.sqlite")
            started = start_benchmark(
                suite,
                model=model,
                harness=DeterministicHarness(sandbox=UnusedSandbox()),
                runs_root=Path(temp_dir),
                run_id="benchmark_run",
                memory_store=memory_store,
                memory_limit=2,
                memory_context_budget_chars=1800,
                agent_mode="multi_agent_no_review",
            )
            summary = summarize_benchmark(started.run_dir)
            benchmark = json.loads(
                (started.run_dir / "benchmark.json").read_text(encoding="utf-8")
            )

            self.assertEqual(summary["status_counts"], {"awaiting_approval": 1})
            self.assertEqual(summary["usage"]["model_calls"], 4)
            self.assertEqual(summary["cases"][0]["usage"]["calls"], 4)
            self.assertEqual(
                benchmark["protocol_version"], "agent-config-ablation-v2"
            )
            self.assertTrue(benchmark["memory"]["enabled"])
            self.assertFalse(benchmark["memory"]["write_enabled"])
            self.assertEqual(benchmark["memory"]["limit"], 2)
            self.assertEqual(benchmark["memory"]["context_budget_chars"], 1800)
            self.assertEqual(benchmark["memory"]["corpus"]["entry_count"], 0)
            self.assertEqual(benchmark["agent_mode"], "multi_agent_no_review")
            self.assertEqual(summary["agent_mode"], "multi_agent_no_review")

    def test_starts_three_attempts_per_case_and_reports_pass_at_three(self) -> None:
        suite = load_suite(SUITE_PATH, case_ids=("order_service_001",))
        model = ScriptedModel(
            {"planner": [{"acceptance_criteria": []} for _ in range(3)]}
        )
        with temporary_directory() as temp_dir:
            started = start_benchmark(
                suite,
                model=model,
                harness=DeterministicHarness(sandbox=UnusedSandbox()),
                runs_root=Path(temp_dir),
                run_id="three_attempts",
                attempts_per_case=3,
            )
            summary = summarize_benchmark(started.run_dir)
            benchmark = json.loads(
                (started.run_dir / "benchmark.json").read_text(encoding="utf-8")
            )

            self.assertEqual(len(started.case_results), 3)
            self.assertEqual(benchmark["attempts_per_case"], 3)
            self.assertEqual(
                [row["attempt"] for row in benchmark["case_runs"]], [1, 2, 3]
            )
            self.assertEqual(summary["case_count"], 1)
            self.assertEqual(summary["run_count"], 3)
            self.assertEqual(summary["attempts_per_case"], 3)
            self.assertEqual(summary["pass_at_1"], 0.0)
            self.assertEqual(summary["pass_at_3"], 0.0)

    def test_resume_skips_recorded_attempts_without_new_model_calls(self) -> None:
        suite = load_suite(SUITE_PATH, case_ids=("order_service_001",))
        with temporary_directory() as temp_dir:
            runs_root = Path(temp_dir)
            started = start_benchmark(
                suite,
                model=ScriptedModel(
                    {"planner": [{"acceptance_criteria": []} for _ in range(3)]}
                ),
                harness=DeterministicHarness(sandbox=UnusedSandbox()),
                runs_root=runs_root,
                run_id="resumable",
                attempts_per_case=3,
            )

            resumed = start_benchmark(
                suite,
                model=ScriptedModel({}),
                harness=DeterministicHarness(sandbox=UnusedSandbox()),
                runs_root=runs_root,
                run_id="resumable",
                attempts_per_case=3,
                resume_existing=True,
            )

            self.assertEqual(resumed.case_results, ())
            record = json.loads(
                (started.run_dir / "benchmark.json").read_text(encoding="utf-8")
            )
            self.assertEqual(len(record["case_runs"]), 3)

    def test_resume_recovers_unrecorded_checkpoint_without_rerunning(self) -> None:
        suite = load_suite(SUITE_PATH, case_ids=("order_service_001",))
        with temporary_directory() as temp_dir:
            runs_root = Path(temp_dir)
            started = start_benchmark(
                suite,
                model=ScriptedModel(
                    {"planner": [{"acceptance_criteria": []}]}
                ),
                harness=DeterministicHarness(sandbox=UnusedSandbox()),
                runs_root=runs_root,
                run_id="orphaned",
            )
            record_path = started.run_dir / "benchmark.json"
            record = json.loads(record_path.read_text(encoding="utf-8"))
            record["case_runs"] = []
            record_path.write_text(json.dumps(record), encoding="utf-8")

            resumed = start_benchmark(
                suite,
                model=ScriptedModel({}),
                harness=DeterministicHarness(sandbox=UnusedSandbox()),
                runs_root=runs_root,
                run_id="orphaned",
                resume_existing=True,
            )

            self.assertEqual(len(resumed.case_results), 1)
            self.assertEqual(resumed.case_results[0].status, "model_error")
            recovered = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertEqual(len(recovered["case_runs"]), 1)


if __name__ == "__main__":
    unittest.main()
