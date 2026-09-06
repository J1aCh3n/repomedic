# Open-source release checklist

Local verification date: 2026-09-05. Hosted CI verification date: 2026-09-06.

## Completed locally

- MIT license, package metadata, contribution guide, and security policy are
  present.
- All 12 synthetic development cases, their evaluator tests, and maintainer
  reference material are included for reproducibility. They are public
  development data, not a confidential holdout.
- The deterministic test suite passes on the release worktree.
- The wheel and source distribution build with the pinned build backend.
- A fresh virtual environment can install the wheel and invoke the `repomedic`
  console entry point without the source checkout on `sys.path`.
- The zero-API public demo reaches the real approval checkpoint, displays its
  diff, and finishes `verified` through Docker after approval.
- The dependency audit and manual security review are documented in
  [`security-review.md`](security-review.md).
- Generated runs, virtual environments, SQLite databases, and environment
  files are ignored rather than included in the release.

## Hosted automation

- The repository is published at
  [`J1aCh3n/repomedic`](https://github.com/J1aCh3n/repomedic).
- Hosted [`CI #1`](https://github.com/J1aCh3n/repomedic/actions/runs/34038257997)
  passed on commit `69f2d9d`: all four Windows/Linux and Python 3.11/3.12 test
  jobs, distribution building, and dependency auditing completed successfully.
- `.github/workflows/release.yml` provides opt-in delivery: pushing a tag that
  exactly matches `v<project.version>` reruns deterministic checks, builds the
  wheel and source distribution, and creates a GitHub Release using the
  repository-scoped `GITHUB_TOKEN`.
- No PyPI publishing is configured. No release has been published by this
  workflow yet; creating and pushing a version tag remains an explicit
  maintainer action.

## Claim boundary

The project is an experimental learning implementation. Its completed
six-case, four-configuration result is development-set evidence. It does not
establish production readiness, benchmark superiority, full 12-case ablation
performance, or holdout generalization.
