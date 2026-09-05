# Document pipeline fixture

This standard-library project loads a small plain-text document, normalizes its
content, and exports Markdown through a command-line entry point.

Run the tests:

```powershell
python -m unittest discover -s tests -v
```

Convert a document:

```powershell
python -m document_pipeline.cli input.txt output.md
```
