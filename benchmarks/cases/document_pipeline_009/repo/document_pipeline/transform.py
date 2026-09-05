from document_pipeline.models import Document


def normalize_whitespace(value: str) -> str:
    return " ".join(value.strip().split(" "))


def transform_document(
    document: Document, *, uppercase_title: bool = False
) -> Document:
    title = normalize_whitespace(document.title)
    if uppercase_title:
        title = title.upper()
    return Document(
        title=title,
        paragraphs=tuple(
            normalize_whitespace(paragraph) for paragraph in document.paragraphs
        ),
        source=document.source,
    )
