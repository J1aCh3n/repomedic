import unittest
from unittest.mock import Mock, patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_openai import ChatOpenAI
from openai.types.responses import Response

from repomedic.graph import RunLimits, openai_model
from repomedic.tools import make_tools
from repomedic.sandbox import DockerSandbox


def response(output):
    return Response.model_validate({"id": "resp_test", "object": "response", "created_at": 0,
                                    "model": "test-model", "output": output,
                                    "parallel_tool_calls": False, "tool_choice": "auto", "tools": [],
                                    "status": "completed", "store": False,
                                    "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15,
                                              "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                                              "output_tokens_details": {"reasoning_tokens": 0}}})


class ModelTests(unittest.TestCase):
    def test_responses_history_replays_call_ids_and_encrypted_reasoning_without_network(self) -> None:
        root = Mock()
        responses = [response([
            {"id": "rs_test", "type": "reasoning", "summary": [], "encrypted_content": "opaque-ciphertext"},
            {"id": "fc_test", "type": "function_call", "call_id": "call_test",
             "name": "read_file", "arguments": '{"path":"app.py"}', "status": "completed"},
        ]), response([
            {"id": "msg_test", "type": "message", "role": "assistant", "status": "completed",
             "content": [{"type": "output_text", "text": "done", "annotations": []}]},
        ])]
        root.responses.with_raw_response.create.side_effect = [
            Mock(parse=Mock(return_value=value), headers={}) for value in responses]
        model = ChatOpenAI(model="test-model", api_key="unused-offline-key", root_client=root,
                           client=Mock(), async_client=Mock(), root_async_client=Mock(), use_responses_api=True,
                           output_version="responses/v1", store=False, max_retries=0)
        bound = model.bind_tools(make_tools(DockerSandbox()), parallel_tool_calls=False)
        history = [HumanMessage(content="repair")]
        with patch("httpx.Client.send", side_effect=AssertionError("offline test attempted network")):
            first = bound.invoke(history)
        self.assertEqual(first.tool_calls[0]["id"], "call_test")
        # Validate that a normal message serialization roundtrip preserves the opaque block.
        first = AIMessage.model_validate(first.model_dump())
        with patch("httpx.Client.send", side_effect=AssertionError("offline test attempted network")):
            bound.invoke(history + [first, ToolMessage(content="value = 1", tool_call_id="call_test")])
        payload = root.responses.with_raw_response.create.call_args.kwargs
        self.assertFalse(payload["store"])
        self.assertFalse(payload["parallel_tool_calls"])
        self.assertNotIn("previous_response_id", payload)
        items = payload["input"]
        self.assertTrue(any(item.get("encrypted_content") == "opaque-ciphertext" for item in items))
        self.assertTrue(any(item.get("type") == "function_call_output" and
                            item.get("call_id") == "call_test" for item in items))

    def test_live_factory_freezes_stateless_behavior_and_disables_hidden_retries(self) -> None:
        with patch("repomedic.graph.ChatOpenAI") as factory:
            openai_model("explicit-model", RunLimits(model_timeout=12), "low")
        arguments = factory.call_args.kwargs
        self.assertEqual(arguments["model"], "explicit-model")
        self.assertFalse(arguments["store"])
        self.assertFalse(arguments["use_previous_response_id"])
        self.assertEqual(arguments["max_retries"], 0)
        self.assertEqual(arguments["timeout"], 12)
