from pathlib import Path

from document_pipeline.models import Document


def render_markdown(document: Document) -> str:
    sections = [f"# {document.title}", *document.paragraphs]
    body = "\n\n".join(sections)
    return f"{body}\n\n_Source: {document.source.name}_\n"


def write_output(destination: Path, content: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content, encoding="utf-8")
