from decimal import Decimal

from order_service.inventory import Inventory
from order_service.models import Order
from order_service.pricing import calculate_total
from order_service.storage import InMemoryOrderStore


class OrderService:
    def __init__(
        self,
        catalog: dict[str, Decimal],
        inventory: Inventory,
        store: InMemoryOrderStore,
    ) -> None:
        self._catalog = dict(catalog)
        self._inventory = inventory
        self._store = store

    def place_order(self, sku: str, quantity: int) -> Order:
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        if sku not in self._catalog:
            raise ValueError(f"unknown sku: {sku}")

        self._inventory.reserve(sku, quantity)
        order = Order(
            order_id=self._store.next_order_id(),
            sku=sku,
            quantity=quantity,
            total=calculate_total(self._catalog[sku], quantity),
        )
        self._store.save(order)
        return order

