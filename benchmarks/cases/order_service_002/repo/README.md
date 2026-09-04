# Order service fixture

This small Python service validates an order, reserves inventory, computes the
price, and stores the result in memory. It has no third-party dependencies.

Run the tests from this directory:

```powershell
python -m unittest discover -s tests -v
```

Run the example entry point:

```powershell
python -m order_service.app MUG 4
```

