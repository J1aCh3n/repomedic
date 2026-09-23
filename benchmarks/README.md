# Development tasks

The twelve Python repair cases are development data for the v3 tool loop. They
are not confidential or untouched holdouts. Manifests no longer contain an edit
allowlist or limits; scope comes from the model and budgets from the harness.

- `fixtures/`: three clean Python repositories.
- `cases/<id>/repo/`: faulty or feature-incomplete Agent-visible input.
- `cases/<id>/evaluator/`: maintainer reference repair and evaluator tests,
  never mounted for Agent operations.
- `cases/<id>/manifest.yaml`: issue, fixture identity and test contracts.
- `suites/initial_12.yaml`: all twelve development tasks; smaller development
  subsets remain usable with the taskset loader.
- `results/`: historical workflow evidence for commit
  `9154e8ce8ac32a871b339f6be5168f4ec4f3710b`, not v3 measurements.

```powershell
python -m scripts.validate_suite benchmarks/suites/initial_12.yaml
python -m repomedic eval benchmarks/suites/initial_12.yaml --model YOUR_MODEL_ID
```

The fixture gate spends no model credits. Live eval runs one independent repair
per task, skips human review and never exports patches. The grader restores
original `tests/` in a separate copy, runs public plus evaluator tests and saves
all task outcomes. `summary.json` is the source for success rate; failures count
in its denominator. Reference-patch equality is never a success criterion.

Do not pool v3 eval with old pass@1/pass@3, Reviewer or memory ablation results.
