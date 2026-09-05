# RepoMedic benchmarks

The current benchmark implementation includes an eight-case development suite,
deterministic harness, and Agent graph. The preserved v1 staged result and its
failure analysis are in `results/initial_8_20260905.md`; the current graph uses
the remediated v2 prompt and protocol.

## Layout

- `fixtures/order_service/` and `fixtures/task_scheduler/` are clean baselines.
- `cases/<case-id>/repo/` is the faulty or feature-incomplete repository visible
  to an Agent.
- `cases/<case-id>/evaluator/` must not be copied or mounted into an Agent
  workspace.
- `cases/<case-id>/manifest.yaml` defines each case contract.
- `suites/order_service_4.yaml` preserves the first measured checkpoint.
- `suites/initial_8.yaml` freezes the expanded eight-case suite.

## Four-case development suite

| Case | Category | Target behavior |
| --- | --- | --- |
| `order_service_001` | local logic bug | Inclusive bulk-discount threshold |
| `order_service_002` | cross-module contract bug | Stable `order-N` string IDs |
| `order_service_003` | edge/regression bug | Failed reservations are atomic |
| `order_service_004` | small feature | Safe order cancellation across three modules |

All four are development cases. They may be used to debug the runner or prompts
and therefore must not later be relabeled as untouched holdout evidence.

## Eight-case development suite

The second fixture adds the same four task categories without modifying the
first measured checkpoint.

| Case | Category | Target behavior |
| --- | --- | --- |
| `task_scheduler_005` | local logic bug | Allow adjacent half-open task intervals |
| `task_scheduler_006` | cross-module contract bug | Normalize parsed timestamps to UTC |
| `task_scheduler_007` | edge/regression bug | Failed requests do not consume task IDs |
| `task_scheduler_008` | small feature | Atomic rescheduling across policy, scheduler, and storage |

All eight cases remain development cases. Validate both clean fixtures, all
faulty/incomplete inputs, and all maintainer reference repairs with:

```powershell
python -m scripts.validate_suite benchmarks/suites/initial_8.yaml
```

The command writes its evidence below `runs/suite-gates/initial_8/<run-id>/`.
All current manifests reserve at least 43 bounded repository operations. That
minimum covers the schema's maximum initial investigation and two direct repair
iterations; repeated Reviewer replanning remains bounded by the same cap.

With Docker Desktop running, reproduce every faulty state and reference repair:

```powershell
python -m scripts.validate_suite benchmarks/suites/order_service_4.yaml
```

The four-case command writes a generated `summary.json` and `summary.md` below
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
