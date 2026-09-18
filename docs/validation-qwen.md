# Qwen integration validation

Recorded 2026-09-18T21:13:31.303529+00:00 on Windows / Python 3.12.
Base revision: `f6f0825`. No dependency changes or new live API calls.

## Native provider behavior

`fix` and `eval` accept `--provider qwen`, `--base-url`, and
`--thinking` / `--no-thinking`. Qwen uses `DASHSCOPE_API_KEY`, Chat Completions,
non-parallel function tools, and a bounded `max_tokens` output. Scope forces the
`declare_scope` tool with thinking disabled; agent turns use `auto` tool choice
and the configured thinking mode. No Responses-only parameters are sent.
Provider, endpoint and thinking settings are saved in suite/case configs and
summaries. CLI revision reloads those settings; approval needs no model or key.
OpenAI remains the default with Responses API. API/model errors remain visible.

An actual ChatOpenAI client with mocked HTTP validates request bodies, tool-call
IDs and their replay, usage accounting, output limits and omission of private
Qwen reasoning from messages. CLI and grader tests validate settings persistence.

- Offline suite: 59 tests, 2 platform skips, no failures.
- Compilation, dependency consistency, CLI help, Git whitespace checks and offline wheel/source builds passed.
- Twelve-case Docker fixture gate: `runs/fixture-gates/20260918T211233Z-f5faf540/summary.json`, status `verified`.
- Commands, real exit codes and artifact paths: `.repomedic/qwen-checks.json`.
- Scripted Docker fix/export: `runs/tool-loop-gates/fix/order_service_001/20260918T211220Z-915c3fa2/result.json`.
- Separate Docker public/evaluator grade: `runs/tool-loop-gates/eval/order_service_001/20260918T211224Z-bb2a2510/grade.json`.

These checks do not establish live compatibility of the newly committed provider.
The invoking process had no loaded API key. Models requiring streaming or lacking
the documented thinking switch are outside this integration's verified contract.

## Earlier temporary-adapter evidence

The following counts were generated from the two saved summaries below:
11 verified out of 12 cases; 304,697 total tokens.
Model: `qwen3.7-plus`. These were temporary-adapter runs, not a live evaluation of
this native integration. Per-task token limits differed: 100,000 in the
first run and 60,000 in the second. Both summaries are complete.

- [20260918T205011Z-9a906a30](../runs/evals-qwen/qwen_smoke/20260918T205011Z-9a906a30/summary.json)
- [20260918T205102Z-994d1fc6](../runs/evals-qwen/qwen_smoke/20260918T205102Z-994d1fc6/summary.json)

This is the twelve-case development set with manifest `expected_behavior`
provided to the agent, not an untouched holdout. Preserve the original failed
`order_service_004` row; do not count an unperformed regrade as a success.

## Recommendation for order_service_004

Its issue specifies `ValueError` for unknown/already-cancelled IDs and no stock
changes. The hidden test additionally requires the message substring `unknown`,
which the issue and manifest specification do not require. The saved candidate
uses `ValueError("order order-999 not found")`.

Change only that wording assertion to `assertRaises(ValueError)`, retain inventory
and storage side-effect assertions, repeated-cancellation checks and ID checks.
Record a new evaluator hash/version and regrade a separate copy of the saved
candidate with fresh public and hidden tests. Keep both original and revised
results. Regrading does not require another model call. Passing that regrade is
necessary before claiming verified success; this review alone is not proof of
all candidate behavior. No benchmark fixture or evaluator was edited in this task.

## API reference

Alibaba's [function calling documentation](https://www.alibabacloud.com/help/en/model-studio/qwen-function-calling)
describes forced tool choice in non-thinking mode. Its
[OpenAI-compatible API documentation](https://www.alibabacloud.com/help/en/model-studio/compatibility-of-openai-with-dashscope)
describes region-bound endpoints and credentials. Use the endpoint assigned to
your account/workspace if it differs from the international default.
