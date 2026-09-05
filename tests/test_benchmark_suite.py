from collections import Counter
from pathlib import Path
import unittest

from repomedic.benchmark import SuiteError, load_suite


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SUITE_PATH = PROJECT_ROOT / "benchmarks" / "suites" / "order_service_4.yaml"
EIGHT_CASE_SUITE_PATH = PROJECT_ROOT / "benchmarks" / "suites" / "initial_8.yaml"
TWELVE_CASE_SUITE_PATH = PROJECT_ROOT / "benchmarks" / "suites" / "initial_12.yaml"
PREFLIGHT_SUITE_PATH = PROJECT_ROOT / "benchmarks" / "suites" / "preflight_6.yaml"

EXPECTED_CATEGORIES = {
    "local_logic_bug",
    "cross_module_contract_bug",
    "edge_case_regression_bug",
    "small_feature",
}


class BenchmarkSuiteTests(unittest.TestCase):
    def test_loads_four_distinct_task_categories(self) -> None:
        suite = load_suite(SUITE_PATH)

        self.assertEqual(suite.suite_id, "order_service_4")
        self.assertEqual(len(suite.cases), 4)
        self.assertEqual(
            {case.manifest.category for case in suite.cases},
            EXPECTED_CATEGORIES,
        )

    def test_eight_case_suite_has_four_categories_per_fixture(self) -> None:
        suite = load_suite(EIGHT_CASE_SUITE_PATH)

        self.assertEqual(suite.suite_id, "initial_8")
        self.assertEqual(len(suite.cases), 8)

        categories_by_fixture: dict[str, set[str]] = {}
        for case in suite.cases:
            fixture_id = case.manifest.fixture.fixture_id
            categories_by_fixture.setdefault(fixture_id, set()).add(
                case.manifest.category
            )

        self.assertEqual(
            set(categories_by_fixture),
            {"order_service", "task_scheduler"},
        )
        for categories in categories_by_fixture.values():
            self.assertEqual(categories, EXPECTED_CATEGORIES)

    def test_twelve_case_suite_has_four_categories_per_fixture(self) -> None:
        suite = load_suite(TWELVE_CASE_SUITE_PATH)

        self.assertEqual(suite.suite_id, "initial_12")
        self.assertEqual(len(suite.cases), 12)

        categories_by_fixture: dict[str, set[str]] = {}
        for case in suite.cases:
            fixture_id = case.manifest.fixture.fixture_id
            categories_by_fixture.setdefault(fixture_id, set()).add(
                case.manifest.category
            )

        self.assertEqual(
            set(categories_by_fixture),
            {"order_service", "task_scheduler", "document_pipeline"},
        )
        for categories in categories_by_fixture.values():
            self.assertEqual(categories, EXPECTED_CATEGORIES)

    def test_preflight_suite_is_the_frozen_stratified_six_case_subset(self) -> None:
        suite = load_suite(PREFLIGHT_SUITE_PATH)

        self.assertEqual(suite.suite_id, "preflight_6")
        self.assertEqual(
            tuple(case.case_id for case in suite.cases),
            (
                "order_service_001",
                "order_service_004",
                "task_scheduler_006",
                "task_scheduler_008",
                "document_pipeline_010",
                "document_pipeline_011",
            ),
        )
        self.assertEqual(
            Counter(case.manifest.fixture.fixture_id for case in suite.cases),
            {
                "order_service": 2,
                "task_scheduler": 2,
                "document_pipeline": 2,
            },
        )
        self.assertEqual(
            {case.manifest.category for case in suite.cases},
            EXPECTED_CATEGORIES,
        )

    def test_rejects_duplicate_case_ids(self) -> None:
        with self.assertRaisesRegex(SuiteError, "duplicate"):
            load_suite(SUITE_PATH, case_ids=("order_service_001", "order_service_001"))


if __name__ == "__main__":
    unittest.main()
