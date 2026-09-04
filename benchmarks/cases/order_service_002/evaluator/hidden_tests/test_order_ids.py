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


class OrderIdEvaluatorTests(unittest.TestCase):
    def test_ids_remain_strings_and_advance_after_save(self) -> None:
        store = InMemoryOrderStore()
        service = OrderService(
            catalog={"MUG": Decimal("25.00")},
            inventory=Inventory({"MUG": 5}),
            store=store,
        )

        first = service.place_order("MUG", quantity=1)
        second = service.place_order("MUG", quantity=1)

        self.assertIsInstance(first.order_id, str)
        self.assertEqual((first.order_id, second.order_id), ("order-1", "order-2"))
        self.assertEqual(store.get(second.order_id), second)


if __name__ == "__main__":
    unittest.main()
