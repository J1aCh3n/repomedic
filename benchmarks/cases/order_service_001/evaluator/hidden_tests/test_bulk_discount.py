from decimal import Decimal
import os
from pathlib import Path
import sys
import unittest


DEFAULT_REPO = Path(__file__).resolve().parents[2] / "repo"
REPO_UNDER_TEST = Path(
    os.environ.get("REPOMEDIC_REPO_UNDER_TEST", DEFAULT_REPO)
).resolve()
sys.path.insert(0, str(REPO_UNDER_TEST))

from order_service.pricing import calculate_total  # noqa: E402


class BulkDiscountEvaluatorTests(unittest.TestCase):
    def test_threshold_with_a_different_unit_price(self) -> None:
        self.assertEqual(
            calculate_total(Decimal("12.50"), quantity=8),
            Decimal("90.00"),
        )

    def test_total_above_threshold_remains_discounted(self) -> None:
        self.assertEqual(
            calculate_total(Decimal("25.00"), quantity=5),
            Decimal("112.50"),
        )

    def test_total_just_below_threshold_remains_unchanged(self) -> None:
        self.assertEqual(
            calculate_total(Decimal("19.99"), quantity=5),
            Decimal("99.95"),
        )


if __name__ == "__main__":
    unittest.main()
