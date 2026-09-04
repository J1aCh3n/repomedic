# RepoMedic benchmarks

The current benchmark implementation includes the first fixture vertical slice
and deterministic harness. Agent orchestration has not yet been implemented.

## Layout

- `fixtures/order_service/` is the clean `order_service-v1` baseline.
- `cases/order_service_001/repo/` is the faulty repository visible to an Agent.
- `cases/order_service_001/evaluator/` must not be copied or mounted into an
  Agent workspace.
- `cases/order_service_001/manifest.yaml` defines the case contract.

The fixture uses only the Python standard library. The commands below use
`python`; substitute the path to a Python 3.11+ executable if it is not on
`PATH`.

## Verify the clean baseline

From `benchmarks/fixtures/order_service`:

```powershell
python -m unittest discover -s tests -v
python -m order_service.app MUG 4
```

The tests should pass and the example order total should be `90.00`.

## Reproduce the faulty case

From `benchmarks/cases/order_service_001/repo`:

```powershell
python -m unittest discover -s tests -v
```

Exactly the threshold test should fail: the faulty implementation returns
`100.00` instead of `90.00`.

From `benchmarks/cases/order_service_001`:

```powershell
python -m unittest discover -s evaluator/hidden_tests -v
```

Exactly the evaluator's alternate threshold test should fail. The evaluator
defaults to testing this case's `repo/`. To validate a repaired disposable
copy, set `REPOMEDIC_REPO_UNDER_TEST` to that copy's absolute path before
running the evaluator command.

`evaluator/reference.patch` is maintainer-only evidence for the fixture gate.
It is not an exact-patch scoring target and must not be exposed to the Agent.

The harness copies only `repo/` into a disposable run workspace. Evaluator tests
are mounted into a separate Docker container path only during deterministic
evaluation, while the repaired workspace is mounted read-only. See the root
README for harness commands and security limits.
