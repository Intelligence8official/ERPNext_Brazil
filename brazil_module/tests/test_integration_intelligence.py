import sys
from unittest.mock import MagicMock, patch

if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    _fm = MagicMock()
    _fm._ = lambda x: x
    sys.modules["frappe"] = _fm
    sys.modules["frappe.utils"] = _fm.utils

frappe = sys.modules["frappe"]
sys.modules.setdefault("anthropic", MagicMock())
sys.modules.setdefault("requests", MagicMock())

import unittest

# Mock all service dependencies for clean import
_mock_deps = [
    "brazil_module.services.intelligence.circuit_breaker",
    "brazil_module.services.intelligence.cost_tracker",
    "brazil_module.services.intelligence.decision_engine",
    "brazil_module.services.intelligence.action_executor",
    "brazil_module.services.intelligence.context_builder",
    "brazil_module.services.intelligence.tools",
    "brazil_module.services.intelligence.prompts.system_prompt",
    "brazil_module.services.intelligence.channels.channel_router",
    "brazil_module.services.intelligence.channels.telegram_bot",
    "brazil_module.services.intelligence.channels.erp_chat",
]
_saved = {}
for dep in _mock_deps:
    if dep in sys.modules:
        _saved[dep] = sys.modules[dep]
    sys.modules[dep] = MagicMock()

import brazil_module.services.intelligence.agent as agent_mod

# Restore
for dep, orig in _saved.items():
    sys.modules[dep] = orig
for dep in _mock_deps:
    if dep not in _saved and dep in sys.modules:
        del sys.modules[dep]


class TestRecurringExpenseFlow(unittest.TestCase):
    """Integration test: recurring expense event -> agent -> PO creation -> audit log."""

    def setUp(self):
        frappe.reset_mock()
        frappe.cache = MagicMock()
        frappe.cache.get_value.return_value = None  # No lock

    def _make_settings(self):
        s = MagicMock()
        s.enabled = True
        s.default_confidence_threshold = 0.85
        s.high_value_confirmation_pin = False
        s.high_value_threshold = 10000
        s.daily_budget_usd = 10
        s.pause_on_budget_exceeded = False
        s.haiku_model = "claude-haiku-4-5-20251001"
        s.sonnet_model = "claude-sonnet-4-6"
        s.opus_model = "claude-opus-4-6"
        s.haiku_timeout_seconds = 30
        s.sonnet_timeout_seconds = 60
        s.opus_timeout_seconds = 120
        return s

    def test_process_single_event_acquires_lock(self):
        """Verify idempotency lock is acquired and released."""
        frappe.get_single.return_value = self._make_settings()

        # Make process_event a no-op to focus on lock behavior
        with patch.object(agent_mod.Intelligence8Agent, "process_event", return_value={"status": "completed"}):
            agent_mod.process_single_event("recurring_schedule", "test-id-001", {"module": "p2p"})

        # Verify lock was acquired
        frappe.cache.get_value.assert_called_with("i8:lock:recurring_schedule:test-id-001")
        frappe.cache.set_value.assert_called_once()
        # Verify lock was released
        frappe.cache.delete_value.assert_called_with("i8:lock:recurring_schedule:test-id-001")

    def test_process_single_event_skips_if_locked(self):
        """If another worker is processing this event, skip it."""
        frappe.cache.get_value.return_value = 1  # Lock exists
        frappe.get_single.return_value = self._make_settings()

        agent_mod.process_single_event("recurring_schedule", "test-id-001", {"module": "p2p"})

        # Should NOT call set_value (no lock acquired)
        frappe.cache.set_value.assert_not_called()

    def test_on_communication_enqueues_classify_email(self):
        """Email arrives -> enqueue classify_email event."""
        frappe.db.get_single_value.return_value = True

        doc = MagicMock()
        doc.communication_type = "Communication"
        doc.sent_or_received = "Received"
        doc.name = "COM-100"
        doc.subject = "NF attached"
        doc.content = "Please find the invoice"
        doc.sender = "supplier@example.com"

        agent_mod.on_communication(doc)

        frappe.enqueue.assert_called_once()
        kwargs = frappe.enqueue.call_args[1]
        self.assertEqual(kwargs["event_type"], "classify_email")
        self.assertEqual(kwargs["event_data"]["communication"], "COM-100")
        self.assertEqual(kwargs["event_data"]["module"], "email")

    def test_on_nota_fiscal_enqueues_nf_received(self):
        """NF arrives -> enqueue nf_received event."""
        frappe.db.get_single_value.return_value = True

        doc = MagicMock()
        doc.name = "NF-050"
        doc.cnpj_emitente = "12345678000190"

        agent_mod.on_nota_fiscal(doc)

        frappe.enqueue.assert_called_once()
        kwargs = frappe.enqueue.call_args[1]
        self.assertEqual(kwargs["event_type"], "nf_received")
        self.assertEqual(kwargs["event_data"]["nota_fiscal"], "NF-050")

    def test_agent_skips_when_disabled(self):
        """Agent disabled -> skip processing."""
        settings = self._make_settings()
        settings.enabled = False
        frappe.get_single.return_value = settings

        agent = agent_mod.Intelligence8Agent()
        result = agent.process_event("test", {"module": "p2p"})
        self.assertEqual(result["status"], "skipped")

    def test_model_tiering(self):
        """Verify correct model selection per event type."""
        frappe.get_single.return_value = self._make_settings()
        agent = agent_mod.Intelligence8Agent()

        self.assertIn("haiku", agent.select_model("classify_email"))
        self.assertIn("sonnet", agent.select_model("create_po"))
        self.assertIn("opus", agent.select_model("anomaly_detected"))


if __name__ == "__main__":
    unittest.main()
