from argparse import ArgumentParser
from pathlib import Path

from document_pipeline.pipeline import process


def main(argv: list[str] | None = None) -> int:
    parser = ArgumentParser(description="Convert a plain-text document to Markdown")
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--uppercase-title", action="store_true")
    args = parser.parse_args(argv)
    process(
        args.source,
        args.destination,
        uppercase_title=args.uppercase_title,
    )
    print(args.destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
