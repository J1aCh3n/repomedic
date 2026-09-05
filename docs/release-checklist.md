# Open-source release checklist

Local verification date: 2026-09-05.

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

## Pending external evidence

- The GitHub Actions workflow is configured for Windows and Linux on Python
  3.11 and 3.12, without API credentials. It cannot be called hosted-CI-green
  until this repository is pushed and the first workflow run passes.

## Claim boundary

The project is an experimental learning implementation. Its completed
six-case, four-configuration result is development-set evidence. It does not
establish production readiness, benchmark superiority, full 12-case ablation
performance, or holdout generalization.
