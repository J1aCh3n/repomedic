# Maintainer reference

Calling `str.split()` without a separator treats every whitespace run as a
single delimiter and removes leading and trailing whitespace. The evaluator
also checks less common whitespace and preserves mixed-case punctuation.

Equivalent behavior-preserving repairs are acceptable; exact patch equality is
not part of scoring.
