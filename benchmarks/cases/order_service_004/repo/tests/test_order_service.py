from decimal import Decimal
import unittest

from order_service.inventory import Inventory
from order_service.pricing import calculate_total
from order_service.service import OrderService
from order_service.storage import InMemoryOrderStore


class PricingTests(unittest.TestCase):
    def test_bulk_discount_applies_at_threshold(self) -> None:
        total = calculate_total(Decimal("25.00"), quantity=4)

        self.assertEqual(total, Decimal("90.00"))

    def test_total_below_threshold_is_unchanged(self) -> None:
        total = calculate_total(Decimal("12.50"), quantity=7)

        self.assertEqual(total, Decimal("87.50"))


class OrderServiceTests(unittest.TestCase):
    def test_cancel_order_restores_inventory_and_removes_order(self) -> None:
        inventory = Inventory({"MUG": 5})
        store = InMemoryOrderStore()
        service = OrderService(
            catalog={"MUG": Decimal("25.00")},
            inventory=inventory,
            store=store,
        )
        order = service.place_order("MUG", quantity=2)

        cancelled = service.cancel_order(order.order_id)

        self.assertEqual(cancelled, order)
        self.assertEqual(inventory.available("MUG"), 5)
        self.assertIsNone(store.get(order.order_id))

    def test_place_order_reserves_stock_and_persists_order(self) -> None:
        inventory = Inventory({"MUG": 5})
        store = InMemoryOrderStore()
        service = OrderService(
            catalog={"MUG": Decimal("25.00")},
            inventory=inventory,
            store=store,
        )

        order = service.place_order("MUG", quantity=2)

        self.assertEqual(order.total, Decimal("50.00"))
        self.assertEqual(inventory.available("MUG"), 3)
        self.assertEqual(store.get(order.order_id), order)

    def test_place_order_rejects_non_positive_quantity(self) -> None:
        service = OrderService(
            catalog={"MUG": Decimal("25.00")},
            inventory=Inventory({"MUG": 5}),
            store=InMemoryOrderStore(),
        )

        with self.assertRaisesRegex(ValueError, "positive"):
            service.place_order("MUG", quantity=0)


if __name__ == "__main__":
    unittest.main()
