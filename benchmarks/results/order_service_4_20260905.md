# Initial benchmark: 4 order-service cases

## Outcome

RepoMedic verified all four development cases under the same
`multi-agent-review-v1` protocol. Every accepted repair stayed within its path
allowlist and passed both the public tests and hidden evaluator.

- Run ID: `20260905T000329Z-92ec493c`
- Model: `gpt-5.6-terra`
- Reasoning effort: `low`
- Prompt version: `agent-graph-v1`
- Verified: `4/4` (100%)
- Policy compliant: `4/4` (100%)
- Repair iterations: `1` per successful case

| Case | Category | Successful attempt | Public | Hidden | Model calls | Input tokens | Output tokens | Cumulative model latency | Average per call |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `order_service_001` | Local threshold bug | 3 | 4/4 | 3/3 | 5 | 4,715 | 876 | 31.000 s | 6.200 s |
| `order_service_002` | Cross-module ID contract | 1 | 4/4 | 1/1 | 5 | 5,999 | 1,244 | 19.047 s | 3.809 s |
| `order_service_003` | Failure atomicity regression | 1 | 5/5 | 3/3 | 5 | 5,439 | 1,013 | 16.234 s | 3.247 s |
| `order_service_004` | Cancellation feature | 1 | 5/5 | 3/3 | 5 | 8,315 | 2,304 | 25.954 s | 5.191 s |
| **Valid total** |  |  |  |  | **20** | **24,468** | **5,437** | **92.235 s** | **4.612 s** |

`Cumulative model latency` sums the measured round-trip duration of the model
calls in a case. It is not the latency of one response and does not include
Docker test time. The five successful-path calls are Planner, investigation
selection, Investigator, Coder, and Reviewer. The timer includes the Responses
API request, network/service time, structured-output parsing, and local schema
validation.

At the published Terra rates used for this run, the successful attempts cost an
estimated USD 0.114 before any cached-input discount.

## Infrastructure incidents

Case `order_service_001` required two discarded attempts before the valid run:

| Attempt | Result | Cause | Model calls | Input tokens | Output tokens | Model latency |
|---|---|---|---:|---:|---:|---:|
| 1 | `model_error` | Approval server lacked outbound network access | 4 | 3,647 | 665 | 23.157 s |
| 2 | `review_error` | Docker was unavailable; the graph rejected reviewer `pass` because public tests had failed | 5 | 4,847 | 954 | 15.687 s |

These attempts are excluded from the 4/4 capability result because the failures
were in the evaluation infrastructure, not in a completed model repair. They are
included in actual expenditure: all attempts together used 32,962 input tokens,
7,056 output tokens, and 29 recorded model calls, for an estimated USD 0.151
before cached-input discounts.

Docker Desktop failed because stale Windows AF_UNIX runtime sockets prevented
its backend from starting. The stale runtime directories were moved aside, the
engine was restarted, and the valid attempt was run only after `docker ps`
succeeded.

## Interpretation

This result validates the end-to-end graph, human approval boundary, policy
checks, deterministic Docker harness, hidden evaluator, checkpoint recovery,
and metrics collection on the current four cases. It does not yet establish
general repair reliability: the sample is small and all cases share one compact
Python order-service fixture. The next eight-case stage should add new failure
shapes and repository interactions before comparing pass rate, latency, and
token growth with this baseline.

Raw checkpoints and traces remain in the local ignored run directory:
`runs/benchmarks/order_service_4/20260905T000329Z-92ec493c/`.
