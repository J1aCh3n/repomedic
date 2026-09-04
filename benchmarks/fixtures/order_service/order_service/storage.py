from order_service.models import Order


class InMemoryOrderStore:
    def __init__(self) -> None:
        self._orders: dict[str, Order] = {}

    def next_order_id(self) -> str:
        return f"order-{len(self._orders) + 1}"

    def save(self, order: Order) -> None:
        self._orders[order.order_id] = order

    def get(self, order_id: str) -> Order | None:
        return self._orders.get(order_id)

