# Qwen key fallback and sandbox error classification: validation

Recorded 2026-09-18 on Windows / Python 3.12. Base revision: `e68c39e`.

## Changes verified

- Qwen reads `DASHSCOPE_API_KEY`, falling back to `QWEN_API_KEY`. When both are
  set, `DASHSCOPE_API_KEY` wins; blank values are treated as unset.
- Blank or null-byte `bash` commands are rejected in the tool layer as `ToolDenied`,
  so the model can correct them without a container starting.
- Other `SandboxError`s are no longer returned to the model as tool errors. They end
  the run as `infrastructure_error` from `bash`, `run_tests` or the submission check,
  with `result.json` and `final-report.md` written and no further model calls.

## Results

- Offline tests: 62 tests, 2 platform skips, no failures. Git whitespace check passed.
- Live Qwen `fix` on `order_service_001` with only `QWEN_API_KEY` set: `awaiting_review`,
  5 model calls, 8,565 tokens. The run was rejected afterwards; no patch was exported.

This is an integration check, not a performance measurement.
