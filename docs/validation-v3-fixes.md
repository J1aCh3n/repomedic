# v3 review corrections: validation

Recorded 2026-09-18T20:18:40.541046+00:00 on Windows / Python 3.12.
Base revision: `da7d9d5`. These are deterministic checks, not live-model performance results.

## Changes verified

- Normal `token`, `password`, and `api_key` code survives local review, CLI display and exact patch export.
- Model read and bash responses retain workspace code; trace and observation logs remain sanitized.
- Command observations are sanitized as structured data before JSON serialization.
- Invalid absolute/traversal scope requests are recoverable tool errors, with no partial scope update.
- Blank bash requests return a tool error without starting a container.
- Three consecutive replies without tools terminate as `stalled`; tool calls reset the counter.
- Default 200 graph steps support all 80 tool calls, followed by checks, review and approval/export.
- Protected workspace modifications still terminate, and approval drift still requires fresh review.

Prompt version is `tool-loop-v3.2` with explicit relative-path instructions.
The CLI requires the matching prompt version for checkpoints; use the original revision for older runs.

## Results and evidence

- Offline tests: 51 tests, 2 platform skips, no failures.
- Compilation, dependency consistency, CLI help, whitespace checks and wheel/source builds passed.
- Fixture gate: `runs/fixture-gates/20260918T201550Z-d1ec70e2/summary.json`
- Scripted fix/approval/export Docker gate: `runs/tool-loop-gates/fix/order_service_001/20260918T201828Z-c97f0cb4/result.json`
- Separate public/evaluator Docker grade: `runs/tool-loop-gates/eval/order_service_001/20260918T201832Z-a82ff2cf/grade.json`
- Commands, exit codes and offline output: `.repomedic/v3-fixes-checks.json`

Run artifacts are retained locally in the ignored `runs/` tree.
The fixture results below were generated from the saved summary, not manually entered.

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

No live API calls or new dependency changes were required.
Unittest-only support, scan limits, reserved evaluator paths and writable grader scratch
remain documented limitations. Development eval reports disclose supplemental `expected_behavior`.
