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

from order_service.inventory import Inventory  # noqa: E402
from order_service.service import OrderService  # noqa: E402
from order_service.storage import InMemoryOrderStore  # noqa: E402


def make_service() -> tuple[OrderService, Inventory, InMemoryOrderStore]:
    inventory = Inventory({"MUG": 5})
    store = InMemoryOrderStore()
    service = OrderService(
        catalog={"MUG": Decimal("25.00")},
        inventory=inventory,
        store=store,
    )
    return service, inventory, store


class CancellationEvaluatorTests(unittest.TestCase):
    def test_unknown_cancellation_has_no_side_effects(self) -> None:
        service, inventory, store = make_service()

        with self.assertRaisesRegex(ValueError, "unknown"):
            service.cancel_order("order-999")

        self.assertEqual(inventory.available("MUG"), 5)
        self.assertIsNone(store.get("order-999"))

    def test_second_cancellation_fails_without_restocking_twice(self) -> None:
        service, inventory, _ = make_service()
        order = service.place_order("MUG", quantity=2)
        service.cancel_order(order.order_id)

        with self.assertRaises(ValueError):
            service.cancel_order(order.order_id)

        self.assertEqual(inventory.available("MUG"), 5)

    def test_cancelled_order_id_is_not_reused(self) -> None:
        service, _, store = make_service()
        first = service.place_order("MUG", quantity=1)
        service.cancel_order(first.order_id)

        second = service.place_order("MUG", quantity=1)

        self.assertEqual((first.order_id, second.order_id), ("order-1", "order-2"))
        self.assertIsNone(store.get(first.order_id))
        self.assertEqual(store.get(second.order_id), second)


if __name__ == "__main__":
    unittest.main()
