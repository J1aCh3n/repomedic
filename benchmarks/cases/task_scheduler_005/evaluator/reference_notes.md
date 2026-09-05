# Maintainer reference

Half-open scheduling intervals allow one task to begin exactly when another
ends. Conflict checks therefore require strict comparisons on both interval
boundaries. The evaluator checks adjacency in the reverse insertion order and
confirms that a positive overlap still fails.

Equivalent behavior-preserving repairs are acceptable; exact patch equality is
not part of scoring.
