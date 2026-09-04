from pathlib import Path
import unittest

from repomedic.benchmark import SuiteError, load_suite


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SUITE_PATH = PROJECT_ROOT / "benchmarks" / "suites" / "order_service_4.yaml"


class BenchmarkSuiteTests(unittest.TestCase):
    def test_loads_four_distinct_task_categories(self) -> None:
        suite = load_suite(SUITE_PATH)

        self.assertEqual(suite.suite_id, "order_service_4")
        self.assertEqual(len(suite.cases), 4)
        self.assertEqual(
            {case.manifest.category for case in suite.cases},
            {
                "local_logic_bug",
                "cross_module_contract_bug",
                "edge_case_regression_bug",
                "small_feature",
            },
        )

    def test_rejects_duplicate_case_ids(self) -> None:
        with self.assertRaisesRegex(SuiteError, "duplicate"):
            load_suite(SUITE_PATH, case_ids=("order_service_001", "order_service_001"))


if __name__ == "__main__":
    unittest.main()
