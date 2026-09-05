# Maintainer reference

Rescheduling coordinates three responsibilities: Scheduler validates and builds
an immutable replacement, the conflict policy excludes the current task, and
storage replaces only an existing record. Validation finishes before storage is
mutated, so rejected reschedules preserve the original task.

The evaluator checks self-exclusion, conflicts, past times, unknown IDs, and
failure side effects. Equivalent behavior-preserving repairs are acceptable;
exact patch equality is not part of scoring.
