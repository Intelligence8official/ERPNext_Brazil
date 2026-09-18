import json
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    _fm = MagicMock()
    _fm._ = lambda x: x
    sys.modules["frappe"] = _fm
    sys.modules["frappe.utils"] = _fm.utils

from brazil_module.services.intelligence.llm.base import (
    AssistantTurn,
    LLMError,
    ToolCall,
    ToolResult,
    ToolResults,
    UserTurn,
)
from brazil_module.services.intelligence.llm.openai_provider import OpenAIProvider
from brazil_module.tests.llm_contract import ProviderContractTests

TOOLS = [
    {
        "name": "erp-read_document",
        "description": "reads",
        "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}},
    }
]


def _fake_client(*, text="", tool_calls=(), usage=(0, 0, 0), error=None, captured=None, output=None):
    """A stand-in for openai.OpenAI answering on the Responses API.

    `input_tokens` INCLUDES the cached ones, which live in
    `input_tokens_details.cached_tokens` — so the adapter subtracts.
    """
    tokens_in, tokens_out, cached = usage
    items = []
    if text:
        items.append(
            SimpleNamespace(
                type="message",
                content=[SimpleNamespace(type="output_text", text=text)],
            )
        )
    for call_id, name, arguments in tool_calls:
        items.append(
            SimpleNamespace(
                type="function_call",
                call_id=call_id,
                name=name,
                arguments=json.dumps(arguments) if isinstance(arguments, dict) else arguments,
            )
        )

    response = SimpleNamespace(
        status="completed",
        incomplete_details=None,
        output=items if output is None else output,
        usage=SimpleNamespace(
            input_tokens=tokens_in,
            output_tokens=tokens_out,
            input_tokens_details=SimpleNamespace(cached_tokens=cached),
        ),
    )

    def create(**kwargs):
        captured.append(kwargs)
        if error:
            raise error
        return response

    return SimpleNamespace(responses=SimpleNamespace(create=create))


class TestOpenAIProvider(ProviderContractTests, unittest.TestCase):
    provider_name = "openai"
    model = "gpt-5.6-terra"

    def adapter_for(self, *, text="", tool_calls=(), usage=(0, 0, 0), error=None):
        captured: list = []
        client = _fake_client(text=text, tool_calls=tool_calls, usage=usage, error=error, captured=captured)
        return OpenAIProvider(api_key="sk-test", client=client), captured

    def complete(self, adapter, **overrides):
        kwargs = {
            "model": self.model,
            "system": "voce e o operador",
            "messages": [UserTurn("oi")],
            "max_tokens": 4096,
        }
        kwargs.update(overrides)
        return adapter.complete(**kwargs)

    def assert_replayed(self, payload, provider_state):
        self.assertEqual(payload["input"][1:], list(provider_state))

    # --- what is particular to the Responses API ---

    def test_the_system_prompt_is_the_instructions_field(self):
        adapter, captured = self.adapter_for(text="oi")

        self.complete(adapter)

        self.assertEqual(captured[0]["instructions"], "voce e o operador")
        self.assertEqual(captured[0]["input"], [{"role": "user", "content": "oi"}])
        self.assertEqual(captured[0]["max_output_tokens"], 4096)

    def test_tools_are_flat_and_not_strict(self):
        # In the Responses API a tool is internally tagged, and leaving out
        # `strict` makes it ATTEMPT strict mode — which rejects schemas that
        # the other two providers accept.
        adapter, captured = self.adapter_for(text="oi")

        self.complete(adapter, tools=TOOLS)

        self.assertEqual(
            captured[0]["tools"],
            [
                {
                    "type": "function",
                    "name": "erp-read_document",
                    "description": "reads",
                    "parameters": TOOLS[0]["input_schema"],
                    "strict": False,
                }
            ],
        )

    def test_a_tool_exchange_travels_as_separate_items(self):
        adapter, captured = self.adapter_for(text="pronto")
        transcript = [
            UserTurn("processa"),
            AssistantTurn(text="vou ler", tool_calls=(ToolCall("call_1", "erp-read_document", {"name": "X"}),)),
            ToolResults((ToolResult("call_1", "erp-read_document", '{"ok": true}'),)),
        ]

        self.complete(adapter, messages=transcript)

        items = captured[0]["input"]
        self.assertEqual(items[0], {"role": "user", "content": "processa"})
        self.assertEqual(items[1], {"role": "assistant", "content": "vou ler"})
        self.assertEqual(
            items[2],
            {
                "type": "function_call",
                "call_id": "call_1",
                "name": "erp-read_document",
                "arguments": '{"name": "X"}',
            },
        )
        self.assertEqual(
            items[3],
            {"type": "function_call_output", "call_id": "call_1", "output": '{"ok": true}'},
        )

    def test_an_assistant_turn_with_no_text_sends_only_the_call(self):
        adapter, captured = self.adapter_for(text="pronto")
        transcript = [AssistantTurn(tool_calls=(ToolCall("call_1", "erp-read_document", {}),))]

        self.complete(adapter, messages=transcript)

        self.assertEqual([item.get("type") for item in captured[0]["input"]], ["function_call"])

    def test_arguments_come_back_parsed(self):
        adapter, _ = self.adapter_for(
            text="", tool_calls=(("call_1", "erp-read_document", {"doctype": "Issue"}),)
        )

        self.assertEqual(self.complete(adapter).tool_calls[0].arguments, {"doctype": "Issue"})

    def test_arguments_that_are_not_json_stop_the_turn(self):
        # Running a tool with empty arguments could mean "everything": better
        # to fail loudly than to act on a guess.
        adapter, _ = self.adapter_for(text="", tool_calls=(("call_1", "erp-list_documents", "{broken"),))

        with self.assertRaises(LLMError) as raised:
            self.complete(adapter)

        self.assertIn("erp-list_documents", str(raised.exception))

    def test_a_refusal_is_an_error_not_an_empty_answer(self):
        # A refusal arrives inside the message item. Read only as text it
        # comes out as "", and the agent loop takes that for a finished turn.
        adapter, _ = self.adapter_for(text="oi")
        refused = SimpleNamespace(
            status="completed",
            incomplete_details=None,
            output=[
                SimpleNamespace(
                    type="message",
                    content=[SimpleNamespace(type="refusal", refusal="I cannot help with that")],
                )
            ],
            usage=SimpleNamespace(input_tokens=5, output_tokens=1, input_tokens_details=SimpleNamespace(cached_tokens=0)),
        )
        adapter._client.responses.create = lambda **kwargs: refused

        with self.assertRaises(LLMError) as raised:
            self.complete(adapter)

        self.assertIn("I cannot help with that", str(raised.exception))

    def test_a_truncated_answer_says_it_was_truncated(self):
        # The budget can be spent entirely on reasoning tokens, and what comes
        # back is an empty answer that looks successful.
        adapter, _ = self.adapter_for(text="oi")
        truncated = SimpleNamespace(
            status="incomplete",
            incomplete_details=SimpleNamespace(reason="max_output_tokens"),
            output=[],
            usage=SimpleNamespace(input_tokens=5, output_tokens=16, input_tokens_details=SimpleNamespace(cached_tokens=0)),
        )
        adapter._client.responses.create = lambda **kwargs: truncated

        with self.assertRaises(LLMError) as raised:
            self.complete(adapter)

        self.assertIn("max_output_tokens", str(raised.exception))

    def test_an_answer_with_nothing_in_it_is_an_error(self):
        adapter, _ = self.adapter_for(text="")

        with self.assertRaises(LLMError):
            self.complete(adapter)

    def test_passes_the_timeout_through(self):
        adapter, captured = self.adapter_for(text="oi")

        self.complete(adapter, timeout=30)

        self.assertEqual(captured[0]["timeout"], 30)

    def test_builds_its_own_client_when_none_is_injected(self):
        fake_sdk = MagicMock()
        sys.modules["openai"] = fake_sdk
        self.addCleanup(sys.modules.pop, "openai", None)

        OpenAIProvider(api_key="sk-live").client

        fake_sdk.OpenAI.assert_called_once_with(api_key="sk-live")


if __name__ == "__main__":
    unittest.main()
