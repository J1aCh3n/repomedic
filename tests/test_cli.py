from pathlib import Path
import unittest

from repomedic.__main__ import _parser


class CliTests(unittest.TestCase):
    def test_start_benchmark_accepts_repeated_case_filters(self) -> None:
        args = _parser().parse_args(
            [
                "start-benchmark",
                "benchmarks/suites/initial_8.yaml",
                "--model",
                "gpt-5.6-terra",
                "--case",
                "task_scheduler_005",
                "--case",
                "task_scheduler_008",
                "--memory-db",
                "runs/memory.sqlite",
                "--memory-limit",
                "2",
                "--memory-context-budget-chars",
                "1800",
                "--agent-mode",
                "multi_agent_no_review",
            ]
        )

        self.assertEqual(args.suite, Path("benchmarks/suites/initial_8.yaml"))
        self.assertEqual(
            args.case_ids, ["task_scheduler_005", "task_scheduler_008"]
        )
        self.assertEqual(args.memory_db, Path("runs/memory.sqlite"))
        self.assertEqual(args.memory_limit, 2)
        self.assertEqual(args.memory_context_budget_chars, 1800)
        self.assertEqual(args.agent_mode, "multi_agent_no_review")

    def test_memory_commands_parse_explicit_database(self) -> None:
        learn = _parser().parse_args(
            [
                "memory-learn",
                "runs/order_service_001/attempt_1",
                "--memory-db",
                "runs/memory.sqlite",
            ]
        )
        search = _parser().parse_args(
            [
                "memory-search",
                "threshold discount",
                "--memory-db",
                "runs/memory.sqlite",
                "--fixture",
                "order_service",
                "--exclude-case",
                "order_service_001",
                "--limit",
                "2",
                "--context-budget-chars",
                "1800",
            ]
        )

        self.assertEqual(learn.run_dir, Path("runs/order_service_001/attempt_1"))
        self.assertEqual(search.query, "threshold discount")
        self.assertEqual(search.fixture, "order_service")
        self.assertEqual(search.exclude_case, "order_service_001")
        self.assertEqual(search.limit, 2)
        self.assertEqual(search.context_budget_chars, 1800)

        compare = _parser().parse_args(
            [
                "compare-memory",
                "runs/baseline",
                "runs/treatment",
                "--output-dir",
                "runs/comparison",
            ]
        )
        self.assertEqual(compare.baseline_run, Path("runs/baseline"))
        self.assertEqual(compare.memory_run, Path("runs/treatment"))
        self.assertEqual(compare.output_dir, Path("runs/comparison"))

        configurations = _parser().parse_args(
            [
                "compare-configurations",
                "runs/single",
                "runs/no-review",
                "runs/review",
                "--output-dir",
                "runs/configuration-comparison",
            ]
        )
        self.assertEqual(configurations.single_agent_run, Path("runs/single"))
        self.assertEqual(configurations.no_review_run, Path("runs/no-review"))
        self.assertEqual(configurations.review_run, Path("runs/review"))


if __name__ == "__main__":
    unittest.main()
