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
            ]
        )

        self.assertEqual(args.suite, Path("benchmarks/suites/initial_8.yaml"))
        self.assertEqual(
            args.case_ids, ["task_scheduler_005", "task_scheduler_008"]
        )


if __name__ == "__main__":
    unittest.main()
