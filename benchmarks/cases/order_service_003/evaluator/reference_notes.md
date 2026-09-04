# Maintainer reference

The faulty implementation subtracts inventory before checking availability, so
an exception leaves mutated state behind. The reference repair restores the
check-before-mutation ordering from the clean fixture.

The evaluator checks repeated failures, the service/storage interaction, and a
successful exact-stock reservation. Equivalent behavior-preserving repairs are
acceptable; exact patch equality is not part of scoring.
