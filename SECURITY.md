# Security policy

RepoMedic is experimental, local, single-user software. The operator, Python
runtime and Docker daemon are trusted. It has not been independently audited
and is not suitable for hostile code or sensitive repositories as a production
security boundary.

## Boundaries

- Repair models never receive a host shell. Shell commands run only in Docker
  with no network, read-only root filesystem, non-root UID, dropped capabilities,
  no-new-privileges, CPU/memory/PID limits, timeout, and a digest-pinned image.
- Workspace and scratch are the only writable bind mounts. Agent operations
  never mount evaluator files, the source repository, Git or Docker sockets.
- Copies exclude `.git`, `.env*` and evaluator entries. Scan/read/edit operations
  reject unsafe paths, links, junctions, hard-linked files and special entries.
- Scans check entry and file size bounds before chunked reads. Command output
  is streamed with a combined cap; overflow and timeout terminate the command.
- Model-declared scope is an audit/check mechanism. `bash` can write outside
  scope inside workspace; submission fails until those changes are resolved.
- Human review occurs after edits and public tests, before a `fix` patch export.
  Approval is bound to diff and workspace hashes. Source code is never patched.
- Eval skips review, never exports, and grades a separate copy with restored
  original tests. Evaluator results do not return to the model.
- Logs redact common credential forms. Local review diffs and approved patches
  retain exact contents and may contain sensitive text. Model tool responses
  also retain workspace text; log redaction does not sanitize model context.
  Public traces omit private reasoning; encrypted
  reasoning is retained only in private local checkpoint history.

## Limitations

- Writable bind mounts have no disk quota. A command can fill the disk before
  the post-command scan. Scratch is not scanned as part of a patch.
- Scans observe file states between tools, not every syscall. Protected files
  created and removed within one command are not necessarily observable.
- There is no guarantee of precise recovery after a model/tool/host crash.
  Only human-review interrupts support resume; start a new run after other crashes.
- Filesystem checks assume no hostile concurrent host process. They are not a
  race-proof filesystem capability system.
- Only text content patches are supported; permission/mode changes are not
  included. Log redaction can miss arbitrary secrets or flag harmless text.
- SQLite and run artifacts are local, unsigned and unencrypted. Their integrity
  against a hostile host operator is outside the threat model.
- Original tests and public evaluator files support reproducibility, not secret
  holdouts or complete protection against adversarial test manipulation.
- Dependencies and container vulnerabilities may change. CI audits dependencies
  but cannot prove safety. No weaker host-execution fallback is provided.

Use GitHub private vulnerability reporting where available. Otherwise request a
private contact channel without publishing credentials or exploit details.
Include the affected revision, platform, reproduction conditions and impact.
