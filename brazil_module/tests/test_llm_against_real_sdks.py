"""
The adapters, checked against the SDKs themselves instead of our fakes.

Every other adapter test builds a stand-in shaped like what the adapter is
about to read, which proves the adapter agrees with itself. These build the
request with the provider's own types and read a response made of the
provider's own objects, so a renamed field or a key the SDK rejects fails
here instead of in production.

Skipped when an SDK is not installed: a site running on one provider should
still be able to run the suite. The imports go around the mocks other test
files leave in `sys.modules` — a check that silently skips is worse than no
check at all.
"""

import json
import sys
import unittest
from unittest.mock import MagicMock

if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    _fm = MagicMock()
    _fm._ = lambda x: x
    sys.modules["frappe"] = _fm
    sys.modules["frappe.utils"] = _fm.utils

from brazil_module.services.intelligence.llm import (
    anthropic_provider,
    google_provider,
    openai_provider,
)
from brazil_module.services.intelligence.llm.base import (
    AssistantTurn,
    ToolCall,
    ToolResult,
    ToolResults,
    UserTurn,
)

TOOLS = [
    {
        "name": "erp-read_document",
        "description": "Reads a document",
        "input_schema": {
            "type": "object",
            "properties": {"doctype": {"type": "string"}, "name": {"type": "string"}},
            "required": ["doctype", "name"],
            "additionalProperties": False,
        },
    }
]

TRANSCRIPT = [
    UserTurn("processa a NF"),
    AssistantTurn(text="vou ler", tool_calls=(ToolCall("call_1", "erp-read_document", {"doctype": "Issue", "name": "ISS-1"}),)),
    ToolResults((ToolResult("call_1", "erp-read_document", '{"ok": true}'),)),
]


def _capture(adapter, attr, **overrides):
    """Run complete() against a client that only records the payload."""
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        raise _Stop()

    setattr(adapter, "_client", _Client(attr, create))
    kwargs = {"model": "m", "system": "s", "messages": TRANSCRIPT, "tools": TOOLS, "max_tokens": 256}
    kwargs.update(overrides)
    try:
        adapter.complete(**kwargs)
    except Exception:
        pass
    return captured


class _Stop(Exception):
    pass


class _Client:
    def __init__(self, attr, create):
        setattr(self, attr, type("_Namespace", (), {attr_name: staticmethod(create) for attr_name in ("create", "generate_content")})())


def _real(module_name: str, importer):
    """Import from the real package even when a test file has mocked it.

    The frappe mock is shared by the whole suite and so are these: test_agent
    puts a MagicMock under "anthropic", and `from anthropic.types import ...`
    then fails as if the SDK were missing.
    """
    mocked = {name: sys.modules.pop(name, None) for name in list(sys.modules) if name == module_name or name.startswith(f"{module_name}.")}
    mocked = {name: module for name, module in mocked.items() if isinstance(module, MagicMock)}
    try:
        return importer()
    except ImportError:
        return None
    finally:
        sys.modules.update(mocked)


def _import_genai():
    from google.genai import types

    return types


genai_types = _real("google", _import_genai)


@unittest.skipIf(genai_types is None, "google-genai is not installed")
class TestGoogleAgainstSDK(unittest.TestCase):
    def test_the_config_we_build_is_a_config_the_sdk_accepts(self):
        # The SDK's models forbid unknown keys, so a wrong name fails here
        # rather than reaching the wire.
        payload = _capture(google_provider.GoogleProvider(project="p"), "models", timeout=30)

        config = genai_types.GenerateContentConfig(**payload["config"])

        self.assertEqual(config.system_instruction, "s")
        self.assertEqual(config.max_output_tokens, 256)
        self.assertEqual(config.http_options.timeout, 30_000)
        self.assertTrue(config.automatic_function_calling.disable)
        self.assertEqual(
            config.tools[0].function_declarations[0].parameters_json_schema,
            TOOLS[0]["input_schema"],
        )

    def test_the_turns_we_build_are_contents_the_sdk_accepts(self):
        payload = _capture(google_provider.GoogleProvider(project="p"), "models")

        contents = [genai_types.Content(**turn) for turn in payload["contents"]]

        self.assertEqual([c.role for c in contents], ["user", "model", "user"])
        self.assertEqual(contents[1].parts[1].function_call.name, "erp-read_document")
        self.assertEqual(contents[2].parts[0].function_response.id, "call_1")
        self.assertEqual(contents[2].parts[0].function_response.response, {"output": {"ok": True}})

    def test_reads_a_response_built_out_of_the_sdks_own_objects(self):
        response = genai_types.GenerateContentResponse(
            candidates=[
                genai_types.Candidate(
                    content=genai_types.Content(
                        role="model",
                        parts=[
                            genai_types.Part(text="vou ler"),
                            genai_types.Part(
                                function_call=genai_types.FunctionCall(
                                    id="call_9", name="erp-read_document", args={"doctype": "Issue"}
                                )
                            ),
                        ],
                    ),
                    finish_reason="STOP",
                )
            ],
            usage_metadata=genai_types.GenerateContentResponseUsageMetadata(
                prompt_token_count=100,
                candidates_token_count=20,
                cached_content_token_count=40,
                thoughts_token_count=500,
                tool_use_prompt_token_count=10,
            ),
        )

        completion = google_provider._to_completion(response, "gemini-3.8-flash", "google")

        self.assertEqual(completion.text, "vou ler")
        self.assertEqual(completion.tool_calls[0].id, "call_9")
        self.assertEqual(completion.tool_calls[0].arguments, {"doctype": "Issue"})
        self.assertEqual(completion.usage.input_tokens, 70)
        self.assertEqual(completion.usage.cached_input_tokens, 40)
        self.assertEqual(completion.usage.output_tokens, 520)
        self.assertTrue(completion.provider_state)


def _import_openai():
    from openai.types.responses import (
        Response,
        ResponseFunctionToolCall,
        ResponseOutputMessage,
        ResponseOutputText,
        ResponseUsage,
    )
    from openai.types.responses.response_usage import InputTokensDetails, OutputTokensDetails

    return (
        Response,
        ResponseFunctionToolCall,
        ResponseOutputMessage,
        ResponseOutputText,
        ResponseUsage,
        InputTokensDetails,
        OutputTokensDetails,
    )


_openai_types = _real("openai", _import_openai)
if _openai_types:
    (
        Response,
        ResponseFunctionToolCall,
        ResponseOutputMessage,
        ResponseOutputText,
        ResponseUsage,
        InputTokensDetails,
        OutputTokensDetails,
    ) = _openai_types
else:
    Response = None


@unittest.skipIf(Response is None, "openai is not installed")
class TestOpenAIAgainstSDK(unittest.TestCase):
    def test_reads_a_response_built_out_of_the_sdks_own_objects(self):
        response = Response(
            id="resp_1",
            created_at=0,
            model="gpt-5.6-terra",
            object="response",
            parallel_tool_calls=False,
            tool_choice="auto",
            tools=[],
            status="completed",
            output=[
                ResponseOutputMessage(
                    id="msg_1",
                    type="message",
                    role="assistant",
                    status="completed",
                    content=[ResponseOutputText(type="output_text", text="vou ler", annotations=[])],
                ),
                ResponseFunctionToolCall(
                    type="function_call",
                    call_id="call_9",
                    name="erp-read_document",
                    arguments=json.dumps({"doctype": "Issue"}),
                ),
            ],
            usage=ResponseUsage(
                input_tokens=100,
                output_tokens=20,
                total_tokens=120,
                input_tokens_details=InputTokensDetails(cached_tokens=40, cache_write_tokens=10),
                output_tokens_details=OutputTokensDetails(reasoning_tokens=5),
            ),
        )

        completion = openai_provider._to_completion(response, "gpt-5.6-terra", "openai")

        self.assertEqual(completion.text, "vou ler")
        self.assertEqual(completion.tool_calls[0].id, "call_9")
        # Three buckets, three prices: full rate, cache read, cache write.
        self.assertEqual(completion.usage.input_tokens, 50)
        self.assertEqual(completion.usage.cached_input_tokens, 40)
        self.assertEqual(completion.usage.cache_write_tokens, 10)
        # Reasoning tokens are already part of output_tokens here, unlike
        # Gemini, which reports them apart.
        self.assertEqual(completion.usage.output_tokens, 20)

    def test_the_items_we_build_are_items_the_sdk_accepts(self):
        payload = _capture(openai_provider.OpenAIProvider(api_key="sk"), "responses")

        call = ResponseFunctionToolCall(**payload["input"][2])

        self.assertEqual(call.call_id, "call_1")
        self.assertEqual(json.loads(call.arguments), {"doctype": "Issue", "name": "ISS-1"})
        self.assertEqual(payload["input"][3]["type"], "function_call_output")


def _import_anthropic():
    from anthropic.types import Message, TextBlock, ToolUseBlock, Usage

    return Message, TextBlock, ToolUseBlock, Usage


_anthropic_types = _real("anthropic", _import_anthropic)
if _anthropic_types:
    Message, TextBlock, ToolUseBlock, AnthropicUsage = _anthropic_types
else:
    Message = None


@unittest.skipIf(Message is None, "anthropic is not installed")
class TestAnthropicAgainstSDK(unittest.TestCase):
    def test_reads_a_message_built_out_of_the_sdks_own_objects(self):
        message = Message(
            id="msg_1",
            model="claude-sonnet-5",
            role="assistant",
            type="message",
            stop_reason="tool_use",
            content=[
                TextBlock(type="text", text="vou ler", citations=None),
                ToolUseBlock(
                    type="tool_use", id="toolu_1", name="erp-read_document", input={"doctype": "Issue"}
                ),
            ],
            usage=AnthropicUsage(
                input_tokens=100,
                output_tokens=20,
                cache_read_input_tokens=40,
                cache_creation_input_tokens=25,
            ),
        )

        completion = anthropic_provider._to_completion(message, "claude-sonnet-5", "anthropic")

        self.assertEqual(completion.text, "vou ler")
        self.assertEqual(completion.tool_calls[0].id, "toolu_1")
        # Anthropic already leaves the cached tokens out of input_tokens.
        self.assertEqual(completion.usage.input_tokens, 100)
        self.assertEqual(completion.usage.cached_input_tokens, 40)
        self.assertEqual(completion.usage.cache_write_tokens, 25)
        self.assertEqual(len(completion.provider_state), 2)


if __name__ == "__main__":
    unittest.main()
