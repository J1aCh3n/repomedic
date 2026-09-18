# Command output formatting validation

Recorded 2026-09-18T20:35:28.157859+00:00 on Windows / Python 3.12.
Base revision: `17cd588`. No live-model API calls or dependency changes.

`bash` and `run_tests` return plain-text status fields and stdout/stderr sections.
Newlines and original model-visible code are preserved. Each stream retains its
head and tail under truncation; status fields remain at the beginning. The total
model response stays within 8,000 characters. Stored command records stay
structured, and saved logs remain sanitized.

- Offline tests: 53 tests, 2 platform skips, no failures.
- Compilation, dependency consistency, CLI help, Git whitespace and package builds passed.
- Commands and real exit codes: `.repomedic/command-output-checks.json`.
- Scripted Docker fix/export: `runs/tool-loop-gates/fix/order_service_001/20260918T203515Z-2188b843/result.json`.
- Separate Docker public/evaluator grade: `runs/tool-loop-gates/eval/order_service_001/20260918T203520Z-119cebf9/grade.json`.

Run artifacts are local, ignored files. These gates do not measure live-model
repair performance. Fixture inputs and evaluator source are unchanged.
