# RepoMedic

RepoMedic is an experimental Python repository repair agent built with LangGraph.
The model declares a working scope, chooses its own tools and action order, and
submits a repair. Deterministic Python code owns Docker isolation, file checks,
budgets, checkpoints, public tests, human review, and patch export.

This branch implements the v3 tool-loop design. It replaces the previous role
workflow, memory, ablation tools, benchmark runner, and approval web UI. Previous
experiment reports remain historical evidence; they do not measure this agent.
Saved Qwen development runs used a temporary adapter before native provider
support. They are separate evidence from the offline validation of the current
integration; see [docs/validation-qwen.md](docs/validation-qwen.md).

## Setup

Python 3.11 or 3.12 and Docker Desktop / Docker Engine are required. The Docker
daemon and host operator are trusted. This is not a production security boundary.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
python -m repomedic --help
```

On POSIX shells, activate with `source .venv/bin/activate`. The model integration
uses `langchain-openai`, stateless message history, and one tool call per turn.
The default OpenAI provider uses Responses API and requires `OPENAI_API_KEY`.
Qwen uses Chat Completions and reads `DASHSCOPE_API_KEY`, falling back to
`QWEN_API_KEY`; when both are set, `DASHSCOPE_API_KEY` wins. Both require an
explicit `--model`.
Do not put credentials in the repository copy. LangSmith tracing is disabled in
the repair model call; RepoMedic does not require a tracing service.

The sandbox uses this pinned image, with `--pull never` during execution:

```text
python:3.11-slim@sha256:9534e5a8e315485d4061ed659af0fd78a284c015f9b73661b41d6bab25604534
```

Install the image separately if it is missing. It supplies Python and the
standard library; general dependency installation and arbitrary-language
repositories are outside this implementation.

## Fix a repository copy

```powershell
python -m repomedic fix C:\projects\sample `
  --issue "Describe the repair" --model YOUR_MODEL_ID
```

Use `--issue-file issue.txt` for a longer issue. Public tests default to
`python -m unittest discover -s tests -v`; `--test-command` can set another
Python unittest command. A development case can be used directly:

```powershell
python -m repomedic fix benchmarks/cases/order_service_001 --model YOUR_MODEL_ID
```

For Qwen, set `DASHSCOPE_API_KEY` (or `QWEN_API_KEY`) in the invoking shell
without logging it, then:

```powershell
python -m repomedic fix benchmarks/cases/order_service_001 `
  --provider qwen --model qwen3.7-plus --model-timeout 180
```

Qwen defaults to `https://dashscope-intl.aliyuncs.com/compatible-mode/v1`.
Use `--base-url` for the HTTPS endpoint assigned to your region or workspace;
keys must match that endpoint. The provider never infers a region from a key or
falls back to `OPENAI_API_KEY`. Credentials are not saved in run configuration.

Qwen's forced scope tool call disables thinking; the agent loop uses `auto`
tool choice and enables thinking by default. `--no-thinking` disables agent
thinking too. `--reasoning-effort` is an OpenAI-only option. Both providers
disable parallel tool calls and apply the configured output-token limit.
This integration covers Qwen models supporting non-streaming Chat Completions,
function tools and the `enable_thinking` parameter; other API/model combinations
surface their errors rather than silently changing behavior.

The original repository is never edited. The run directory contains a private
baseline, a writable workspace, and persistent scratch space. `.git`, `.env*`,
and evaluator entries are excluded from copies and protected throughout a run.
Set `--runs-root` outside the source repository; copying a repository into its
own output tree is rejected.

## Graph and tools

```text
START -> scope -> agent <-> tools
                   |
                 submit
                   v
                 check -- failed --> agent
                   |
                   v
                 review (human interrupt)
                   | approve -> export -> END
                   | revise  -> agent
                   | reject  -> END
```

`scope` is a model call given the issue, public test command and file inventory.
It declares exact relative files and a short plan. There is no manifest edit
allowlist. Scope is an auditable proposal, not a sandbox boundary or evidence of
correctness. `update_scope(paths, reason)` adds files and records the reason.

| Tool | Execution | Contract |
| --- | --- | --- |
| `bash(command)` | Docker | Arbitrary shell inside the disposable sandbox |
| `read_file(path, start, end)` | Host | Bounded UTF-8 reads inside workspace, with line numbers |
| `edit_file(path, old, new)` | Host | One exact replacement in an existing scoped file |
| `run_tests()` | Docker | Configured public unittest command, read-only workspace mount |
| `update_scope(paths, reason)` | Python state | Expand exact file scope; protected paths remain denied |
| `submit(summary)` | Python state | Run deterministic checks before human review |

Each shell call starts a fresh container at `/workspace`. Shell cwd, processes
and environment do not persist. Workspace files and `/scratch` do persist;
temporary scripts and reproduction output belong in `/scratch`.

After each tool, the harness records observed file changes and hashes. Protected
paths, links, junctions, hard-linked files and special files terminate the run.
Out-of-scope edits are recorded and cause submission checks to fail until they
are reverted or their scope is declared. Scans observe changes between tool
calls; they do not audit transient modifications inside a single shell command.

`check` runs public tests and compares actual changes with scope. Failures go
back to the agent. A successful submission pauses for human review:

```powershell
python -m repomedic status runs/fixes/CASE/RUN
python -m repomedic decide runs/fixes/CASE/RUN approve
python -m repomedic decide runs/fixes/CASE/RUN revise --feedback "Explain what to change"
python -m repomedic decide runs/fixes/CASE/RUN reject
```

Review includes the diff, summary, scope history and real public test results.
Approval binds the diff hash and workspace content fingerprint. Drift requires
fresh checks and approval. Only an approved `fix` run writes `patch.diff`.
`status`, unchanged approval, and rejection make no model call. Revision resumes
the same recorded model, provider, endpoint, thinking/reasoning configuration and
remaining budgets. Older configurations without a provider field use OpenAI.

Patches support UTF-8 file contents, additions and deletions, including CRLF and
missing final newlines. Binary changes and file mode changes are not exported.
Local review diffs and approved `patch.diff` preserve exact contents, including
code using names such as `token` or `password`. Log redaction does not decide
whether a patch is valid. Trace and observation files remain sanitized.
Tool responses retain original workspace text for the model; redaction applies
to their saved logs, so it does not corrupt code needed for exact replacements.

## Explorer subagent (`--agents explorer`)

The default `--agents single` is the one-agent baseline above, with an unchanged
prompt. `--agents explorer` adds one tool, `delegate_explore(question)`, which runs
a separate read-only explorer agent compiled as its own LangGraph subgraph:

```text
main agent --delegate_explore(question)--> explorer subgraph (own state and context)
                                            read_file, bash (read-only), report
main agent <--checked report (ToolMessage)--+
```

- **Isolated context:** the explorer receives only the issue, the question and the
  file list, never the main conversation. The main agent receives only the report.
- **Read-only by construction:** explorer shell commands mount `/workspace`
  read-only in Docker (`/scratch` stays writable). The harness also compares
  workspace fingerprints before and after each delegation; any change is a
  `policy_violation`. Because it cannot write, a delegation is safe to rerun and
  the subgraph has no checkpoint.
- **Checked handoff:** reports are structured (`answer`, `findings` with path and
  line range, `root_cause_hypothesis`, `confidence`, `open_questions`). The harness
  marks each finding `verified` or `UNVERIFIED` depending on whether the cited file
  and lines exist. This checks locations, not whether the claim is correct.
- **Budgets:** `--explorer-max-tool-calls` (15), `--explorer-max-tokens` (60,000)
  and `--max-delegations` (5) bound each delegation. Explorer tokens and model calls
  are merged into the run totals, so `--max-tokens` still bounds the whole run; a
  delegation never receives more than the run has left. At the tool limit or 75% of
  its token budget the explorer gets one final chance to report what it has.
- **Evidence:** trace events carry `agent` (`main`/`explorer`) and `delegation_id`;
  `delegation_started`/`delegation_finished` record question, budgets, status,
  usage and checked findings. Usage adds `delegations`, `explorer_tokens` and
  `explorer_tool_calls`. The mode is frozen in `config.json` and reused on resume.

Whether to delegate is the main agent's decision. Defaults are initial,
unvalidated values, and no success-rate benefit over `single` has been measured.

## Development evaluation

```powershell
python -m repomedic eval benchmarks/suites/initial_12.yaml --model YOUR_MODEL_ID
```

For Qwen, add `--provider qwen --model qwen3.7-plus` instead of the model
placeholder. For example, `--max-tokens 60000 --model-timeout 180` sets a
per-task cumulative token limit and request timeout, not a suite-wide cost cap.

The twelve existing cases form a development taskset across three Python
fixtures. Their manifests describe tasks and tests; permissions and execution
limits have been removed. They are not confidential holdouts.
The model also receives each manifest's `expected_behavior` specification in
addition to the issue. Results therefore describe this augmented development
protocol, not repairs from issue text alone.

Eval skips human review and never exports a patch. When an agent submits a
checked repair, the grader creates a separate scoring copy, restores the entire
original `tests/` tree, removes agent-added files there, and runs original public
tests plus evaluator tests in Docker. Evaluator data is mounted only for grading
after the agent has terminated. Its results never feed back into the agent.
Evaluator text output is withheld from artifacts because tracebacks can expose
test source; commands, exit codes, timings and output hashes remain recorded.
This grader currently requires public unittest discovery from `tests/`.

Model, budget, policy and infrastructure failures remain failed task rows.
`summary.json` includes every task, its agent outcome, grader outcome, real test
results and usage. Success rate is computed from these rows; there is no pass@3,
memory ablation, or workflow comparison in the new runner.

## Limits, evidence and recovery

Initial, unvalidated defaults: 200 total graph steps, 80 tool calls, 400,000
cumulative input/output tokens, 60 seconds per command and model request,
4,000 output tokens per request, 10,000 filesystem entries and 2 MB per file.
CLI flags set and freeze these values. Graph steps remain cumulative after human
revision; `recursion_limit` also bounds each LangGraph invocation. Token limits
are checked between calls and after usage is returned, so a model request may
overshoot the token threshold; unavailable provider usage cannot be counted.
The step default allows 80 tool calls plus checks and approval/export overhead;
custom step and tool budgets remain independently enforced. Three consecutive
agent replies without a tool call end with `stalled`; a tool call resets the count.
Invalid tool arguments, including absolute scope paths and blank shell commands,
return tool errors so the model can correct them. Actual protected or unsafe
workspace entries still terminate the run as `policy_violation`. Sandbox
configuration faults the model cannot correct, such as an invalid bind mount,
terminate immediately as `infrastructure_error` instead of consuming the budget.

Only standard-library unittest repositories are supported. The scanner does not
automatically exclude `.venv` or data directories: oversized files or more than
the configured number of entries reject the copy. Every directory named
`evaluator` is excluded, including ordinary application directories of that name.
The grader retains a writable scratch mount, while grading workspace and
evaluator mounts are read-only.

Command stdout/stderr are captured concurrently with a combined 1 MiB cap;
output overflow terminates the command. Observations exposed to the model keep
approximately 8,000 characters from the head and tail.
For `bash` and `run_tests`, model responses use plain-text status fields and
separate stdout/stderr sections with real newlines. Each stream is truncated
independently to preserve its head and tail within the combined character limit.
Bounded observations, sanitized commands/results, scope history, transitions, token usage and latency
are saved in `observations/`, `trace.jsonl`, `test-results.json`, `usage.json`,
and `result.json`. Public artifacts omit private reasoning; checkpoints retain
opaque encrypted reasoning required for stateless API history.

Recovery is supported only from human-review interrupts, including a new process
with the same SQLite checkpoint. Arbitrary tool execution interrupted by a crash
must be restarted as a new run; there is no journal or exact-once shell replay.
Writable bind mounts have no disk quota. File and output limits do not prevent
a command from filling the host disk before its next scan. See [SECURITY.md](SECURITY.md).

## Verification without model credits

```powershell
python -m unittest discover -s tests -v
python -m compileall -q repomedic tests scripts
python -m scripts.validate_tool_loop
python -m scripts.validate_suite benchmarks/suites/initial_12.yaml
git diff --check
```

Default tests use scripted models, fake sandboxes and mocked HTTP/API clients;
they require no API key or Docker. Docker gates use only disposable copies and
produce reproducible run artifacts. The tool-loop gate uses an explicitly
recorded scripted approval to test export; it is not a live model or human review
benchmark. The fixture gate checks clean fixtures, injected failures and
maintainer reference repairs. Historical evidence and the old implementation are
available at commit `9154e8ce8ac32a871b339f6be5168f4ec4f3710b`.

The current local validation record is in [docs/validation-v3.md](docs/validation-v3.md).
That record covers initial commit `da7d9d5`; follow-up fixes are recorded in
[docs/validation-v3-fixes.md](docs/validation-v3-fixes.md).
Plain-text command output validation is recorded in
[docs/validation-command-output.md](docs/validation-command-output.md).
Qwen provider validation is recorded in
[docs/validation-qwen.md](docs/validation-qwen.md).
