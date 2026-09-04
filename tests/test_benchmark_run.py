from pathlib import Path
import unittest

from repomedic.benchmark import load_suite
from repomedic.benchmark_run import start_benchmark, summarize_benchmark
from repomedic.harness import DeterministicHarness
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
            summary = summarize_benchmark(started.run_dir)

            self.assertEqual(len(started.case_results), 4)
            self.assertEqual(summary["status_counts"], {"model_error": 4})
            self.assertTrue(summary["complete"])
            self.assertEqual(summary["verified"], 0)
            for result in started.case_results:
                self.assertTrue(Path(result.run_dir, "checkpoint.sqlite").is_file())


if __name__ == "__main__":
    unittest.main()
