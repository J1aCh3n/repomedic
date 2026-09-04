# RepoMedic

> Early implementation. The first fixture vertical slice is complete; the
> deterministic harness and Agent graph have not been implemented.

RepoMedic is a proposed LangGraph-based multi-agent coding system that turns a small repository issue into a tested patch and an auditable evidence bundle. It is intended to extend the ideas explored in [`langgraph_file_editor`](../langgraph_file_editor/) from controlled file operations to repository-level diagnosis, implementation, testing, reflection, and human approval.

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

Phase 2 now contains one Python fixture and one reproducible faulty case:

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
