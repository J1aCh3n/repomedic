from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Document:
    title: str
    paragraphs: tuple[str, ...]
    source: Path
