# RepoMedic

> Early implementation. The deterministic harness, configurable Agent graph,
> and twelve-case development dataset are complete. A stratified six-case,
> four-configuration live development ablation is complete; it found no
> measurable Reviewer or memory success-rate uplift on this saturated subset.
> The full twelve-case comparison and held-out evaluation remain future work.

RepoMedic is a proposed LangGraph-based multi-agent coding system that turns a small repository issue into a tested patch and an auditable evidence bundle. It is intended to extend the ideas explored in [`langgraph_file_editor`](../langgraph_file_editor/) from controlled file operations to repository-level diagnosis, implementation, testing, reflection, and human approval.

This is an experimental learning project, not a production repair service. See
[`CONTRIBUTING.md`](CONTRIBUTING.md) before proposing changes and
[`SECURITY.md`](SECURITY.md) for the security model and reporting process.

The project must demonstrate real Agent-system engineering rather than a collection of role prompts. A deterministic orchestrator owns state transitions, budgets, retries, and safety. LLM agents perform only the steps that require model judgment.

## Project objective

Given:

- a reproducible Python repository fixture;
- an issue describing a bug or small feature;
- allowed paths and execution limits;

RepoMedic should:

1. create a structured repair plan;
2. inspect the repository and collect code evidence;
3. generate a minimal patch in an isolated workspace;
4. run deterministic public, regression, and evaluator-only tests;
5. review the patch and either accept, revise, replan, or stop;
6. pause for human approval before exporting the final patch;
7. save enough evidence to reproduce and audit the run.

## Non-goals for the first version

- Supporting every programming language or arbitrary public repositories.
- Automatically committing, pushing, opening pull requests, or merging code.
- Giving agents unrestricted shell or filesystem access.
- Using LangGraph, AutoGen, CrewAI, and MetaGPT together for keyword coverage.
- Claiming production readiness, autonomous software engineering, or benchmark superiority without measured evidence.
- Treating a task board, chat history, or vector database alone as proof of long-term memory.

## Proposed architecture

```text
Issue + repository fixture
          |
          v
Deterministic LangGraph orchestrator
          |
          v
Planner Agent
          |
          +--------------------+
          v                    v
Investigator Agent       acceptance/test plan
          |                    |
          +----------+---------+
                     v
                 Coder Agent
                     |
                     v
         deterministic test runner
                     |
                     v
               Reviewer Agent
          +----------+----------+
          |          |          |
        pass       revise     replan/stop
          |          |          |
          v          +--> Coder +--> Planner
   human approval
          |
          v
Patch + tests + trace + final report
```

The orchestrator is not an LLM agent. It must enforce transitions, iteration limits, timeouts, budgets, checkpointing, and approval gates in normal Python code.

### Agent responsibilities

| Component | Responsibility | Permitted capabilities |
| --- | --- | --- |
| Planner Agent | Convert the issue into acceptance criteria, investigation tasks, candidate areas, and a repair plan | Repository summary and read-only metadata |
| Investigator Agent | Locate relevant code, dependencies, contracts, and likely root cause with file-level evidence | Read-only file listing, search, and file reads |
| Coder Agent | Produce the smallest patch consistent with the plan and evidence | Allowlisted edits inside an isolated worktree |
| Reviewer Agent | Compare the issue, diff, and real test results; return a structured verdict | Read-only issue, diff, evidence, and test results |
| Test runner | Execute tests and policy checks; report real exit codes and logs | Fixed commands in a sandbox; no LLM judgment |
| Orchestrator | Route state, enforce budgets, persist checkpoints, and handle retries/approval | Application state only |

Each LLM agent must have a distinct prompt, tool allowlist, structured input/output model, and private context. Merely renaming the same unrestricted agent does not count as multi-agent collaboration.

## Benchmark strategy

The first benchmark will use small synthetic repositories instead of collecting large real-world GitHub histories. This is deliberate: every task should be runnable, reproducible, cheap to reset, and supported by known acceptance tests.

### Repository fixtures

Start with three Python repositories. Each repository should contain one runnable entry point and several modules with genuine dependencies or calls between them. A suitable scale is 4-8 source files plus tests.

Illustrative shapes, not committed implementation choices:

```text
order_service/
  app.py -> service.py -> inventory.py
                    \-> pricing.py
                    \-> storage.py

task_scheduler/
  cli.py -> scheduler.py -> parser.py
                         \-> policies.py

document_pipeline/
  main.py -> loader.py -> transform.py -> exporter.py
```

The fixtures must not be folders containing unrelated scripts. At least some tasks must require tracing a value or contract across two or more modules.

### Initial task set

Target 12 cases for the first complete benchmark: four cases per fixture repository.

- Local logic bug: the fault is concentrated in one function.
- Cross-module contract bug: two modules disagree on a value, type, or error contract.
- Edge-case or regression bug: a plausible local fix can break existing behavior.
- Small feature: the implementation must touch more than one layer while preserving prior behavior.

After the complete evaluation pipeline works, the dataset may grow. Dataset size must not expand before the first 12 cases run end to end.

### Case layout

```text
benchmarks/
  cases/
    order_service_001/
      manifest.yaml
      repo/                 # faulty repository visible to the agent
      evaluator/            # not mounted into the agent workspace
        hidden_tests/
        reference_notes.md
```

The evaluator files may be public in the open-source project, but they must be outside the Agent's runtime workspace. A folder named `hidden_tests` inside the Agent-visible repository is not hidden.

Each manifest should eventually record:

- stable case ID and category;
- issue text;
- fixture version or content hash;
- runnable entry point;
- public and evaluator-only test commands;
- allowed and forbidden paths;
- time, tool-call, and iteration limits;
- expected behavioral outcomes;
- optional reference patch for maintainers only.

Exact patch equality must not be the primary success condition. Multiple patches can be correct. Behavioral tests, regression checks, and policy constraints determine success.

## Testing strategy

### 1. Unit tests

Test deterministic infrastructure without a live model:

- state transitions and conditional routing;
- schema validation for every agent response;
- path containment and edit allowlists;
- iteration and budget limits;
- patch application and reset behavior;
- checkpoint save/resume;
- prevention of evaluator-data access;
- log and secret redaction.

### 2. Scripted integration tests

Use scripted or mock model responses to exercise complete paths reproducibly:

- direct success;
- failed test followed by revision;
- reviewer requests replanning;
- malformed model output;
- tool timeout or execution error;
- approval pause, rejection, and resume;
- termination after the maximum iteration count.

### 3. Live-model benchmark

For every measured run, record the model identifier, prompt version, configuration, run seed when supported, tool calls, tokens, latency, test output, and final status. Run each case multiple times because zero temperature does not guarantee identical model behavior.

### 4. Ablation study

Compare at least these configurations on the same cases:

1. single-agent baseline based on the File Editor architecture;
2. multi-agent workflow without Reviewer reflection;
3. multi-agent workflow with Reviewer reflection;
4. multi-agent workflow with reflection and cross-task memory.

This comparison determines whether additional agents and memory improve verified outcomes or merely increase cost.

## Success definition and metrics

A task counts as a verified success only when all of the following are true:

```text
evaluator-only tests pass
AND original regression tests pass
AND path and safety policies pass
AND no unapproved side effect occurred
```

Primary metrics:

- verified task success rate;
- pass@1 and pass@3;
- regression rate;
- unsafe or out-of-scope edit rate;
- tool-failure recovery rate;
- average repair iterations and tool calls;
- token usage, latency, and estimated model cost;
- Reviewer precision and recall against deterministic test outcomes;
- memory uplift versus the no-memory configuration.

All summary tables must be generated from saved run artifacts. Do not hand-select successful examples or insert placeholder numbers as results.

## Memory plan

- **Working memory:** current issue, plan, evidence, patch, tests, and review feedback.
- **Checkpoint state:** exact graph execution state needed to pause and resume one run.
- **Episodic memory:** validated lessons from prior tasks, including failure cause and successful repair strategy.
- **Semantic memory:** repository conventions, architecture facts, and test rules with provenance.

The first vertical slice needs working memory and checkpointing. The project is not considered complete for the target Agent role until episodic or semantic memory is implemented and evaluated with a no-memory ablation.

Unverified model opinions must not be written into long-term memory as facts. A memory entry requires evidence such as passing tests, an approved patch, or a human-confirmed repository rule.

## Human validation

Human involvement has two separate purposes:

1. **Runtime approval:** approve, reject, or edit a proposed high-impact action or final patch before export.
2. **Offline evaluation:** inspect issue compliance, diff quality, maintainability, repository style, unsupported claims, and risks not covered by tests.

Human review must inspect the actual diff and test evidence, not the Agent's self-description. If only one person reviews the benchmark, report it as a single-reviewer manual audit. Do not claim independent or blinded review unless it occurred.

## Run artifacts

Every end-to-end run should eventually produce an auditable bundle:

```text
runs/<case-id>/<run-id>/
  config.json
  plan.json
  investigation.json
  patch.diff
  test-results.json
  review.json
  trace.jsonl
  usage.json
  final-report.md
```

Secrets, full environment variables, hidden-test source, and private chain-of-thought must never be stored in run artifacts.

## Evidence required before public claims

The README may eventually show:

- an architecture diagram;
- a three-minute reproducible demo;
- an automatically generated benchmark comparison;
- one successful first-pass case;
- one test-failure -> reflection -> successful revision case;
- one unsafe action blocked or routed to approval;
- one checkpoint recovery case;
- representative failures and current limitations.

Resume bullets and benchmark claims must be written only after the corresponding implementation and measurements exist.

## Proposed implementation phases

1. **Preflight:** agree on scope, invariants, case format, metrics, and security boundaries.
2. **Fixture vertical slice:** one runnable mini-repository, one faulty case, public tests, and isolated evaluator tests.
3. **Deterministic harness:** reset, sandbox, test runner, artifact writer, and policy checks.
4. **Agent graph:** Planner, Investigator, Coder, Reviewer, routing, and human approval.
5. **Initial benchmark:** expand to 12 cases and run the single-agent/multi-agent/reflection comparison.
6. **Memory:** add evidence-gated long-term memory and measure its effect.
7. **Open-source hardening:** documentation, reproducible setup, CI, security review, license, and public demo.

No later phase should begin merely to make the directory tree look complete. Each phase must pass its own verification gate first.

## Current implementation status

Phase 2 contains one Python fixture and one reproducible faulty case:

- `benchmarks/fixtures/order_service/` is the clean `order_service-v1` baseline;
- `benchmarks/cases/order_service_001/repo/` contains an exact-threshold bulk
  discount bug and Agent-visible public tests;
- `benchmarks/cases/order_service_001/evaluator/` contains tests and reference
  material that must remain outside the Agent workspace;
- `benchmarks/cases/order_service_001/manifest.yaml` records the issue, commands,
  path policy, limits, and expected behavior.

The fixture gate has been exercised locally: the clean baseline passes, the
faulty case fails only the intended threshold checks, and the reference repair
passes both public and evaluator tests. See [`benchmarks/README.md`](benchmarks/README.md)
for the exact commands and current boundary.

Phase 3 adds a deterministic harness with no model dependency:

- strict YAML manifest parsing into typed dataclass contracts;
- `prepare_case()` to create a fresh run directory and copy only the Agent-visible
  repository into its disposable workspace;
- `evaluate()` to enforce path policy before execution, run fixed public and
  evaluator commands, re-check policy, and assign a typed status;
- a Docker runner with no network, a read-only root filesystem, read-only bind
  mounts, a non-root user, dropped Linux capabilities, `no-new-privileges`, and
  CPU, memory, PID, temporary-storage, and wall-time limits;
- SHA-256 change detection, allowlist/denylist checks, unified diff generation,
  secret redaction, atomic JSON/text artifact writes, and JSONL trace events;
- duplicate-evaluation prevention so a completed run cannot silently execute
  its side effects twice.

The Docker image is pinned by digest. The harness never falls back to an
unrestricted host subprocess when Docker is unavailable and uses `--pull never`
to prevent implicit network access. Missing Docker infrastructure is recorded as
`infrastructure_error`.

### Install and test the harness

```powershell
python -m pip install -e .
python -m unittest discover -s tests -v
```

Default tests use scripted process results and require neither Docker nor a live
model/API key.

### Run the faulty case

With Docker Desktop running:

```powershell
python -m repomedic run-case benchmarks/cases/order_service_001
```

The expected status is `tests_failed`: the harness ran correctly and preserved
the intentionally faulty input. Evidence is written below
`runs/order_service_001/<run-id>/`.

### Reproduce the harness gate

The maintainer-only gate applies the known one-line repair inside the prepared
workspace and then runs the same Docker evaluation path:

```powershell
python -m scripts.validate_phase3
```

The expected status is `verified`. This script is validation infrastructure; it
is not available to a repair Agent and does not change the source benchmark.

## Phase 4 Agent graph

The first LangGraph workflow now contains distinct Planner, Investigator, Coder,
and Reviewer contracts. Python routing owns tool and repair budgets, validates
every model response with Pydantic, runs only bounded repository operations, and
does not expose evaluator-only files or an unrestricted shell to the model.

The Coder proposes exact text replacements but cannot apply them directly. The
graph pauses first, saves its state to `checkpoint.sqlite`, and accepts an
explicit `approve`, `revise`, or `reject` decision. Approved changes are applied
idempotently inside the disposable workspace. Public tests inform revision;
evaluator-only tests run only after the Reviewer returns `pass`.

Default tests use `ScriptedModel` and make no API calls. To start a live run,
set `OPENAI_API_KEY` in the current environment and explicitly choose a model:

```powershell
repomedic run-agent benchmarks/cases/order_service_001 --model MODEL_ID
```

The command prints the run directory when it pauses. Inspect or resume it with:

```powershell
repomedic agent-status runs/order_service_001/RUN_ID
repomedic decide-agent runs/order_service_001/RUN_ID approve
```

For the optional local control panel, run:

```powershell
repomedic serve-agent runs/order_service_001/RUN_ID
```

Then open `http://127.0.0.1:8765`. The server refuses non-loopback bindings,
escapes model-controlled content, and protects approval posts with a per-process
token. The CLI remains the source-of-truth interface.

With Docker Desktop running, reproduce the complete scripted-model graph gate:

```powershell
python -m scripts.validate_phase4
```

The expected status is `verified`. This gate uses fixed model responses, pauses
at the real approval node, and spends no API credits.

## Four-case benchmark checkpoint

The first development suite is declared in
`benchmarks/suites/order_service_4.yaml`. Validate its clean fixture, four faulty
or incomplete inputs, and four maintainer reference repairs without an API:

```powershell
python -m scripts.validate_suite benchmarks/suites/order_service_4.yaml
```

Start one live attempt per case with a frozen model and reasoning setting:

```powershell
repomedic start-benchmark benchmarks/suites/order_service_4.yaml `
  --model gpt-5.6-terra --reasoning-effort low
```

Each case stops at its own approval checkpoint. Review and approve the printed
run directories with `serve-agent` or `decide-agent`; no batch auto-approval is
provided. Generate the current aggregate after decisions complete:

```powershell
repomedic benchmark-status runs/benchmarks/order_service_4/BENCHMARK_RUN_ID
```

The generated summary includes every declared case, including failures and
pending approvals. It records statuses, token counts, model/tool calls, and
latency from run artifacts; it does not insert hand-written success numbers.

## Eight-case benchmark checkpoint

Cases 005-008 add a `task_scheduler` fixture with local-logic,
cross-module-contract, edge/regression, and small-feature tasks. The eight-case
suite keeps the original four-case suite immutable so its recorded result stays
reproducible.

Validate all eight inputs and reference repairs without an API call:

```powershell
python -m scripts.validate_suite benchmarks/suites/initial_8.yaml
```

Start the next measured run with the same protocol used for the first four:

```powershell
repomedic start-benchmark benchmarks/suites/initial_8.yaml `
  --model gpt-5.6-terra --reasoning-effort low
```

The preserved v1 staged result verified 6/8 cases and is documented in
[`benchmarks/results/initial_8_20260905.md`](benchmarks/results/initial_8_20260905.md).
The preserved smoke results use `agent-graph-v2` / `multi-agent-review-v2` and
must remain separate from later protocols. The graph prevents
Reviewer revisions outside the manifest edit allowlist, records failed model
and tool attempts, validates that tool budgets cover the declared direct repair
path, and aligns hidden assertions with the manifest contract. Protocol changes
must not be merged into one success rate.

## Twelve-case benchmark dataset

Cases 009-012 add the `document_pipeline` fixture and complete the planned
three-fixture, four-category development matrix. The earlier four- and
eight-case suites remain immutable checkpoints.

Validate the complete dataset without an API call:

```powershell
python -m scripts.validate_suite benchmarks/suites/initial_12.yaml
```

Start a live run with the same frozen model settings used by earlier stages:

```powershell
repomedic start-benchmark benchmarks/suites/initial_12.yaml `
  --model gpt-5.6-terra --reasoning-effort low `
  --agent-mode multi_agent_review
```

## Phase 5 Agent configuration ablation

The current `agent-graph-v5` / `agent-config-ablation-v2` protocol exposes three
memory-free modes. A fourth arm adds memory to the full review graph. All four
hold the fixture, schemas, tool restrictions, approval gate, Docker tests, and
artifact accounting constant:

- `single_agent`: one `repairer` model identity performs planning,
  investigation decisions, evidence synthesis, coding, and self-review across
  successive structured calls;
- `multi_agent_no_review`: Planner, Investigator, and Coder are separate, but a
  public-test failure terminates without a Reviewer call or reflection;
- `multi_agent_review`: the full Planner, Investigator, Coder, and Reviewer
  graph can revise or replan within the recorded limits.

The original preflight targets all 12 development cases. To limit API and
approval cost, the first configuration ablation uses the frozen, stratified
`preflight_6` subset instead. It covers two cases per fixture and all four task
categories, but it must not be reported as a complete `initial_12` result or as
holdout evidence. Start the three matched memory-free runs with three
independent attempts per case. This creates 18 case-runs per configuration and
54 across these three arms; each proposal that reaches the approval gate must
be inspected and decided separately:

```powershell
repomedic start-benchmark benchmarks/suites/preflight_6.yaml `
  --model gpt-5.6-terra --reasoning-effort low `
  --agent-mode single_agent --attempts 3 --run-id preflight6-single-v2
repomedic start-benchmark benchmarks/suites/preflight_6.yaml `
  --model gpt-5.6-terra --reasoning-effort low `
  --agent-mode multi_agent_no_review --attempts 3 --run-id preflight6-no-review-v2
repomedic start-benchmark benchmarks/suites/preflight_6.yaml `
  --model gpt-5.6-terra --reasoning-effort low `
  --agent-mode multi_agent_review --attempts 3 --run-id preflight6-review-v2
```

If a long start command is interrupted, rerun that same command with `--resume`.
RepoMedic validates the frozen configuration, skips recorded attempts, recovers
an unrecorded checkpoint at the next expected case-attempt, and continues. It
does not silently restart completed model work.

After every case reaches a terminal status, generate the matched comparison:

```powershell
repomedic compare-configurations `
  runs/benchmarks/preflight_6/preflight6-single-v2 `
  runs/benchmarks/preflight_6/preflight6-no-review-v2 `
  runs/benchmarks/preflight_6/preflight6-review-v2 `
  --output-dir runs/configuration-ablation/preflight-6-v2
```

The comparator rejects incomplete runs, fewer than three attempts per case,
enabled memory, wrong modes, different ordered case-attempt pairs, or mismatched
suite, model, reasoning, prompt, and protocol. `pass@1` is the mean single-sample
success estimate across the three attempts; `pass@3` is the estimated chance
that at least one of three samples succeeds. Old staged v1/v2 runs cannot be
merged into this result.

## Phase 6 episodic memory

RepoMedic now has an opt-in SQLite episodic memory store. A memory entry can be
created only from a run whose artifacts prove all of the following: the human
approved the patch, public and evaluator tests passed, the path policy passed,
and the final status is `verified`. Stored lessons contain sanitized issue,
root-cause, repair-summary, changed-path, and evidence-path fields plus fixture,
case, and run provenance. Evaluator source and test output are not stored.

Memory retrieval is deterministic lexical ranking with a same-fixture bonus.
The current case is excluded. At most `--memory-limit` lessons are considered,
and only lessons that fit the total `--memory-context-budget-chars` budget are
supplied to the Planner. Long lesson fields and path lists are bounded before
packing. The default budget is 2400 characters. Lessons remain untrusted
hypotheses; repository evidence must still be gathered for the current run.
Python/LangGraph continues to own routing, budgets, approval, testing, and the
write gate.

Import a prior verified Agent run and inspect retrieval without calling a model:

```powershell
repomedic memory-learn runs/PREVIOUS_CASE/RUN_ID `
  --memory-db runs/memory/episodic.sqlite
repomedic memory-search "boundary comparison failure" `
  --memory-db runs/memory/episodic.sqlite --fixture order_service `
  --context-budget-chars 2400
```

Enable memory for one run or benchmark with an explicit database:

```powershell
repomedic run-agent benchmarks/cases/order_service_002 `
  --model gpt-5.6-terra --reasoning-effort low `
  --agent-mode multi_agent_review `
  --memory-db runs/memory/episodic.sqlite `
  --memory-context-budget-chars 2400
```

Every run writes `memory.json`, including retrieved entry provenance, canonical
compact-JSON character count, the budget, the initial memory-corpus entry count
and content hash, the write policy, and any entry written after successful
verification. Individual memory-enabled runs learn by default; benchmark runs
set the write policy to read-only. The database path, corpus identity, budget,
retrieval IDs, and write policy are frozen in `config.json`, so approval through
either CLI or web UI resumes the same behavior. Runs created under an older
prompt version are rejected on resume rather than silently changing behavior.

The memory uplift gate requires paired, complete benchmark runs. Seed and freeze
the memory database from prior verified runs, then run the same suite without
memory and with memory using identical model and reasoning settings. Benchmark
runs retrieve from the frozen database but do not write new entries, preventing
earlier cases or attempts from contaminating later ones. Do not run
`memory-learn` against the frozen database while the treatment is in progress.
A comparison on the selected six development cases is development-set evidence
only; it does not establish performance on all 12 cases or holdout
generalization:

```powershell
repomedic start-benchmark benchmarks/suites/preflight_6.yaml `
  --model gpt-5.6-terra --reasoning-effort low `
  --agent-mode multi_agent_review --attempts 3 `
  --run-id preflight6-review-memory-v2 `
  --memory-db runs/memory/preflight6-frozen.sqlite
```

After reviewing and deciding every case in both runs, generate the two benchmark
summaries and the matched comparison:

```powershell
repomedic benchmark-status runs/benchmarks/preflight_6/preflight6-review-v2
repomedic benchmark-status runs/benchmarks/preflight_6/preflight6-review-memory-v2
repomedic compare-memory runs/benchmarks/preflight_6/preflight6-review-v2 `
  runs/benchmarks/preflight_6/preflight6-review-memory-v2 `
  --output-dir runs/memory-ablation/preflight-6-v2
```

`compare-memory` refuses incomplete or unmatched runs, a baseline with memory
enabled, a treatment with memory disabled, missing provenance, changed corpus
snapshots, invalid context-budget accounting, a mutable treatment corpus,
same-case memory, or a treatment that retrieved no entries. It derives pass@1,
pass@3, verified-rate, and usage deltas from saved artifacts.

After all four 18-run arms are terminal, generate the unified reduced preflight
comparison (72 case-runs total):

```powershell
repomedic compare-preflight `
  runs/benchmarks/preflight_6/preflight6-single-v2 `
  runs/benchmarks/preflight_6/preflight6-no-review-v2 `
  runs/benchmarks/preflight_6/preflight6-review-v2 `
  runs/benchmarks/preflight_6/preflight6-review-memory-v2 `
  --output-dir runs/preflight-comparison/preflight-6-v2
```

The command first applies the strict three-arm configuration checks, then the
frozen-corpus memory checks, and finally writes one four-row report plus both
component reports. The completed six-case result is documented in
[`benchmarks/results/preflight_6_ablation_20260905.md`](benchmarks/results/preflight_6_ablation_20260905.md).
It found no measurable Reviewer or memory success-rate uplift on this subset.

## License

RepoMedic, including its synthetic benchmark fixtures and tests, is released
under the [MIT License](LICENSE).
