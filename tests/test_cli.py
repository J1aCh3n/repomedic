from pathlib import Path
import json
import unittest

from repomedic.__main__ import _configured_agent, _parser
from repomedic.prompts import PROMPT_VERSION
from tests.helpers import temporary_directory


class CliTests(unittest.TestCase):
    def test_resume_preserves_read_only_memory_setting(self) -> None:
        with temporary_directory() as temp_dir:
            run_dir = Path(temp_dir)
            (run_dir / "config.json").write_text(
                json.dumps(
                    {
                        "agent_graph": {
                            "model": "scripted",
                            "reasoning_effort": None,
                            "prompt_version": PROMPT_VERSION,
                            "mode": "multi_agent_review",
                        },
                        "memory": {
                            "enabled": True,
                            "database": "memory.sqlite",
                            "limit": 3,
                            "context_budget_chars": 2400,
                            "write_enabled": False,
                        },
                    }
                ),
                encoding="utf-8",
            )

            configured = _configured_agent(run_dir)

            self.assertFalse(configured[5])

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
                "--attempts",
                "3",
                "--resume",
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
        self.assertEqual(args.attempts, 3)
        self.assertTrue(args.resume)

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

        preflight = _parser().parse_args(
            [
                "compare-preflight",
                "runs/single",
                "runs/no-review",
                "runs/review",
                "runs/memory",
                "--output-dir",
                "runs/preflight-comparison",
            ]
        )
        self.assertEqual(preflight.memory_run, Path("runs/memory"))
        self.assertEqual(preflight.output_dir, Path("runs/preflight-comparison"))


if __name__ == "__main__":
    unittest.main()
