from decimal import Decimal, ROUND_HALF_UP


BULK_DISCOUNT_THRESHOLD = Decimal("100.00")
BULK_DISCOUNT_RATE = Decimal("0.10")
CENTS = Decimal("0.01")


def calculate_total(unit_price: Decimal, quantity: int) -> Decimal:
    """Return the order total after the bulk discount, rounded to cents."""
    subtotal = unit_price * quantity

    if subtotal > BULK_DISCOUNT_THRESHOLD:
        subtotal *= Decimal("1.00") - BULK_DISCOUNT_RATE

    return subtotal.quantize(CENTS, rounding=ROUND_HALF_UP)

