from pathlib import Path
import json
import unittest

from repomedic.artifacts import ArtifactWriter, redact_text
from tests.helpers import temporary_directory


class ArtifactTests(unittest.TestCase):
    def test_redacts_common_secret_forms(self) -> None:
        text = (
            "OPENAI_API_KEY=abc123 "
            "token=ghp_abcdefghijklmnopqrstuvwxyz123456 "
            "credential=sk-proj-abcdefghijklmnopqrstuvwxyz"
        )

        redacted = redact_text(text)

        self.assertNotIn("abc123", redacted)
        self.assertNotIn("ghp_", redacted)
        self.assertNotIn("sk-proj-", redacted)
        self.assertIn("[REDACTED]", redacted)

    def test_redacts_sensitive_json_keys_without_hiding_usage_metrics(self) -> None:
        with temporary_directory() as temp_dir:
            writer = ArtifactWriter(Path(temp_dir))
            writer.write_json(
                "config.json",
                {
                    "OPENAI_API_KEY": "plain-secret",
                    "nested": {"access_token": "another-secret"},
                    "usage": {"input_tokens": 42},
                },
            )

            result = json.loads(
                (Path(temp_dir) / "config.json").read_text(encoding="utf-8")
            )

        self.assertEqual(result["OPENAI_API_KEY"], "[REDACTED]")
        self.assertEqual(result["nested"]["access_token"], "[REDACTED]")
        self.assertEqual(result["usage"]["input_tokens"], 42)

    def test_writes_sanitized_json_and_trace(self) -> None:
        with temporary_directory() as temp_dir:
            writer = ArtifactWriter(Path(temp_dir))
            writer.write_json("result.json", {"stdout": "PASSWORD=hunter2"})
            writer.append_trace("test_completed", {"authorization": "Bearer secret"})

            result = json.loads((Path(temp_dir) / "result.json").read_text(encoding="utf-8"))
            trace = (Path(temp_dir) / "trace.jsonl").read_text(encoding="utf-8")

        self.assertNotIn("hunter2", result["stdout"])
        self.assertNotIn("Bearer secret", trace)


if __name__ == "__main__":
    unittest.main()
