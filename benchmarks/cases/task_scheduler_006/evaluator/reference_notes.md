# Maintainer reference

`parse_start` is the boundary that converts user-facing ISO-8601 values into the
UTC-aware datetime contract consumed by Scheduler and storage. Normalizing at
that boundary preserves the instant while preventing downstream modules from
persisting mixed offsets.

The evaluator uses a half-hour offset, verifies the stored task, and retains the
naive-input rejection. Equivalent behavior-preserving repairs are acceptable;
exact patch equality is not part of scoring.
