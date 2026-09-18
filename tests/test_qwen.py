import json
import os
import unittest
from unittest.mock import patch

import httpx
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_openai import ChatOpenAI

from repomedic import graph
from repomedic.sandbox import DockerSandbox
from repomedic.tools import make_tools


class QwenTests(unittest.TestCase):
    def test_chat_tools_thinking_call_ids_and_output_limit_without_network(self) -> None:
        payloads = []
        def handle(request):
            self.assertEqual(request.url.path, "/compatible-mode/v1/chat/completions")
            self.assertEqual(request.headers["authorization"], "Bearer unused-dashscope-key")
            payloads.append(json.loads(request.content))
            name = "declare_scope" if len(payloads) == 1 else "read_file"
            args = {"paths": ["app.py"], "plan": "repair"} if name == "declare_scope" else {"path": "app.py"}
            return httpx.Response(200, json={"id": "chat_test", "object": "chat.completion", "created": 0,
                "model": "qwen3.7-plus", "choices": [{"index": 0, "finish_reason": "tool_calls",
                    "message": {"role": "assistant", "content": None, "reasoning_content": "private-qwen-reasoning",
                        "tool_calls": [{"id": "call_test", "type": "function", "function": {
                            "name": name, "arguments": json.dumps(args)}}]}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})
        client = httpx.Client(transport=httpx.MockTransport(handle))
        def offline_factory(**kwargs):
            return ChatOpenAI(**kwargs, http_client=client)
        with client, patch.dict(os.environ, {"DASHSCOPE_API_KEY": "unused-dashscope-key", "OPENAI_API_KEY": "unused-openai-key"}), \
                patch("repomedic.graph.ChatOpenAI", side_effect=offline_factory):
            settings = graph.ModelSettings(provider="qwen")
            model = graph.openai_model("qwen3.7-plus", graph.RunLimits(max_output_tokens=321), settings=settings)
            scope = model.bind_tools([{"type": "function", "function": {"name": "declare_scope",
                "parameters": {"type": "object", "properties": {}}}}], tool_choice="declare_scope", parallel_tool_calls=False)
            first = scope.invoke([HumanMessage(content="repair")])
            agent = model.bind_tools(make_tools(DockerSandbox()), parallel_tool_calls=False)
            second = agent.invoke([HumanMessage(content="repair"), first,
                                   ToolMessage(content="Scope declared", tool_call_id="call_test")])
        self.assertEqual(first.tool_calls[0]["name"], "declare_scope")
        self.assertEqual(second.tool_calls[0]["id"], "call_test")
        self.assertEqual(second.usage_metadata["total_tokens"], 15)
        self.assertFalse(payloads[0]["enable_thinking"])
        self.assertTrue(payloads[1]["enable_thinking"])
        self.assertEqual(payloads[0]["tool_choice"]["function"]["name"], "declare_scope")
        self.assertEqual(payloads[1]["tool_choice"], "auto")
        self.assertTrue(any(item.get("role") == "tool" and item["tool_call_id"] == "call_test"
                            for item in payloads[1]["messages"]))
        for payload in payloads:
            self.assertEqual(payload["max_tokens"], 321)
            self.assertFalse(payload["parallel_tool_calls"])
            self.assertNotIn("max_completion_tokens", payload)
            self.assertNotIn("reasoning", payload)
            self.assertNotIn("include", payload)
        self.assertNotIn("private-qwen-reasoning", json.dumps(second.model_dump()))

    def test_requires_dashscope_key_and_rejects_openai_reasoning_options(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "unused-openai-key"}, clear=True):
            with self.assertRaisesRegex(ValueError, "DASHSCOPE_API_KEY"):
                graph.openai_model("qwen3.7-plus", graph.RunLimits(), settings=graph.ModelSettings(provider="qwen"))
        with self.assertRaisesRegex(ValueError, "reasoning"):
            graph.openai_model("qwen3.7-plus", graph.RunLimits(), "high", settings=graph.ModelSettings(provider="qwen"))

    def test_settings_validate_endpoint_and_thinking_options(self) -> None:
        settings = graph.ModelSettings(provider="qwen", base_url="https://example.invalid/compatible-mode/v1/", enable_thinking=False)
        self.assertEqual(settings.base_url, "https://example.invalid/compatible-mode/v1")
        self.assertFalse(settings.enable_thinking)
        for url in ("http://example.invalid/v1", "https://user:password@example.invalid/v1", "https://example.invalid/v1?key=secret"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                graph.ModelSettings(provider="qwen", base_url=url)
        with self.assertRaises(ValueError):
            graph.ModelSettings(provider="openai", enable_thinking=True)
