import argparse
from decimal import Decimal
import json

from order_service.inventory import Inventory
from order_service.service import OrderService
from order_service.storage import InMemoryOrderStore


CATALOG = {
    "LAMP": Decimal("50.00"),
    "MUG": Decimal("25.00"),
    "NOTEBOOK": Decimal("12.50"),
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Place a sample order")
    parser.add_argument("sku", choices=sorted(CATALOG))
    parser.add_argument("quantity", type=int)
    args = parser.parse_args()

    service = OrderService(
        catalog=CATALOG,
        inventory=Inventory({sku: 20 for sku in CATALOG}),
        store=InMemoryOrderStore(),
    )
    order = service.place_order(args.sku, args.quantity)
    print(
        json.dumps(
            {
                "order_id": order.order_id,
                "sku": order.sku,
                "quantity": order.quantity,
                "total": str(order.total),
            }
        )
    )


if __name__ == "__main__":
    main()

