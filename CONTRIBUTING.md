# Contributing

Keep scope, model tool decisions and deterministic harness responsibilities
separate. Preserve evaluator isolation, recorded failures and exact approval
semantics. Read README.md, AGENTS.md and SECURITY.md before changing behavior.

Use Python 3.11 or 3.12 and a project-local virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
python -m unittest discover -s tests -v
python -m compileall -q repomedic tests scripts
python -m repomedic --help
git diff --check
```

Default tests need no API key, network or Docker. Mock provider calls at the
transport boundary. Scripted-model tests establish orchestration correctness,
not repair ability. Do not add live calls to default tests or CI.

For copying, sandbox, graph or grading changes, run these explicit Docker gates:

```powershell
python -m scripts.validate_tool_loop
python -m scripts.validate_suite benchmarks/suites/initial_12.yaml
```

The pinned image must already be installed; repair commands never pull images.
Gates save evidence under ignored `runs/` directories. Do not commit credentials,
generated workspaces, SQLite files or private holdouts. Historical reports refer
to the pre-refactor commit and must not be relabeled as tool-loop measurements.

Add the lowest sufficient regression test. Do not scaffold future multi-agent
subgraphs, journals, web UI, memory or provider abstractions without a current
authorized requirement. Completed, verified changes get one scoped local commit;
pushing or publishing requires an explicit request.
