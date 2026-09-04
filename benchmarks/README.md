# RepoMedic benchmarks

The current benchmark implementation includes the first four-case development
suite, deterministic harness, and Agent graph. Live-model results have not been
recorded yet.

## Layout

- `fixtures/order_service/` is the clean `order_service-v1` baseline.
- `cases/<case-id>/repo/` is the faulty or feature-incomplete repository visible
  to an Agent.
- `cases/<case-id>/evaluator/` must not be copied or mounted into an Agent
  workspace.
- `cases/<case-id>/manifest.yaml` defines each case contract.
- `suites/order_service_4.yaml` freezes the first development suite.

## Four-case development suite

| Case | Category | Target behavior |
| --- | --- | --- |
| `order_service_001` | local logic bug | Inclusive bulk-discount threshold |
| `order_service_002` | cross-module contract bug | Stable `order-N` string IDs |
| `order_service_003` | edge/regression bug | Failed reservations are atomic |
| `order_service_004` | small feature | Safe order cancellation across three modules |

All four are development cases. They may be used to debug the runner or prompts
and therefore must not later be relabeled as untouched holdout evidence.

With Docker Desktop running, reproduce every faulty state and reference repair:

```powershell
python -m scripts.validate_suite benchmarks/suites/order_service_4.yaml
```

The command writes a generated `summary.json` and `summary.md` below
`runs/suite-gates/order_service_4/<run-id>/`. A valid gate observes
`tests_failed` for every input case and `verified` after every maintainer
reference repair.

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
