# v3 implementation validation

Recorded 2026-09-18T19:59:18.632331+00:00 on Windows with Python 3.12.
This is deterministic implementation and fixture validation, not a live-model benchmark.
The twelve cases are development data; reference repairs are maintainer-provided.

- Offline tests: 43 tests, 2 platform skips, no failures.
- Compilation, dependency consistency, CLI smoke tests and Git whitespace checks passed.
- Wheel and source distribution built locally; hosted CI has not run for this branch.
- Installed dependency audit found no known vulnerabilities at validation time.
- Responses tool-call IDs and encrypted reasoning replay were verified using a mocked SDK transport.
- Docker gate verified shell/scratch access, checkpoint reopening, recorded scripted approval and patch export.
- Separate evaluation gate passed public and evaluator tests without review or patch export.

## Saved run evidence

Paths below are relative to the project root. Run artifacts remain in the local ignored `runs/` tree.

- Fixture gate: `runs/fixture-gates/20260918T195302Z-d7388e6e/summary.json`
- Fix gate: `runs/tool-loop-gates/fix/order_service_001/20260918T195757Z-c17f1f6b/result.json`
- Eval gate: `runs/tool-loop-gates/eval/order_service_001/20260918T195801Z-464347db/grade.json`
- Offline checks: `.repomedic/final-checks.log`
- Dependency audit: `.repomedic/dependency-audit.json`

## Fixture outcomes

This table was generated from the fixture gate's saved `summary.json`.

| Case | Injected fault | Reference repair |
| --- | --- | --- |
| order_service_001 | tests_failed | verified |
| order_service_002 | tests_failed | verified |
| order_service_003 | tests_failed | verified |
| order_service_004 | tests_failed | verified |
| task_scheduler_005 | tests_failed | verified |
| task_scheduler_006 | tests_failed | verified |
| task_scheduler_007 | tests_failed | verified |
| task_scheduler_008 | tests_failed | verified |
| document_pipeline_009 | tests_failed | verified |
| document_pipeline_010 | tests_failed | verified |
| document_pipeline_011 | tests_failed | verified |
| document_pipeline_012 | tests_failed | verified |

All clean fixture public tests passed. Docker image:

```text
python:3.11-slim@sha256:9534e5a8e315485d4061ed659af0fd78a284c015f9b73661b41d6bab25604534
```

Remaining limits are documented in README and SECURITY: no writable-bind disk quota,
no crash recovery during tool execution, UTF-8 content patches only, and unvalidated default budgets.
