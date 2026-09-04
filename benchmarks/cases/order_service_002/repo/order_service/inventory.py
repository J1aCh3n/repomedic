class OutOfStockError(ValueError):
    """Raised when an order requests more units than are available."""


class Inventory:
    def __init__(self, stock: dict[str, int]) -> None:
        self._stock = dict(stock)

    def available(self, sku: str) -> int:
        return self._stock.get(sku, 0)

    def reserve(self, sku: str, quantity: int) -> None:
        if self.available(sku) < quantity:
            raise OutOfStockError(f"insufficient stock for {sku}")
        self._stock[sku] -= quantity

