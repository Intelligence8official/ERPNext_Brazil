import unittest

from brazil_module.services.intelligence.llm.base import (
    AssistantTurn,
    LLMError,
    ToolCall,
    ToolResult,
    ToolResults,
    Usage,
    UserTurn,
    validate_tool_names,
)


def _tool(name: str) -> dict:
    return {"name": name, "description": "does a thing", "input_schema": {"type": "object"}}


class TestValidateToolNames(unittest.TestCase):
    """The narrowest rule of the three providers, checked before the call.

    A bad name comes back as a 400 from the provider with the whole request
    rejected; catching it here says which tool is at fault.
    """

    def test_accepts_the_names_the_app_already_uses(self):
        validate_tool_names([_tool("erp-read_document"), _tool("fiscal-process_xml")])

    def test_rejects_a_name_that_starts_with_a_digit(self):
        with self.assertRaises(LLMError) as raised:
            validate_tool_names([_tool("2fa-check")])

        self.assertIn("2fa-check", str(raised.exception))

    def test_rejects_a_dot_because_openai_does(self):
        with self.assertRaises(LLMError):
            validate_tool_names([_tool("erp.read_document")])

    def test_rejects_a_name_longer_than_64_characters(self):
        # Vertex caps at 64 even though the Developer API allows 128.
        with self.assertRaises(LLMError):
            validate_tool_names([_tool("erp-" + "x" * 61)])

    def test_accepts_a_name_of_exactly_64_characters(self):
        validate_tool_names([_tool("e" + "x" * 63)])


class TestLLMError(unittest.TestCase):
    def test_says_which_provider_failed(self):
        # This text reaches the Error Log and Telegram: it has to name the
        # provider, because the whole point is knowing who broke.
        error = LLMError("google", 429, "quota exceeded")

        self.assertIn("google", str(error))
        self.assertIn("429", str(error))
        self.assertIn("quota exceeded", str(error))

    def test_works_without_a_status(self):
        self.assertIn("openai", str(LLMError("openai", None, "connection reset")))


class TestTranscriptTypes(unittest.TestCase):
    def test_a_turn_carries_what_the_model_said_and_asked_for(self):
        turn = AssistantTurn(text="vou ler", tool_calls=(ToolCall("call_1", "erp-read_document", {"name": "X"}),))

        self.assertEqual(turn.tool_calls[0].arguments, {"name": "X"})

    def test_a_tool_result_keeps_the_name_as_well_as_the_id(self):
        # Anthropic and OpenAI answer by id; Gemini answers by function name.
        result = ToolResult(call_id="call_1", name="erp-read_document", content="{}")

        self.assertEqual((result.call_id, result.name), ("call_1", "erp-read_document"))

    def test_turns_are_immutable(self):
        with self.assertRaises(Exception):
            UserTurn("oi").text = "outra coisa"

    def test_tool_results_travel_as_one_turn(self):
        turn = ToolResults((ToolResult("call_1", "a", "{}"), ToolResult("call_2", "b", "{}")))

        self.assertEqual(len(turn.results), 2)

    def test_usage_defaults_to_no_cached_tokens(self):
        self.assertEqual(Usage(input_tokens=10, output_tokens=2).cached_input_tokens, 0)


if __name__ == "__main__":
    unittest.main()
