# Local release checks

- Install from a clean Python 3.11/3.12 environment.
- Run deterministic tests, compilation, CLI help, package build and dependency audit.
- Run `scripts.validate_tool_loop` and the twelve-case `scripts.validate_suite`
  against real Docker; preserve failure artifacts and the generated summaries.
- Check source fixtures and evaluator content remain unchanged.
- Check no old workflow modules/commands remain referenced by active code/docs.
- Document token overshoot, unavailable usage, disk quota absence, content-only
  patches and approval-only recovery. Do not claim hostile-code containment.
- State explicitly whether live model eval or hosted CI was actually run.
- Keep historical workflow evidence tied to its old commit.
- Create a scoped local commit after relevant checks pass; never push implicitly.
