from pathlib import Path

from document_pipeline.models import Document


def load_document(source: Path) -> Document:
    lines = source.read_text(encoding="utf-8").splitlines()
    if not lines or not lines[0].strip():
        raise ValueError("document title must not be empty")
    paragraphs = tuple(line.strip() for line in lines[1:] if line.strip())
    return Document(
        title=lines[0].strip(),
        paragraphs=paragraphs,
        source=source,
    )
