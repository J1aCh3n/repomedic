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

from order_service.inventory import Inventory, OutOfStockError  # noqa: E402
from order_service.service import OrderService  # noqa: E402
from order_service.storage import InMemoryOrderStore  # noqa: E402


class InventoryAtomicityEvaluatorTests(unittest.TestCase):
    def test_repeated_failures_leave_stock_unchanged(self) -> None:
        inventory = Inventory({"MUG": 2})

        for _ in range(2):
            with self.assertRaises(OutOfStockError):
                inventory.reserve("MUG", quantity=3)

        self.assertEqual(inventory.available("MUG"), 2)

    def test_failed_order_does_not_persist_or_consume_stock(self) -> None:
        inventory = Inventory({"MUG": 1})
        store = InMemoryOrderStore()
        service = OrderService(
            catalog={"MUG": Decimal("25.00")},
            inventory=inventory,
            store=store,
        )

        with self.assertRaises(OutOfStockError):
            service.place_order("MUG", quantity=2)

        self.assertEqual(inventory.available("MUG"), 1)
        self.assertIsNone(store.get("order-1"))

    def test_reserving_all_stock_still_succeeds(self) -> None:
        inventory = Inventory({"MUG": 2})

        inventory.reserve("MUG", quantity=2)

        self.assertEqual(inventory.available("MUG"), 0)


if __name__ == "__main__":
    unittest.main()
