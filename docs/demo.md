# Three-minute deterministic demo

This demo exercises the real Planner -> Investigator -> Coder -> approval ->
Docker tests -> Reviewer path with fixed model responses. It is designed to
show RepoMedic's orchestration, safety boundary, checkpoint, patch, tests, and
evidence artifacts without an API key or model charges. It is not evidence of
live-model repair quality.

## Requirements

- Python 3.11 or 3.12
- Docker Desktop (or a compatible Docker daemon) running
- the repository checkout

The pinned Python container image must already be present locally because the
sandbox uses `--pull never` and disables container networking. The image is
normally present after running the fixture or harness gates.

## Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e .
.\.venv\Scripts\python -m scripts.demo
```

## POSIX shell

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m scripts.demo
```

The command prints a one-line patch and pauses. Inspect it, then type
`approve`. A successful run ends with output shaped like:

```text
model=scripted (no API key or model credits)
status=awaiting_approval
run_dir=.../runs/demo/order_service_001/<run-id>
proposal_diff_begin
...
proposal_diff_end
decision=approve
status=verified
evidence=.../final-report.md
```

For an unattended maintainer gate that still prints the diff before recording
the scripted decision:

```powershell
python -m scripts.validate_phase4
```

## What to inspect

Open the printed run directory. The key evidence is:

- `proposal.diff`: the unapproved proposal shown at the checkpoint;
- `checkpoint.sqlite`: persisted graph state used across the approval pause;
- `test-results.json`: real public and evaluator Docker results;
- `patch.diff`: the approved final patch;
- `trace.jsonl`: ordered orchestration and policy events;
- `final-report.md`: terminal status and test summary.

The source case under `benchmarks/cases/order_service_001/repo/` is never
modified. All edits occur in the disposable run workspace.

## Troubleshooting

- `infrastructure_error` or a Docker pipe/socket permission error means the
  daemon is unavailable to the current process; start Docker Desktop and retry.
- A missing-image error is intentional fail-closed behavior. Pull the exact
  digest recorded in `repomedic/sandbox.py` before rerunning.
- The interactive command rejects anything other than `approve`; the resulting
  incomplete run remains below `runs/demo/` for inspection.
