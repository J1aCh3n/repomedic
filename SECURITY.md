# Security policy

## Project status

RepoMedic is experimental software. It executes untrusted repository code only
through its Docker sandbox, but it has not been independently audited and must
not be used as a production security boundary or on sensitive repositories.
Only the latest commit on the default branch is maintained during the current
`0.1.x` development series.

## Reporting a vulnerability

Use GitHub private vulnerability reporting when it is available. If the
repository does not have private reporting enabled, open a minimal issue asking
for a private contact channel; do not include exploit details, credentials, or
sensitive repository content in a public issue.

Include the affected revision, platform, reproduction preconditions, expected
impact, and the smallest safe reproduction you can provide.

## Security boundaries

RepoMedic is designed around these boundaries:

- Agents receive bounded repository read/edit tools, never a general shell.
- All edits occur in a disposable workspace below the run directory.
- Manifest allowlists and forbidden paths are checked before and after tests.
- Evaluator-only files are mounted separately and are not exposed to Agents.
- Docker runs without network access, as a non-root user, with a read-only root
  filesystem, dropped capabilities, `no-new-privileges`, and resource limits.
- Patch application pauses for explicit human approval.
- Run artifacts redact common secret forms and do not intentionally store full
  environment variables, private reasoning, or evaluator source.
- Episodic-memory writes require verified evidence; retrieved lessons are
  bounded, provenance-bearing, and treated as untrusted input.

## Known limitations

- Docker Desktop or a compatible Docker Engine is required for real fixture
  execution. RepoMedic does not provide a weaker host-execution fallback.
- Redaction is defense in depth, not a guarantee that arbitrary secrets cannot
  appear in model- or process-generated text.
- The local approval UI is intended only for loopback use. It is not an
  authenticated multi-user service.
- SQLite checkpoints and memory databases are local files without application-
  level encryption or tenant isolation.
- Public benchmark evaluator tests and reference patches are reproducibility
  material, not confidential holdouts.
- Dependency and container-image vulnerabilities can change after release;
  Dependabot and the CI `pip-audit` job provide current signals but cannot prove
  the absence of vulnerabilities. Users must apply their own update and
  scanning policy.

Do not place secrets, customer data, proprietary source, or private holdout
material in a RepoMedic run unless the surrounding environment supplies the
additional controls required for that data.
