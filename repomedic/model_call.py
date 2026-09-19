"""One guarded model call shared by every agent: budgets, errors, usage and trace."""

from time import monotonic
from typing import Any

from langchain_core.exceptions import OutputParserException
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage
from openai import OpenAIError
from pydantic import ValidationError

from repomedic.artifacts import ArtifactWriter, redact_text


class ModelFailure(RuntimeError):
    """An explicit scripted/model protocol failure, never a fabricated fallback."""


def zero_usage() -> dict[str, int]:
    return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
            "latency_ms": 0, "unreported_calls": 0}


def invoke_model(writer: ArtifactWriter, model: Any, instructions: str,
                 messages: list[BaseMessage], *, usage: dict[str, int], model_calls: int,
                 max_tokens: int, agent: str,
                 trace: dict[str, Any] | None = None) -> tuple[AIMessage | None, dict[str, Any]]:
    """Return (response, state updates). A None response means the caller must stop.

    Updates always carry the new usage/model_calls; on failure they also carry
    status and error, so any agent's state can absorb them unchanged.
    """
    tags = {"agent": agent, **(trace or {})}
    if usage["total_tokens"] >= max_tokens:
        return None, {"status": "budget_exhausted", "error": "token budget exhausted"}
    if model is None:
        raise ModelFailure("a model is required to execute the agent")
    started = monotonic()
    try:
        response = model.invoke([SystemMessage(content=instructions), *messages])
    except (OpenAIError, OutputParserException, ValidationError, ModelFailure) as error:
        latency = round((monotonic() - started) * 1000)
        failed = {**usage, "latency_ms": usage["latency_ms"] + latency,
                  "unreported_calls": usage.get("unreported_calls", 0) + 1}
        writer.append_trace("model_failed", {**tags, "error": str(error), "latency_ms": latency})
        return None, {"status": "model_error", "error": redact_text(str(error)),
                      "model_calls": model_calls + 1, "usage": failed}
    if not isinstance(response, AIMessage):
        return None, {"status": "model_error", "error": "model did not return AIMessage"}
    updated = dict(usage)
    metadata = response.usage_metadata
    if metadata:
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            updated[key] += metadata.get(key, 0)
    else:
        updated["unreported_calls"] = updated.get("unreported_calls", 0) + 1
    updated["latency_ms"] += round((monotonic() - started) * 1000)
    result: dict[str, Any] = {"usage": updated, "model_calls": model_calls + 1}
    # Keep opaque reasoning in checkpoint history; never print it in public artifacts.
    writer.append_trace("model_completed", {
        **tags, "text": response.text, "tool_calls": response.tool_calls,
        "usage": metadata, "latency_ms": round((monotonic() - started) * 1000),
    })
    if updated["total_tokens"] >= max_tokens:
        return None, {**result, "status": "budget_exhausted", "error": "token budget exhausted"}
    if response.invalid_tool_calls or len(response.tool_calls) > 1:
        return None, {**result, "status": "model_error", "error": "invalid or multiple tool calls"}
    return response, result
