from collections import deque
from dataclasses import dataclass
from time import monotonic
from typing import Any, Generic, Literal, Protocol, TypeVar
import json

from openai import OpenAI, OpenAIError
from pydantic import BaseModel, ValidationError


OutputT = TypeVar("OutputT", bound=BaseModel)


class ModelClientError(RuntimeError):
    """Raised when a configured model call fails."""

    def __init__(self, message: str, *, usage: "ModelUsage | None" = None) -> None:
        super().__init__(message)
        self.usage = usage or ModelUsage()


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
        reasoning_effort: Literal["none", "low", "medium", "high", "xhigh", "max"] | None = None,
        client: OpenAI | None = None,
    ) -> None:
        if not model_id.strip():
            raise ValueError("model_id must be non-empty")
        self.model_id = model_id
        self.max_output_tokens = max_output_tokens
        self.reasoning_effort = reasoning_effort
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
        request: dict[str, Any] = {}
        if self.reasoning_effort is not None:
            request["reasoning"] = {"effort": self.reasoning_effort}
        try:
            response = self.client.responses.parse(
                model=self.model_id,
                instructions=instructions,
                input=json.dumps(input_data, ensure_ascii=False, sort_keys=True),
                text_format=output_type,
                max_output_tokens=self.max_output_tokens,
                store=False,
                **request,
            )
        except ValidationError as error:
            usage = ModelUsage(latency_ms=round((monotonic() - started) * 1000))
            raise ModelOutputError(
                f"invalid {agent} output: {error}", usage=usage
            ) from error
        except OpenAIError as error:
            usage = ModelUsage(latency_ms=round((monotonic() - started) * 1000))
            raise ModelClientError(
                f"{agent} Responses API call failed: {error}", usage=usage
            ) from error
        response_usage = response.usage
        usage = ModelUsage(
            input_tokens=(
                getattr(response_usage, "input_tokens", 0) if response_usage else 0
            ),
            output_tokens=(
                getattr(response_usage, "output_tokens", 0) if response_usage else 0
            ),
            total_tokens=(
                getattr(response_usage, "total_tokens", 0) if response_usage else 0
            ),
            latency_ms=round((monotonic() - started) * 1000),
        )
        parsed = response.output_parsed
        if parsed is None:
            raise ModelOutputError(
                f"{agent} returned no parsed structured output", usage=usage
            )
        try:
            output = output_type.model_validate(parsed)
        except ValidationError as error:
            raise ModelOutputError(
                f"invalid {agent} output: {error}", usage=usage
            ) from error
        return ModelResult(output=output, usage=usage)
