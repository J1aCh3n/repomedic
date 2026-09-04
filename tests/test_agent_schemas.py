import unittest

from pydantic import ValidationError

from repomedic.agent_schemas import Evidence, ReviewReport


class AgentSchemaTests(unittest.TestCase):
    def test_rejects_unknown_agent_fields(self) -> None:
        with self.assertRaises(ValidationError):
            ReviewReport.model_validate(
                {
                    "verdict": "pass",
                    "reasons": ["Tests passed."],
                    "feedback": "",
                    "unexpected": True,
                }
            )

    def test_rejects_inverted_evidence_range(self) -> None:
        with self.assertRaisesRegex(ValidationError, "line_end"):
            Evidence(path="service.py", line_start=5, line_end=2, excerpt="x")


if __name__ == "__main__":
    unittest.main()
