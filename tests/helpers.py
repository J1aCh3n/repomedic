from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
import shutil
import uuid


TEST_TEMP_ROOT = Path(__file__).resolve().parent / ".tmp"


@contextmanager
def temporary_directory() -> Iterator[str]:
    TEST_TEMP_ROOT.mkdir(exist_ok=True)
    directory = TEST_TEMP_ROOT / f"test-{uuid.uuid4().hex}"
    directory.mkdir()
    try:
        yield str(directory)
    finally:
        shutil.rmtree(directory)
