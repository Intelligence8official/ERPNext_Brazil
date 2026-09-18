import sys
from unittest.mock import MagicMock

if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    _fm = MagicMock()
    _fm._ = lambda x: x
    sys.modules["frappe"] = _fm
    sys.modules["frappe.utils"] = _fm.utils

# Mock anthropic before import
sys.modules.setdefault("anthropic", MagicMock())

frappe = sys.modules["frappe"]

# Mock the service dependencies so we can import agent
_agent_mock_deps = [
    "brazil_module.services.intelligence.circuit_breaker",
    "brazil_module.services.intelligence.cost_tracker",
    "brazil_module.services.intelligence.decision_engine",
    "brazil_module.services.intelligence.action_executor",
    "brazil_module.services.intelligence.context_builder",
    "brazil_module.services.intelligence.tools",
    "brazil_module.services.intelligence.prompts.system_prompt",
]
_agent_saved = {}
for _dep in _agent_mock_deps:
    if _dep in sys.modules:
        _agent_saved[_dep] = sys.modules[_dep]
    sys.modules[_dep] = MagicMock()

import unittest

import brazil_module.services.intelligence.agent as _agent_mod

# Restore mocked deps so other test files can import the real modules
for _dep, _orig in _agent_saved.items():
    sys.modules[_dep] = _orig
for _dep in _agent_mock_deps:
    if _dep not in _agent_saved and _dep in sys.modules:
        del sys.modules[_dep]

# Re-import after cleanup so names are bound to the mocks that agent holds


class TestAgentProcessEvent(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()
        frappe.db.get_single_value.side_effect = None
        frappe.get_single.return_value = self._make_settings()
        frappe.cache = MagicMock()
        frappe.cache.get_value.return_value = None

    def _make_settings(self):
        s = MagicMock()
        s.enabled = True
        s.default_confidence_threshold = 0.85
        s.high_value_confirmation_pin = False
        s.high_value_threshold = 10000
        s.daily_budget_usd = 10
        s.pause_on_budget_exceeded = False
        # A site that has not run the rename patch yet: the new fields are
        # empty and the old ones still hold the models in use.
        s.llm_provider = "Anthropic"
        s.model_fast = None
        s.model_standard = None
        s.model_deep = None
        s.timeout_fast = None
        s.timeout_standard = None
        s.timeout_deep = None
        s.haiku_model = "claude-haiku-4-5-20251001"
        s.sonnet_model = "claude-sonnet-4-6"
        s.opus_model = "claude-opus-4-6"
        s.haiku_timeout_seconds = 30
        s.sonnet_timeout_seconds = 60
        s.opus_timeout_seconds = 120
        return s

    def test_skips_when_disabled(self):
        settings = self._make_settings()
        settings.enabled = False
        frappe.get_single.return_value = settings
        agent = _agent_mod.Intelligence8Agent()
        result = agent.process_event("test", {})
        self.assertEqual(result["status"], "skipped")

    def test_simple_events_go_to_the_fast_tier(self):
        agent = _agent_mod.Intelligence8Agent()
        self.assertEqual(agent.select_tier("classify_email"), "fast")

    def test_hard_events_go_to_the_deep_tier(self):
        agent = _agent_mod.Intelligence8Agent()
        self.assertEqual(agent.select_tier("anomaly_detected"), "deep")

    def test_everything_else_goes_to_the_standard_tier(self):
        agent = _agent_mod.Intelligence8Agent()
        self.assertEqual(agent.select_tier("create_po"), "standard")


class TestExtractConfidence(unittest.TestCase):
    def test_extracts_confidence_from_text(self):
        result = _agent_mod.Intelligence8Agent._extract_confidence(
            "I found a matching PO. Confidence: 0.92"
        )
        self.assertAlmostEqual(result, 0.92)

    def test_returns_default_when_no_confidence(self):
        result = _agent_mod.Intelligence8Agent._extract_confidence(
            "Doing something without mentioning confidence."
        )
        self.assertEqual(result, 0.5)

    def test_returns_default_for_an_answer_with_no_text(self):
        self.assertEqual(_agent_mod.Intelligence8Agent._extract_confidence(""), 0.5)


class TestOnCommunication(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()
        frappe.db.get_single_value.side_effect = None

    def test_skips_when_disabled(self):
        frappe.db.get_single_value.return_value = False
        doc = MagicMock()
        doc.communication_type = "Communication"
        doc.sent_or_received = "Received"
        _agent_mod.on_communication(doc)
        frappe.enqueue.assert_not_called()

    def test_skips_sent_emails(self):
        frappe.db.get_single_value.return_value = True
        doc = MagicMock()
        doc.communication_type = "Communication"
        doc.sent_or_received = "Sent"
        _agent_mod.on_communication(doc)
        frappe.enqueue.assert_not_called()

    def test_enqueues_received_email(self):
        frappe.db.get_single_value.return_value = True
        doc = MagicMock()
        doc.communication_type = "Communication"
        doc.sent_or_received = "Received"
        doc.name = "COM-001"
        doc.subject = "Invoice attached"
        doc.content = "Please find..."
        doc.sender = "supplier@test.com"
        _agent_mod.on_communication(doc)
        frappe.enqueue.assert_called_once()
        call_kwargs = frappe.enqueue.call_args[1]
        self.assertEqual(call_kwargs["event_type"], "classify_email")


class TestOnNotaFiscal(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()
        frappe.db.get_single_value.side_effect = None

    def test_skips_when_disabled(self):
        frappe.db.get_single_value.return_value = False
        doc = MagicMock()
        _agent_mod.on_nota_fiscal(doc)
        frappe.enqueue.assert_not_called()

    def test_enqueues_when_enabled(self):
        frappe.db.get_single_value.return_value = True
        doc = MagicMock()
        doc.name = "NF-001"
        doc.cnpj_emitente = "12345678000100"
        _agent_mod.on_nota_fiscal(doc)
        frappe.enqueue.assert_called_once()


if __name__ == "__main__":
    unittest.main()
