# Contributing to RepoMedic

RepoMedic is an experimental learning project. Contributions should preserve
its deterministic safety boundaries and the distinction between infrastructure
evidence and live-model performance.

## Development setup

Python 3.11 or 3.12 is supported. From the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
python -m unittest discover -s tests -v
```

On POSIX shells, activate the environment with `source .venv/bin/activate`.
The default test suite is deterministic and must not require Docker, a network
connection, or an API key.

## Change requirements

- Keep orchestration, budgets, approval, path policy, and test execution in
  deterministic Python code.
- Treat issues, repository contents, model output, and tool output as untrusted.
- Add the lowest-level regression test that proves a behavior change.
- Do not expose evaluator-only files to an Agent or alter tests to make a faulty
  implementation pass.
- Do not add live-model calls to the default test suite or CI.
- Preserve failed benchmark runs and generate aggregate metrics from artifacts.
- Keep changes scoped; avoid unrelated refactors.

Before submitting a change, run:

```powershell
python -m unittest discover -s tests -v
python -m compileall -q repomedic tests scripts
git diff --check
```

Docker-backed fixture and graph gates are maintainer checks. Run the relevant
gate when changing sandboxing, fixtures, evaluation, or graph behavior:

```powershell
python -m scripts.validate_phase3
python -m scripts.validate_phase4
python -m scripts.validate_suite benchmarks/suites/preflight_6.yaml
```

Never commit API keys, environment files, generated run directories, SQLite
state, or private holdout cases.

## Reporting results

State exactly what was measured: suite, case count, attempts, model, prompt,
protocol, approval method, and known limitations. Public fixtures are
development cases, not secret holdouts. A fixture gate or scripted-model run
does not establish live-model repair quality.
