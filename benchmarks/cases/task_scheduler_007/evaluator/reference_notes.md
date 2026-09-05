# Maintainer reference

ID allocation is an observable state change. It must occur only after every
validation that can reject the request and immediately before the task is
saved. The evaluator checks independent validation failures and a cross-module
conflict failure before asserting that successful IDs remain gap-free.

Equivalent behavior-preserving repairs are acceptable; exact patch equality is
not part of scoring.
