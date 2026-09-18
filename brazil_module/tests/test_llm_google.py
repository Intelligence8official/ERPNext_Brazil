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
    ToolCall,
    ToolResult,
    ToolResults,
    UserTurn,
)
from brazil_module.services.intelligence.llm.google_provider import GoogleProvider
from brazil_module.tests.llm_contract import ProviderContractTests

TOOLS = [
    {
        "name": "erp-read_document",
        "description": "reads",
        "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}},
    }
]


def _fake_client(*, text="", tool_calls=(), usage=(0, 0, 0), error=None, captured=None):
    """A stand-in for genai.Client, answering the way Gemini does.

    `prompt_token_count` INCLUDES the cached tokens — the opposite of
    Anthropic — so the adapter has to subtract them.
    """
    tokens_in, tokens_out, cached = usage
    parts = []
    if text:
        parts.append(SimpleNamespace(text=text, function_call=None, function_response=None))
    for call_id, name, arguments in tool_calls:
        parts.append(
            SimpleNamespace(
                text=None,
                function_call=SimpleNamespace(id=call_id, name=name, args=arguments),
                function_response=None,
            )
        )

    response = SimpleNamespace(
        candidates=[SimpleNamespace(content=SimpleNamespace(parts=parts), finish_reason="STOP")],
        usage_metadata=SimpleNamespace(
            prompt_token_count=tokens_in,
            candidates_token_count=tokens_out,
            cached_content_token_count=cached,
        ),
    )

    def generate_content(**kwargs):
        captured.append(kwargs)
        if error:
            raise error
        return response

    return SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))


class TestGoogleProvider(ProviderContractTests, unittest.TestCase):
    provider_name = "google"
    model = "gemini-3.8-flash"

    def adapter_for(self, *, text="", tool_calls=(), usage=(0, 0, 0), error=None):
        captured: list = []
        client = _fake_client(text=text, tool_calls=tool_calls, usage=usage, error=error, captured=captured)
        return GoogleProvider(project="i8-prod", location="global", client=client), captured

    def complete(self, adapter, **overrides):
        kwargs = {
            "model": self.model,
            "system": "voce e o operador",
            "messages": [UserTurn("oi")],
            "max_tokens": 4096,
        }
        kwargs.update(overrides)
        return adapter.complete(**kwargs)

    # --- what is particular to the Gemini wire format ---

    def test_the_system_prompt_is_configuration_not_a_message(self):
        adapter, captured = self.adapter_for(text="oi")

        self.complete(adapter)

        self.assertEqual(captured[0]["config"]["system_instruction"], "voce e o operador")
        self.assertEqual(captured[0]["config"]["max_output_tokens"], 4096)

    def test_a_user_turn_travels_as_a_content_with_parts(self):
        adapter, captured = self.adapter_for(text="oi")

        self.complete(adapter)

        self.assertEqual(
            captured[0]["contents"],
            [{"role": "user", "parts": [{"text": "oi"}]}],
        )

    def test_the_model_turn_uses_the_role_gemini_expects(self):
        adapter, captured = self.adapter_for(text="pronto")
        transcript = [
            UserTurn("processa"),
            AssistantTurn(text="vou ler", tool_calls=(ToolCall("call_1", "erp-read_document", {"name": "X"}),)),
        ]

        self.complete(adapter, messages=transcript)

        model_turn = captured[0]["contents"][1]
        self.assertEqual(model_turn["role"], "model")
        self.assertEqual(model_turn["parts"][0], {"text": "vou ler"})
        self.assertEqual(
            model_turn["parts"][1],
            {"function_call": {"name": "erp-read_document", "args": {"name": "X"}}},
        )

    def test_a_tool_result_goes_back_as_a_user_turn(self):
        # The REST reference allows only 'user' and 'model' as roles; a
        # 'tool' role gets the request rejected.
        adapter, captured = self.adapter_for(text="pronto")
        transcript = [ToolResults((ToolResult("call_1", "erp-read_document", '{"ok": true}'),))]

        self.complete(adapter, messages=transcript)

        turn = captured[0]["contents"][0]
        self.assertEqual(turn["role"], "user")
        self.assertEqual(
            turn["parts"][0],
            {"function_response": {"name": "erp-read_document", "response": {"ok": True}}},
        )

    def test_a_tool_result_that_is_not_an_object_is_wrapped(self):
        # from_function_response takes a dict; our tools may answer a list.
        adapter, captured = self.adapter_for(text="pronto")

        self.complete(adapter, messages=[ToolResults((ToolResult("c1", "erp-list_documents", "[1, 2]"),))])

        response = captured[0]["contents"][0]["parts"][0]["function_response"]["response"]
        self.assertEqual(response, {"result": [1, 2]})

    def test_tools_travel_as_json_schema_with_automatic_calling_off(self):
        # Left on, the SDK would call our Python functions by itself, which is
        # exactly what the agent loop must decide instead.
        adapter, captured = self.adapter_for(text="oi")

        self.complete(adapter, tools=TOOLS)

        declaration = captured[0]["config"]["tools"][0]["function_declarations"][0]
        self.assertEqual(declaration["name"], "erp-read_document")
        self.assertEqual(declaration["parameters_json_schema"], TOOLS[0]["input_schema"])
        self.assertTrue(captured[0]["config"]["automatic_function_calling"]["disable"])

    def test_a_call_without_an_id_still_gets_one(self):
        # Gemini may answer a function call with no id, and the agent loop
        # matches its results by id.
        adapter, _ = self.adapter_for(text="", tool_calls=((None, "erp-read_document", {}),))

        call = self.complete(adapter).tool_calls[0]

        self.assertTrue(call.id)

    def test_the_timeout_goes_over_in_milliseconds(self):
        # The SDK reads HttpOptions.timeout as milliseconds: sending seconds
        # would give every call a 30 ms deadline and fail all of them.
        adapter, captured = self.adapter_for(text="oi")

        self.complete(adapter, timeout=30)

        self.assertEqual(captured[0]["config"]["http_options"], {"timeout": 30_000})

    def test_a_blocked_prompt_says_why(self):
        adapter, captured = self.adapter_for(text="oi")
        blocked = SimpleNamespace(
            candidates=[],
            usage_metadata=None,
            prompt_feedback=SimpleNamespace(block_reason="SAFETY", block_reason_message="unsafe"),
        )
        adapter._client.models.generate_content = lambda **kwargs: blocked

        with self.assertRaises(Exception) as raised:
            self.complete(adapter)

        self.assertIn("SAFETY", str(raised.exception))

    def test_a_malformed_function_call_is_diagnosed_apart_from_a_block(self):
        # This is what a broken tool schema looks like, and it needs a
        # different answer than "the model refused the prompt".
        adapter, _ = self.adapter_for(text="oi")
        empty_turn = SimpleNamespace(
            candidates=[
                SimpleNamespace(
                    content=SimpleNamespace(parts=[]),
                    finish_reason="MALFORMED_FUNCTION_CALL",
                    finish_message="bad schema",
                )
            ],
            usage_metadata=None,
        )
        adapter._client.models.generate_content = lambda **kwargs: empty_turn

        with self.assertRaises(Exception) as raised:
            self.complete(adapter)

        self.assertIn("MALFORMED_FUNCTION_CALL", str(raised.exception))
        self.assertIn("tool", str(raised.exception).lower())

    def test_an_answer_with_nothing_in_it_is_an_error_not_an_empty_string(self):
        adapter, _ = self.adapter_for(text="")

        with self.assertRaises(Exception) as raised:
            self.complete(adapter)

        self.assertIn("STOP", str(raised.exception))


class TestGoogleClientConstruction(unittest.TestCase):
    """How the credentials that spend the Google credits are handed over."""

    def setUp(self):
        self.genai = MagicMock()
        self.service_account = MagicMock()
        google_module = MagicMock()
        google_module.genai = self.genai
        google_module.oauth2.service_account = self.service_account
        sys.modules["google"] = google_module
        sys.modules["google.genai"] = self.genai
        sys.modules["google.oauth2"] = google_module.oauth2
        for name in ("google.oauth2", "google.genai", "google"):
            self.addCleanup(sys.modules.pop, name, None)

    def test_enterprise_mode_sends_project_and_location(self):
        # Vertex is the only door the startup credits go through, and the
        # project must travel WITH the credentials: without it the SDK falls
        # back to application default credentials and ignores ours.
        GoogleProvider(project="i8-prod", location="global", service_account_json='{"type": "service_account"}').client

        kwargs = self.genai.Client.call_args.kwargs
        self.assertTrue(kwargs["vertexai"])
        self.assertEqual(kwargs["project"], "i8-prod")
        self.assertEqual(kwargs["location"], "global")
        self.assertIsNotNone(kwargs["credentials"])

    def test_without_a_service_account_it_leaves_the_default_credentials_alone(self):
        GoogleProvider(project="i8-prod", location="global").client

        kwargs = self.genai.Client.call_args.kwargs
        self.assertNotIn("credentials", kwargs)
        self.assertEqual(kwargs["project"], "i8-prod")

    def test_api_key_mode_does_not_pretend_to_be_vertex(self):
        GoogleProvider(api_key="AIza-test").client

        kwargs = self.genai.Client.call_args.kwargs
        self.assertEqual(kwargs, {"api_key": "AIza-test"})

    def test_a_broken_service_account_json_fails_with_a_readable_error(self):
        provider = GoogleProvider(project="i8-prod", service_account_json="{not json")

        with self.assertRaises(Exception) as raised:
            provider.client

        self.assertIn("service account", str(raised.exception).lower())


if __name__ == "__main__":
    unittest.main()
