# RepoMedic memory design: production review notes

## Purpose

This document summarizes the current RepoMedic memory design and the open
questions for production use. It distinguishes implemented behavior from
proposed hardening. It does not claim that memory improves repair success; that
requires the paired memory/no-memory benchmark experiment described below.

## Current design in one sentence

RepoMedic separates per-run working state from cross-run episodic memory:
LangGraph checkpoint state resumes one repair run, while an opt-in SQLite store
keeps compact, provenance-bearing lessons from verified repairs for later runs.

## Two memory scopes

| Scope | Current implementation | Lifecycle | Contents |
| --- | --- | --- | --- |
| Working state | LangGraph `AgentState` plus a per-run `checkpoint.sqlite` | One run, including approval pause/resume | Issue, plan, investigation, proposal, approval, test/review state, budgets, usage |
| Episodic memory | Optional SQLite database, e.g. `runs/memory/episodic.sqlite` | Shared across runs that select the same database | Verified lesson summary and provenance only |

The working state is deliberately not inherited by a later run. It contains
unfinished reasoning, rejected attempts, and task-specific state that may be
stale or misleading for a different bug. A completed run still keeps its
artifacts for audit, but they are not automatically placed into a later model
prompt.

## How the current SQLite memory is enabled

Memory is opt-in for a run or benchmark:

```powershell
repomedic run-agent CASE_DIR --model gpt-5.6-terra `
  --memory-db runs/memory/episodic.sqlite `
  --memory-context-budget-chars 2400

repomedic start-benchmark SUITE_PATH --model gpt-5.6-terra `
  --memory-db runs/memory/episodic.sqlite `
  --memory-context-budget-chars 2400
```

If the file does not exist, RepoMedic creates the SQLite database and schema.
If it exists, subsequent runs reuse it. The selected database path is frozen in
the run's `config.json`; approve/resume through CLI or the local UI reuses the
same setting. A run without `--memory-db` neither reads nor writes episodic
memory.

The explicit flag is useful while running a measured memory/no-memory ablation.
Each run also records a logical corpus snapshot: the entry count and a stable
hash over sorted entry/content hashes. This identifies the memory contents at
retrieval time even though the SQLite file may receive later verified writes.
For ordinary production use, explicit flags are probably too manual; see the
proposed repository-level configuration below.

## What a memory entry contains

A row in `memory_entries` contains:

```text
entry_id
fixture_id, fixture_version
case_id, run_id
issue
root_cause
repair_summary
changed_paths
evidence_paths
created_at
content_hash
```

This is a compact repair lesson, not a code snapshot. It intentionally does not
store complete source files, full `patch.diff`, line excerpts, evaluator source,
or evaluator test output. Full diffs and investigation detail remain in the
original run artifacts.

The current path-level representation avoids stale line numbers and limits what
is copied into later model context. It also means a future Agent still needs to
read the current repository before acting.

## Write gate: who decides that memory is written?

The model does not have a memory-write tool and cannot decide to persist a
lesson. The human selects whether memory is enabled by supplying the database
configuration. After that, deterministic orchestration decides whether a write
is allowed.

An entry is written only after all of the following are true:

```text
memory database enabled
AND human approval action was approve
AND final status is verified
AND public tests passed
AND evaluator tests passed
AND path policy is compliant
AND at least one allowed path changed
```

Failed runs, rejected proposals, model guesses, unsuccessful patches, and
unapproved changes do not become episodic memory. Re-importing the same
`(case_id, run_id)` is idempotent; conflicting content for that identity is
rejected.

Previously completed verified runs can be imported explicitly:

```powershell
repomedic memory-learn RUN_DIR `
  --memory-db runs/memory/episodic.sqlite
```

## Read path: how a new Agent uses memory

At the start of a new run, before the Planner generates its plan:

```text
current manifest issue
  -> search SQLite memory
  -> select a small ranked set of prior lessons
  -> pass them as memory_lessons to Planner only
  -> Planner proposes an investigation plan
  -> Investigator reads the current repository and verifies the hypothesis
```

The Planner receives provenance, issue, root cause, repair summary, changed
paths, and evidence paths. The Investigator, Coder, and Reviewer do not receive
raw episodic memory directly. The Planner prompt explicitly treats prior lessons
as untrusted hypotheses, not as proof or instructions.

Current retrieval is deterministic lexical ranking:

```text
search text = issue + root_cause + repair_summary + changed_paths + evidence_paths
score       = shared tokens with current issue + 3 when fixture IDs match
```

Common stop words are ignored. The current case ID is excluded, matches are
sorted deterministically, and the default retrieval limit is three entries.
Text fields are truncated to 300 characters, each path to 120 characters, and
each path list to three items before ranked entries are greedily packed into the
configured total character budget. This is not vector search or semantic
embedding retrieval.

## Important clarification about context growth

Enabling one SQLite database for every run does not inject the entire database
into every prompt. The database is external storage. The Planner receives only
ranked entries that satisfy both the count limit and a total compact-JSON
character budget, 2400 characters by default. `memory.json` records the exact
injected payload and its character count.

This is a deterministic proxy rather than a tokenizer-specific token budget.
The database also has no current retention, deduplication across different
runs, or consolidation policy.

## Recommended production policy (not yet implemented)

For normal repair operations, prefer a repository-scoped default rather than
requiring an operator to type `--memory-db` every time:

```toml
# Example proposal; not an implemented configuration format.
[memory]
enabled = true
database = ".repomedic/episodic.sqlite"
retrieval_limit = 3
context_budget_chars = 2400
```

The benchmark runner should retain explicit memory enable/disable switches so
control and treatment experiments remain reproducible.

Recommended hardening:

1. Keep repository databases isolated by default; do not mix unrelated repos.
2. Deduplicate or consolidate near-identical verified lessons by fixture, root
   cause, and changed paths.
3. Add retention, archival, and explicit deletion/forgetting policy.
4. Track retrieval usefulness: retrieved entry IDs, whether the Planner used
   them, verified outcome, latency, tokens, and cost.
5. Keep evaluator-only data, secrets, customer data, and full source out of the
   store; add stronger redaction and access controls before multi-user use.
6. Use a separate human-confirmed incident/rule store for severe but unresolved
   findings. Do not mix them with verified repair lessons.

These are Phase 7 or production concerns, not prerequisites for the first
memory ablation. Repository-default activation and semantic retrieval would add
new behavior and confounds before the lexical baseline has been measured.

## Why not inherit all short-term state between runs?

Blindly carrying state from one bug to another risks anchoring the model on an
incorrect root cause, a rejected patch, or a repository version that has
changed. The production design should promote only compact, verified lessons
across runs. The new run remains responsible for investigating its own current
workspace and test evidence.

## Measurement requirement

Memory usefulness must be measured, not assumed. RepoMedic includes a
`compare-memory` command that only accepts paired benchmark runs with the same
suite, ordered case-attempt pairs, attempt count, model, reasoning effort,
prompt version, and protocol.
The baseline must have memory disabled; the treatment must have memory enabled
and must actually retrieve provenance-bearing entries. The memory corpus is
read-only during a benchmark. The comparator validates this setting, checks
that all treatment runs used the benchmark's initial corpus fingerprint, and
checks that the recorded prompt payload stayed within its character budget. The
comparison derives pass@1, pass@3, verified-rate, and usage deltas from saved
artifacts.

The current 12 cases are a development dataset. A paired result on them measures
development-set behavior only. A later generalization claim needs new holdout
cases that were not used to create memory entries or tune retrieval. The memory
seed manifest, corpus fingerprint, target cases, model, prompt, and review
procedure should be frozen before either arm begins.

Example:

```powershell
repomedic compare-memory BASELINE_RUN MEMORY_RUN `
  --output-dir runs/memory-ablation/EXPERIMENT_ID
```

## Questions for production review

1. Should repository memory be enabled by default, or should each operator/run
   explicitly opt in?
2. What is the desired isolation boundary: repository, tenant, organization, or
   team? Who may read, export, or delete entries?
3. Should the current 2400-character proxy become a model-token budget, and
   should retrieval be progressive instead of injecting all details at planning
   time?
4. What merge, archival, retention, and deletion policy prevents unbounded
   growth while preserving auditability?
5. Should the current lexical retrieval be replaced or supplemented with
   embeddings, metadata filtering, or code-aware retrieval? How will relevance
   and regressions be evaluated?
6. How should severe unresolved findings be recorded, reviewed, escalated, and
   kept separate from verified repair lessons?
7. What redaction, encryption, access control, backup, and incident-response
   requirements apply before storing production repository metadata?
8. Which metrics determine that memory is beneficial: verified success rate,
   repair latency, token cost, unsafe-edit rate, or operator approval burden?
