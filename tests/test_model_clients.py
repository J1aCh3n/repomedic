import unittest
from types import SimpleNamespace

from repomedic.agent_schemas import PlanReport
from repomedic.model_clients import (
    ModelOutputError,
    OpenAIResponsesModel,
    ScriptedModel,
)


VALID_PLAN = {
    "acceptance_criteria": ["Pass the threshold test."],
    "investigation_tasks": ["Inspect the comparison."],
    "candidate_paths": ["pricing.py"],
    "repair_steps": ["Use an inclusive comparison."],
}


class FakeResponses:
    def __init__(self) -> None:
        self.arguments = None

    def parse(self, **kwargs):
        self.arguments = kwargs
        return SimpleNamespace(
            output_parsed=PlanReport.model_validate(VALID_PLAN),
            usage=SimpleNamespace(input_tokens=10, output_tokens=5, total_tokens=15),
        )


class FakeOpenAI:
    def __init__(self) -> None:
        self.responses = FakeResponses()


class ModelClientTests(unittest.TestCase):
    def test_scripted_model_validates_every_response(self) -> None:
        model = ScriptedModel({"planner": [{"acceptance_criteria": []}]})

        with self.assertRaises(ModelOutputError):
            model.generate(
                agent="planner",
                instructions="plan",
                input_data={"issue": "bug"},
                output_type=PlanReport,
            )

    def test_openai_adapter_uses_responses_structured_parse_without_storage(self) -> None:
        client = FakeOpenAI()
        model = OpenAIResponsesModel("test-model", client=client)

        result = model.generate(
            agent="planner",
            instructions="Plan safely.",
            input_data={"issue": "threshold"},
            output_type=PlanReport,
        )

        self.assertEqual(result.output.repair_steps[0], "Use an inclusive comparison.")
        self.assertIs(client.responses.arguments["text_format"], PlanReport)
        self.assertFalse(client.responses.arguments["store"])
        self.assertEqual(result.usage.total_tokens, 15)


if __name__ == "__main__":
    unittest.main()
