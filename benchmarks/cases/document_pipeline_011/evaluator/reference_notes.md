# Maintainer reference

The pipeline can finish loading, transformation, and rendering before the first
destination side effect. `write_output` remains the only write on the success
path. The evaluator checks both a missing destination and preservation of exact
existing bytes.

Equivalent behavior-preserving repairs are acceptable; exact patch equality is
not part of scoring.
