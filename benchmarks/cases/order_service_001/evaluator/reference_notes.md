# Maintainer reference

The faulty comparison excludes the exact threshold. The reference repair changes
the strict comparison in `order_service/pricing.py` from `>` to `>=`.

The patch is guidance for maintainers only. RepoMedic must determine success from
public tests, evaluator tests, regression behavior, and policy checks rather than
from exact patch equality.

