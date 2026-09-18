import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    _fm = MagicMock()
    _fm._ = lambda x: x
    sys.modules["frappe"] = _fm
    sys.modules["frappe.utils"] = _fm.utils

from brazil_module.services.intelligence.llm.anthropic_provider import AnthropicProvider
from brazil_module.services.intelligence.llm.base import (
    AssistantTurn,
    ToolCall,
    ToolResult,
    ToolResults,
    UserTurn,
)
from brazil_module.tests.llm_contract import ProviderContractTests

TOOLS = [{"name": "erp-read_document", "description": "reads", "input_schema": {"type": "object"}}]


def _fake_client(*, text="", tool_calls=(), usage=(0, 0, 0), error=None, captured=None):
    """A stand-in for anthropic.Anthropic, answering the way it does.

    Anthropic reports `input_tokens` ALREADY without the cached ones, which is
    why the adapter must not subtract them again.
    """
    tokens_in, tokens_out, cached = usage
    blocks = []
    if text:
        blocks.append(SimpleNamespace(type="text", text=text))
    for call_id, name, arguments in tool_calls:
        blocks.append(SimpleNamespace(type="tool_use", id=call_id, name=name, input=arguments))

    response = SimpleNamespace(
        content=blocks,
        stop_reason="tool_use" if tool_calls else "end_turn",
        usage=SimpleNamespace(
            input_tokens=tokens_in - cached,
            output_tokens=tokens_out,
            cache_read_input_tokens=cached,
        ),
    )

    def create(**kwargs):
        captured.append(kwargs)
        if error:
            raise error
        return response

    return SimpleNamespace(messages=SimpleNamespace(create=create))


class TestAnthropicProvider(ProviderContractTests, unittest.TestCase):
    provider_name = "anthropic"
    model = "claude-sonnet-5"

    def adapter_for(self, *, text="", tool_calls=(), usage=(0, 0, 0), error=None):
        captured: list = []
        client = _fake_client(text=text, tool_calls=tool_calls, usage=usage, error=error, captured=captured)
        return AnthropicProvider(api_key="sk-test", client=client), captured

    def complete(self, adapter, **overrides):
        kwargs = {
            "model": self.model,
            "system": "voce e o operador",
            "messages": [UserTurn("oi")],
            "max_tokens": 4096,
        }
        kwargs.update(overrides)
        return adapter.complete(**kwargs)

    # --- what is particular to the Anthropic wire format ---

    def test_sends_the_system_prompt_as_its_own_argument(self):
        adapter, captured = self.adapter_for(text="oi")

        self.complete(adapter)

        self.assertEqual(captured[0]["system"], "voce e o operador")
        self.assertEqual(captured[0]["messages"], [{"role": "user", "content": "oi"}])

    def test_sends_tools_with_their_schema_key_unchanged(self):
        adapter, captured = self.adapter_for(text="oi")

        self.complete(adapter, tools=TOOLS)

        self.assertEqual(captured[0]["tools"], TOOLS)

    def test_replays_a_tool_exchange_as_blocks(self):
        adapter, captured = self.adapter_for(text="pronto")
        transcript = [
            UserTurn("processa a NF"),
            AssistantTurn(text="vou ler", tool_calls=(ToolCall("call_1", "erp-read_document", {"name": "X"}),)),
            ToolResults((ToolResult("call_1", "erp-read_document", '{"ok": true}'),)),
        ]

        self.complete(adapter, messages=transcript)

        messages = captured[0]["messages"]
        self.assertEqual(messages[0], {"role": "user", "content": "processa a NF"})
        self.assertEqual(messages[1]["role"], "assistant")
        self.assertEqual(
            messages[1]["content"],
            [
                {"type": "text", "text": "vou ler"},
                {"type": "tool_use", "id": "call_1", "name": "erp-read_document", "input": {"name": "X"}},
            ],
        )
        self.assertEqual(
            messages[2],
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "call_1", "content": '{"ok": true}'}
                ],
            },
        )

    def test_passes_the_timeout_through(self):
        adapter, captured = self.adapter_for(text="oi")

        self.complete(adapter, timeout=30)

        self.assertEqual(captured[0]["timeout"], 30)

    def test_keeps_the_status_code_of_a_refused_call(self):
        # 429 and 529 are what a rate limit and an overload look like, and the
        # alert should be able to tell them apart.
        error = RuntimeError("rate limited")
        error.status_code = 429
        adapter, _ = self.adapter_for(error=error)

        with self.assertRaises(Exception) as raised:
            self.complete(adapter)

        self.assertEqual(raised.exception.status, 429)

    def test_builds_its_own_client_when_none_is_injected(self):
        fake_sdk = MagicMock()
        sys.modules["anthropic"] = fake_sdk

        AnthropicProvider(api_key="sk-live").client

        fake_sdk.Anthropic.assert_called_once_with(api_key="sk-live")


if __name__ == "__main__":
    unittest.main()
