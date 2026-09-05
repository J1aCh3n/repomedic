# Maintainer reference

`Document.source` is a `Path` contract shared by Loader, the model, and
Exporter. Preserving the Loader input satisfies that contract and lets Exporter
select `.name` without exposing parent directories.

Equivalent behavior-preserving repairs are acceptable; exact patch equality is
not part of scoring.
