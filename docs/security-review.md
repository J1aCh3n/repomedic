# Security review: 2026-09-05

## Scope and threat model

This review covers RepoMedic's local single-user execution path: untrusted
fixture repositories, model output, test output, approval input, run artifacts,
Docker command construction, checkpoint state, and episodic memory. The local
operator, Docker daemon, host account, and installed Python interpreter remain
trusted.

It is not an independent penetration test. Multi-user hosting, hostile local
users, a compromised Docker daemon, malicious dependencies, and protection of
sensitive customer repositories are outside the supported threat model.

## Review method

- Manually traced path resolution, workspace reset, edit application, policy
  checks, artifact writes, Docker arguments, approval HTTP handling, Responses
  API configuration, SQLite memory reads/writes, and CLI resume behavior.
- Searched for subprocess execution, unsafe YAML loading, dynamic code
  execution, environment access, link handling, network configuration, secret
  handling, and local HTTP bindings.
- Added regression tests for every implementation finding below.
- Ran all deterministic tests and the real Docker Phase 3 and Phase 4 gates.
- Installed the project into an isolated virtual environment and scanned its
  installed dependencies with `pip-audit 2.10.1`.

## Findings addressed

### Secret redaction gaps

The original text redactor did not reliably match prefixed names such as
`OPENAI_API_KEY`, JSON values selected by sensitive keys, or current `sk-...`
provider-token forms. Artifact sanitization now redacts those forms recursively
while preserving non-secret token-usage metrics such as `input_tokens`.
Approval-page exception text is also redacted before HTML escaping.

### Docker argument and mount validation

A custom image value beginning with `-` could be interpreted by the Docker CLI
as an option instead of an image reference. Image references that are empty,
option-shaped, oversized, contain whitespace, or contain a null byte are now
rejected before command construction. Bind sources must exist as directories
and may not themselves be symlinks or junctions. Workspace reset applies the
same check to the source repository root.

The default image remains digest-pinned. Containers retain no network, a
read-only root filesystem, read-only repository/evaluator mounts, a non-root
user, dropped capabilities, `no-new-privileges`, and CPU, memory, PID, and
timeout limits.

### Episodic-memory integrity

Memory retrieval and corpus snapshots previously trusted the stored
`content_hash` column without recomputing it from the retrieved row. A modified
lesson could therefore retain a stale hash. Reads now validate field types,
path-list JSON, the canonical content hash, and the entry ID derived from
case/run provenance. Search and snapshot operations fail closed on mismatch.
The existing frozen six-entry benchmark corpus remains readable after this
change.

### Local approval page defense in depth

The loopback-only page now adds a restrictive Content Security Policy,
frame-ancestors protection, `X-Frame-Options: DENY`, and a no-referrer policy.
It rejects negative, oversized, malformed, or field-exhaustion request bodies
and uses a constant-time comparison for the per-process CSRF token.

### Dependency advisories

The initial isolated scan reported ten advisories across three installed
packages. One affected a direct runtime dependency,
`langgraph-checkpoint-sqlite 3.0.3`; the others affected the temporary
environment's old pip and setuptools versions.

RepoMedic now requires `langgraph-checkpoint-sqlite 3.1.1` and builds with
`setuptools 84.0.0`. CI upgrades to `pip 26.2.1` and `setuptools 84.0.0` before
installation. A repeated isolated scan reported no known vulnerabilities in
the installed non-editable distributions. The editable RepoMedic package
itself is source-reviewed and skipped by `pip-audit` as an unpublished local
distribution.

## Validation evidence

- `python -m unittest discover -s tests -v`: 79/79 passed.
- `python -m pip check`: no broken requirements.
- Isolated environment with upgraded dependencies: 79/79 passed.
- `python -m pip_audit --local --skip-editable`: no known vulnerabilities.
- `python -m scripts.validate_phase3`: `verified` using real Docker.
- `python -m scripts.validate_phase4`: `verified` using real Docker and a
  scripted model; no API credits were used.
- `memory-search` successfully validated and queried the existing frozen
  six-entry corpus.

The GitHub workflow reproduces deterministic tests, compilation, CLI smoke,
package building, and dependency auditing without model credentials. It has not
run on GitHub until the repository is pushed, so local validation is not a
claim that hosted CI is already green.

## Residual risks

- Redaction recognizes common credential forms but cannot guarantee removal of
  arbitrary secrets embedded in unstructured text.
- Run directories and SQLite files are not signed, encrypted, or protected
  against a malicious local user. Memory validation detects inconsistent rows;
  it is not an authenticity mechanism.
- The approval UI has no user authentication and is suitable only for a trusted
  single-user loopback environment.
- Docker isolation depends on the host daemon and kernel. RepoMedic is not a
  replacement for a hardened disposable VM when executing genuinely hostile
  code.
- Public evaluator tests and reference patches are development/reproducibility
  material, not confidential holdouts.
- Vulnerability databases are incomplete and change over time. Dependabot and
  CI auditing provide ongoing signals, not proof that dependencies are safe.
