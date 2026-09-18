"""The rewritten tool loop: what it sends back on the second turn."""

import sys
import types as _types
import unittest
from unittest.mock import MagicMock

if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    _fm = MagicMock()
    _fm._ = lambda x: x
    sys.modules["frappe"] = _fm
    sys.modules["frappe.utils"] = _fm.utils

import frappe

# Mock the transitive imports only while agent.py is imported, then put back
# whatever was there: the frappe mock is shared by every test file, and a
# module left mocked here would follow the suite into the next one.
_TRANSITIVE = (
    "brazil_module.services.intelligence.action_executor",
    "brazil_module.services.intelligence.context_builder",
    "brazil_module.services.intelligence.decision_engine",
    "brazil_module.services.intelligence.channels.telegram_bot",
)
_previous = {name: sys.modules.get(name) for name in _TRANSITIVE}
for _name, _module in _previous.items():
    if not isinstance(_module, _types.ModuleType):
        sys.modules[_name] = MagicMock()

import brazil_module.services.intelligence.agent as agent_mod

for _name, _module in _previous.items():
    if isinstance(_module, _types.ModuleType):
        sys.modules[_name] = _module
    else:
        sys.modules.pop(_name, None)
from brazil_module.services.intelligence.llm.base import (
    AssistantTurn,
    Completion,
    LLMError,
    ToolCall,
    ToolResults,
    Usage,
    UserTurn,
)


def _completion(text="", tool_calls=(), state=("raw",)):
    return Completion(
        text=text,
        usage=Usage(10, 5, 0),
        model="claude-sonnet-5",
        provider="anthropic",
        tool_calls=tool_calls,
        provider_state=state,
    )


class _LLM:
    """Answers a scripted sequence and records every transcript it was given."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.transcripts = []

    def complete(self, **kwargs):
        self.transcripts.append(list(kwargs["messages"]))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


class ToolLoopCase(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()
        frappe.get_all.side_effect = None
        frappe.get_all.return_value = []
        frappe.db.get_single_value.side_effect = None
        agent_mod._circuit_breaker.record_failure = MagicMock()
        agent_mod._circuit_breaker.record_success = MagicMock()

    def _agent(self, llm):
        agent = agent_mod.Intelligence8Agent.__new__(agent_mod.Intelligence8Agent)
        agent.settings = MagicMock()
        agent.settings.get.return_value = "base prompt"
        agent.llm = llm
        agent.context_builder = MagicMock()
        agent.context_builder.build.return_value = {"module_context": "ctx"}
        agent.decision_engine = MagicMock()
        agent.action_executor = MagicMock()
        agent._current_read_tools = set()
        agent._handle_tool_call = MagicMock(
            return_value={"tool": "erp-read_document", "status": "executed", "result": {"name": "ISS-1"}}
        )
        return agent

    def _run(self, agent):
        return agent._process_with_module("p2p", "create_po", {"module": "p2p"}, "trace-1")


class TestSecondTurn(ToolLoopCase):
    def test_sends_back_the_model_turn_and_then_the_results(self):
        llm = _LLM(
            [
                _completion(text="vou ler", tool_calls=(ToolCall("call_1", "erp-read_document", {}),)),
                _completion(text="pronto"),
            ]
        )
        agent = self._agent(llm)

        result = self._run(agent)

        self.assertEqual(result["status"], "completed")
        second = llm.transcripts[1]
        self.assertIsInstance(second[0], UserTurn)
        self.assertIsInstance(second[1], AssistantTurn)
        self.assertEqual(second[1].provider_state, ("raw",))
        self.assertIsInstance(second[2], ToolResults)
        self.assertEqual(second[2].results[0].call_id, "call_1")
        self.assertEqual(second[2].results[0].name, "erp-read_document")

    def test_answers_every_call_of_a_parallel_turn(self):
        calls = (
            ToolCall("call_1", "erp-read_document", {}),
            ToolCall("call_2", "erp-read_document", {}),
        )
        llm = _LLM([_completion(tool_calls=calls), _completion(text="pronto")])
        agent = self._agent(llm)

        self._run(agent)

        self.assertEqual(len(llm.transcripts[1][2].results), 2)

    def test_stops_when_the_model_stops_asking_for_tools(self):
        llm = _LLM([_completion(text="pronto")])
        agent = self._agent(llm)

        result = self._run(agent)

        self.assertEqual(len(llm.transcripts), 1)
        self.assertEqual(result["text"], "pronto")

    def test_keeps_the_text_of_every_turn(self):
        llm = _LLM(
            [
                _completion(text="vou ler ", tool_calls=(ToolCall("call_1", "erp-read_document", {}),)),
                _completion(text="terminei"),
            ]
        )
        agent = self._agent(llm)

        self.assertEqual(self._run(agent)["text"], "vou ler terminei")

    def test_gives_up_after_the_turn_limit(self):
        forever = [
            _completion(tool_calls=(ToolCall(f"call_{n}", "erp-read_document", {}),)) for n in range(10)
        ]
        llm = _LLM(forever)
        agent = self._agent(llm)

        self._run(agent)

        self.assertEqual(len(llm.transcripts), 5)


class TestFailures(ToolLoopCase):
    def test_a_provider_failure_is_reported_and_counted(self):
        llm = _LLM([LLMError("google", 429, "quota exceeded")])
        agent = self._agent(llm)

        result = self._run(agent)

        self.assertEqual(result["status"], "error")
        self.assertIn("quota exceeded", result["message"])
        agent_mod._circuit_breaker.record_failure.assert_called_once()

    def test_an_unexpected_failure_does_not_escape_the_loop(self):
        # Anything an adapter raises outside its own guard used to escape
        # process_event entirely, leaving the breaker none the wiser.
        llm = _LLM([TypeError("something in the adapter")])
        agent = self._agent(llm)

        result = self._run(agent)

        self.assertEqual(result["status"], "error")
        agent_mod._circuit_breaker.record_failure.assert_called_once()


if __name__ == "__main__":
    unittest.main()
