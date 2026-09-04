from collections import deque
from dataclasses import dataclass
from time import monotonic
from typing import Any, Generic, Protocol, TypeVar
import json

from openai import OpenAI, OpenAIError
from pydantic import BaseModel, ValidationError


OutputT = TypeVar("OutputT", bound=BaseModel)


class ModelClientError(RuntimeError):
    """Raised when a configured model call fails."""


class ModelOutputError(ModelClientError):
    """Raised when model output does not satisfy its declared schema."""


@dataclass(frozen=True)
class ModelUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    latency_ms: int = 0


@dataclass(frozen=True)
class ModelResult(Generic[OutputT]):
    output: OutputT
    usage: ModelUsage


class StructuredModel(Protocol):
    model_id: str

    def generate(
        self,
        *,
        agent: str,
        instructions: str,
        input_data: dict[str, Any],
        output_type: type[OutputT],
    ) -> ModelResult[OutputT]: ...


class ScriptedModel:
    model_id = "scripted:test"

    def __init__(self, responses: dict[str, list[object]]) -> None:
        self._responses = {
            agent: deque(values) for agent, values in responses.items()
        }
        self.calls: list[str] = []

    def generate(
        self,
        *,
        agent: str,
        instructions: str,
        input_data: dict[str, Any],
        output_type: type[OutputT],
    ) -> ModelResult[OutputT]:
        del instructions, input_data
        self.calls.append(agent)
        queue = self._responses.get(agent)
        if not queue:
            raise ModelClientError(f"no scripted response remains for {agent}")
        value = queue.popleft()
        if isinstance(value, Exception):
            raise value
        try:
            output = output_type.model_validate(value)
        except ValidationError as error:
            raise ModelOutputError(f"invalid {agent} output: {error}") from error
        return ModelResult(output=output, usage=ModelUsage())


class OpenAIResponsesModel:
    def __init__(
        self,
        model_id: str,
        *,
        max_output_tokens: int = 4000,
        client: OpenAI | None = None,
    ) -> None:
        if not model_id.strip():
            raise ValueError("model_id must be non-empty")
        self.model_id = model_id
        self.max_output_tokens = max_output_tokens
        self.client = client or OpenAI()

    def generate(
        self,
        *,
        agent: str,
        instructions: str,
        input_data: dict[str, Any],
        output_type: type[OutputT],
    ) -> ModelResult[OutputT]:
        started = monotonic()
        try:
            response = self.client.responses.parse(
                model=self.model_id,
                instructions=instructions,
                input=json.dumps(input_data, ensure_ascii=False, sort_keys=True),
                text_format=output_type,
                max_output_tokens=self.max_output_tokens,
                store=False,
            )
        except OpenAIError as error:
            raise ModelClientError(f"{agent} Responses API call failed: {error}") from error
        parsed = response.output_parsed
        if parsed is None:
            raise ModelOutputError(f"{agent} returned no parsed structured output")
        try:
            output = output_type.model_validate(parsed)
        except ValidationError as error:
            raise ModelOutputError(f"invalid {agent} output: {error}") from error
        usage = response.usage
        latency_ms = round((monotonic() - started) * 1000)
        return ModelResult(
            output=output,
            usage=ModelUsage(
                input_tokens=getattr(usage, "input_tokens", 0) if usage else 0,
                output_tokens=getattr(usage, "output_tokens", 0) if usage else 0,
                total_tokens=getattr(usage, "total_tokens", 0) if usage else 0,
                latency_ms=latency_ms,
            ),
        )
