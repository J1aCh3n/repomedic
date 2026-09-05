from pathlib import Path

from document_pipeline.exporter import render_markdown, write_output
from document_pipeline.loader import load_document
from document_pipeline.transform import transform_document


def process(
    source: Path,
    destination: Path,
    *,
    uppercase_title: bool = False,
) -> str:
    document = load_document(source)
    transformed = transform_document(document, uppercase_title=uppercase_title)
    rendered = render_markdown(transformed)
    write_output(destination, rendered)
    return rendered
