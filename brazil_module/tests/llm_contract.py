"""
The contract every LLM adapter has to honour, whatever provider it wraps.

These are the promises the rest of Intelligence8 depends on: an answer comes
back as text plus tool calls, tokens are counted with cached ones apart, and a
provider failure arrives as LLMError and nothing else. Each adapter's own test
file adds what is particular to its wire format.

A subclass implements `adapter_for()`, which builds the adapter over a fake
SDK client shaped like that provider's own responses.
"""

from brazil_module.services.intelligence.llm.base import LLMError


class ProviderContractTests:
    """Mix into a TestCase. Not a TestCase itself, so it is not collected twice."""

    provider_name: str

    def adapter_for(self, *, text="", tool_calls=(), usage=(0, 0, 0), error=None):
        """Return (adapter, captured_calls).

        `tool_calls` is a tuple of (id, name, arguments). `usage` is
        (input_tokens, output_tokens, cached_tokens) as THAT provider reports
        them — the adapter is what has to normalize them.
        """
        raise NotImplementedError

    def test_brings_back_the_text_of_the_answer(self):
        adapter, _ = self.adapter_for(text="tudo certo")

        self.assertEqual(self.complete(adapter).text, "tudo certo")

    def test_says_which_provider_and_model_answered(self):
        adapter, _ = self.adapter_for(text="oi")

        completion = self.complete(adapter)

        self.assertEqual(completion.provider, self.provider_name)
        self.assertEqual(completion.model, self.model)

    def test_counts_cached_tokens_apart_from_the_rest(self):
        # What is billed at the full rate and what is billed at the cached
        # rate must not overlap, whatever the provider reports.
        adapter, _ = self.adapter_for(text="oi", usage=(1000, 200, 400))

        usage = self.complete(adapter).usage

        self.assertEqual(usage.input_tokens + usage.cached_input_tokens, 1000)
        self.assertEqual(usage.cached_input_tokens, 400)
        self.assertEqual(usage.output_tokens, 200)

    def test_reads_the_tools_the_model_asked_for(self):
        adapter, _ = self.adapter_for(
            text="vou ler",
            tool_calls=(("call_1", "erp-read_document", {"doctype": "Issue", "name": "ISS-1"}),),
        )

        completion = self.complete(adapter)

        self.assertTrue(completion.wants_tools)
        self.assertEqual(len(completion.tool_calls), 1)
        call = completion.tool_calls[0]
        self.assertEqual((call.id, call.name), ("call_1", "erp-read_document"))
        self.assertEqual(call.arguments, {"doctype": "Issue", "name": "ISS-1"})

    def test_a_plain_answer_asks_for_no_tools(self):
        adapter, _ = self.adapter_for(text="pronto")

        self.assertFalse(self.complete(adapter).wants_tools)

    def test_a_provider_failure_arrives_as_one_error_type(self):
        adapter, _ = self.adapter_for(error=RuntimeError("provider is down"))

        with self.assertRaises(LLMError) as raised:
            self.complete(adapter)

        self.assertEqual(raised.exception.provider, self.provider_name)
        self.assertIn("provider is down", str(raised.exception))

    def test_refuses_an_unportable_tool_name_before_spending_a_call(self):
        adapter, captured = self.adapter_for(text="oi")

        with self.assertRaises(LLMError):
            self.complete(adapter, tools=[{"name": "erp.read", "description": "x", "input_schema": {}}])

        self.assertEqual(captured, [])
