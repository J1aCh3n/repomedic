# Six-case preflight configuration ablation

## Outcome

This live development ablation ran the same six stratified cases three times
under each of four configurations. It used `gpt-5.6-terra`, reasoning effort
`low`, prompt `agent-graph-v5`, and the `preflight_6` suite.

| Configuration | Memory | Verified | pass@1 | pass@3 | Tokens | Model calls | Tool calls | Model latency |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `single_agent` | no | 17/18 | 94.4% | 100.0% | 130,805 | 89 | 175 | 275.845 s |
| `multi_agent_no_review` | no | 18/18 | 100.0% | 100.0% | 129,876 | 72 | 307 | 254.113 s |
| `multi_agent_review` | no | 18/18 | 100.0% | 100.0% | 162,656 | 90 | 297 | 316.891 s |
| `multi_agent_review_with_memory` | yes | 18/18 | 100.0% | 100.0% | 171,552 | 90 | 290 | 329.613 s |

Across all four arms, 71 of 72 case-runs were verified. Every configuration
achieved pass@3 on all six cases. The single-agent arm's lower pass@1 came from
one failed attempt for `task_scheduler_008`; all other case-attempt pairs were
verified.

## Failure analysis

`single_agent/task_scheduler_008/attempt_1` ended in `tool_error` before the
human approval gate. Proposal preview attempted an exact text replacement in
`task_scheduler/scheduler.py`, but the expected old text was absent:

```text
expected one exact match in task_scheduler/scheduler.py, found 0
```

This is an edit-generation or edit-application failure, not a test failure.
The recorded attempt was preserved and was not replaced or rerun. Attempts 2
and 3 for the same case verified, which is why the arm still reached pass@3.

## Configuration effects

- Moving from `single_agent` to `multi_agent_no_review` removed the one failed
  attempt while using 929 fewer tokens, 17 fewer model calls, and 21.732 seconds
  less model latency. It did use 132 more repository tool calls.
- Adding Reviewer reflection to the no-review workflow did not change verified
  outcomes or pass@k. It added 32,780 tokens (25.2%), 18 model calls, and 62.778
  seconds of model latency (24.7%).
- Adding memory to the Reviewer workflow also did not change verified outcomes
  or pass@k. It added 8,896 tokens (5.5%) and 12.722 seconds of model latency
  (4.0%), with the same number of model calls.

On this subset, the evidence supports the no-review multi-agent configuration
as the most efficient observed arm. It does not establish that Reviewer or
memory are generally harmful: the baseline was already at 18/18 without them,
so the selected cases leave no success-rate headroom for either feature.

## Memory controls

The treatment used six verified lessons from non-target cases, two per fixture:

- `order_service_002`, `order_service_003`
- `task_scheduler_005`, `task_scheduler_007`
- `document_pipeline_009`, `document_pipeline_012`

All 18 treatment runs retrieved memory, for 36 retrieved entries in total. The
benchmark recorded a six-entry corpus hash of
`ac75e7e1f56ffa12f73b7221b641b19bfe305b147e479a2c06e3d9209ca7afaf`.
The SQLite corpus contained six rows before and after the run, and its semantic
row-content SHA-256 remained
`7e534c9cc33fada72c44d4e732130ec4c733c831c2bfa9945a756b54b50c037b`.
The treatment therefore did not learn from earlier attempts inside this
benchmark.

## Approval and evidence boundaries

Codex performed the approval role under the user's explicit authorization.
Before each approval, it inspected the saved diff and checked changed paths
against the case manifest. The deterministic harness then applied each approved
proposal in an isolated workspace and ran public, regression, evaluator-only,
and policy checks. This is an operator-assisted audit, not an independent or
blinded human review.

These six cases are selected development cases, not a held-out sample. With
only three attempts per case, one attempt changes aggregate pass@1 by 5.6
percentage points, and one completely failed case would change pass@3 by 16.7
points. The result must not be presented as a complete twelve-case comparison,
a statistically robust ranking, or evidence that memory improves performance.

Generated evidence is retained in ignored run directories:

- `runs/benchmarks/preflight_6/preflight6-single-v2/`
- `runs/benchmarks/preflight_6/preflight6-no-review-v2/`
- `runs/benchmarks/preflight_6/preflight6-review-v2/`
- `runs/benchmarks/preflight_6/preflight6-review-memory-v2/`
- `runs/configuration-ablation/preflight-6-v2/`
- `runs/memory-ablation/preflight-6-v2/`
- `runs/preflight-comparison/preflight-6-v2/`
