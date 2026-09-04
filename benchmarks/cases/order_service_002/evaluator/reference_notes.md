# Maintainer reference

`InMemoryOrderStore.next_order_id()` broke its string contract by returning an
integer. The value flows through `OrderService` into `Order.order_id` and becomes
the storage key, so the visible symptom crosses the storage, service, and model
boundaries.

The reference repair restores the established `order-N` value at the source.
Equivalent behavior-preserving repairs are acceptable; exact patch equality is
not part of scoring.
