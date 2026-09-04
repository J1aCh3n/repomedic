from pathlib import Path
import unittest

from repomedic.manifest import ManifestError, load_manifest
from tests.helpers import temporary_directory


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CASE_MANIFEST = (
    PROJECT_ROOT / "benchmarks" / "cases" / "order_service_001" / "manifest.yaml"
)


class ManifestTests(unittest.TestCase):
    def test_loads_case_contract(self) -> None:
        manifest = load_manifest(CASE_MANIFEST)

        self.assertEqual(manifest.case_id, "order_service_001")
        self.assertEqual(manifest.fixture.version, "order_service-v1")
        self.assertEqual(manifest.public_test.cwd, "repo")
        self.assertEqual(manifest.evaluator_test.cwd, "evaluator")
        self.assertEqual(manifest.limits.wall_time_seconds, 60)

    def test_rejects_absolute_allowed_path(self) -> None:
        text = CASE_MANIFEST.read_text(encoding="utf-8").replace(
            "    - order_service/pricing.py",
            "    - C:/outside.py",
        )
        with temporary_directory() as temp_dir:
            path = Path(temp_dir) / "manifest.yaml"
            path.write_text(text, encoding="utf-8")

            with self.assertRaisesRegex(ManifestError, "relative"):
                load_manifest(path)

    def test_rejects_non_python_test_command(self) -> None:
        text = CASE_MANIFEST.read_text(encoding="utf-8").replace(
            "argv: [python, -m, unittest, discover, -s, tests, -v]",
            "argv: [powershell, -Command, Get-ChildItem]",
        )
        with temporary_directory() as temp_dir:
            path = Path(temp_dir) / "manifest.yaml"
            path.write_text(text, encoding="utf-8")

            with self.assertRaisesRegex(ManifestError, "python"):
                load_manifest(path)


if __name__ == "__main__":
    unittest.main()
