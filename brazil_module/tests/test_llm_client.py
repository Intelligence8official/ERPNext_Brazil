import sys
import unittest
from unittest.mock import MagicMock

if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    _fm = MagicMock()
    _fm._ = lambda x: x
    sys.modules["frappe"] = _fm
    sys.modules["frappe.utils"] = _fm.utils

import frappe

from brazil_module.services.intelligence.llm.base import (
    Completion,
    LLMError,
    ToolCall,
    Usage,
    UserTurn,
)
from brazil_module.services.intelligence.llm.client import LLM


class _Provider:
    """A provider that answers whatever the test tells it to."""

    name = "google"

    def __init__(self, completion=None, error=None):
        self.completion = completion
        self.error = error
        self.calls = []

    def complete(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.completion or Completion(
            text="pronto", usage=Usage(10, 2, 3), model=kwargs["model"], provider=self.name
        )


class _Settings:
    llm_provider = "Google"
    model_standard = "gemini-3.8-flash"
    model_fast = "gemini-3.5-flash-lite"
    timeout_standard = 45
    timeout_fast = 15


class _Tracker:
    def __init__(self):
        self.logged = []

    def log(self, **kwargs):
        self.logged.append(kwargs)
        return "COST-001"


class TestLLMComplete(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()
        frappe.cache.get_value.return_value = None
        frappe.cache.get_value.side_effect = None
        self.provider = _Provider()
        self.tracker = _Tracker()
        self.llm = LLM(settings=_Settings(), provider=self.provider, cost_tracker=self.tracker)

    def test_picks_the_model_and_timeout_of_the_tier(self):
        self.llm.complete(tier="fast", system="s", messages=[UserTurn("oi")])

        call = self.provider.calls[0]
        self.assertEqual(call["model"], "gemini-3.5-flash-lite")
        self.assertEqual(call["timeout"], 15)

    def test_standard_is_the_default_tier(self):
        self.llm.complete(system="s", messages=[UserTurn("oi")])

        self.assertEqual(self.provider.calls[0]["model"], "gemini-3.8-flash")

    def test_hands_the_answer_back_untouched(self):
        completion = self.llm.complete(system="s", messages=[UserTurn("oi")])

        self.assertEqual(completion.text, "pronto")

    def test_every_call_is_logged_with_its_provider_and_model(self):
        # The orchestrator's router call used to spend money with no record;
        # logging here is what makes the credit burn add up.
        self.llm.complete(system="s", messages=[UserTurn("oi")], module="briefing", function_name="format")

        logged = self.tracker.logged[0]
        self.assertEqual(logged["provider"], "google")
        self.assertEqual(logged["model"], "gemini-3.8-flash")
        self.assertEqual(logged["tokens_in"], 10)
        self.assertEqual(logged["tokens_out"], 2)
        self.assertEqual(logged["cached_tokens"], 3)
        self.assertEqual(logged["module"], "briefing")
        self.assertEqual(logged["function_name"], "format")
        self.assertGreaterEqual(logged["latency_ms"], 0)

    def test_a_cost_log_that_fails_does_not_lose_the_answer(self):
        self.tracker.log = MagicMock(side_effect=Exception("db down"))

        completion = self.llm.complete(system="s", messages=[UserTurn("oi")])

        self.assertEqual(completion.text, "pronto")

    def test_tools_and_max_tokens_go_through(self):
        tools = [{"name": "erp-read_document", "description": "x", "input_schema": {}}]

        self.llm.complete(system="s", messages=[UserTurn("oi")], tools=tools, max_tokens=512)

        self.assertEqual(self.provider.calls[0]["tools"], tools)
        self.assertEqual(self.provider.calls[0]["max_tokens"], 512)

    def test_a_tool_call_comes_back_whole(self):
        self.provider.completion = Completion(
            text="",
            usage=Usage(1, 1, 0),
            model="gemini-3.8-flash",
            provider="google",
            tool_calls=(ToolCall("c1", "erp-read_document", {"name": "X"}),),
        )

        completion = self.llm.complete(system="s", messages=[UserTurn("oi")])

        self.assertTrue(completion.wants_tools)
        self.assertEqual(completion.tool_calls[0].name, "erp-read_document")


class TestLLMFailure(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()
        frappe.cache.get_value.return_value = None
        frappe.cache.get_value.side_effect = None
        self.provider = _Provider(error=LLMError("google", 429, "quota exceeded"))
        self.tracker = _Tracker()
        self.llm = LLM(settings=_Settings(), provider=self.provider, cost_tracker=self.tracker)

    def test_the_error_reaches_the_caller(self):
        with self.assertRaises(LLMError):
            self.llm.complete(system="s", messages=[UserTurn("oi")])

    def test_a_call_that_failed_costs_nothing(self):
        with self.assertRaises(LLMError):
            self.llm.complete(system="s", messages=[UserTurn("oi")])

        self.assertEqual(self.tracker.logged, [])

    def test_the_failure_is_announced(self):
        # You chose to be told and to stop, so a provider that refuses calls
        # cannot fail only into the Error Log.
        with self.assertRaises(LLMError):
            self.llm.complete(system="s", messages=[UserTurn("oi")])

        key = frappe.cache.set_value.call_args.args[0]
        self.assertIn("google", key)

    def test_the_alert_is_held_back_when_one_was_sent_recently(self):
        # Telling you on every retry of a scheduled job is the same as not
        # telling you.
        frappe.cache.get_value.return_value = "sent"

        with self.assertRaises(LLMError):
            self.llm.complete(system="s", messages=[UserTurn("oi")])

        frappe.cache.set_value.assert_not_called()

    def test_an_alert_that_cannot_be_sent_does_not_hide_the_error(self):
        frappe.cache.get_value.side_effect = Exception("cache down")

        with self.assertRaises(LLMError) as raised:
            self.llm.complete(system="s", messages=[UserTurn("oi")])

        self.assertIn("quota exceeded", str(raised.exception))


class TestAsk(unittest.TestCase):
    """The one-shot helper the briefing, the router and the anomaly use."""

    def setUp(self):
        frappe.reset_mock()
        frappe.cache.get_value.return_value = None
        self.provider = _Provider()
        self.llm = LLM(settings=_Settings(), provider=self.provider, cost_tracker=_Tracker())

    def test_returns_only_the_text(self):
        answer = self.llm.ask(system="s", prompt="formate isto", tier="fast", module="briefing")

        self.assertEqual(answer, "pronto")

    def test_sends_the_prompt_as_the_only_turn(self):
        self.llm.ask(system="s", prompt="formate isto")

        self.assertEqual(self.provider.calls[0]["messages"], [UserTurn("formate isto")])


if __name__ == "__main__":
    unittest.main()
