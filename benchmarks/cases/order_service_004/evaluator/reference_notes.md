# Maintainer reference

Cancellation spans three existing responsibilities: `OrderService` coordinates
the operation, `Inventory` restores stock, and `InMemoryOrderStore` removes the
order while retaining a monotonic ID counter. Reusing `len(_orders) + 1` after a
deletion would otherwise overwrite a later order.

The evaluator checks unknown and repeated cancellation side effects as well as
ID uniqueness after deletion. Equivalent behavior-preserving repairs are
acceptable; exact patch equality is not part of scoring.
