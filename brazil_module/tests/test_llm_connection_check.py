import sys
import unittest
from unittest.mock import MagicMock, patch

if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    _fm = MagicMock()
    _fm._ = lambda x: x
    sys.modules["frappe"] = _fm
    sys.modules["frappe.utils"] = _fm.utils

import frappe

import brazil_module.services.intelligence.llm.check as check_mod
from brazil_module.services.intelligence.llm.base import LLMError
from brazil_module.services.intelligence.llm.check import check_connection


class _Settings:
    llm_provider = "Google"
    model_standard = "gemini-3.8-flash"


class TestConnectionCheck(unittest.TestCase):
    """Finds a wrong credential when it is pasted, not at 08:00 next morning."""

    def setUp(self):
        frappe.reset_mock()
        frappe.get_single.return_value = _Settings()

    def test_reports_who_answered_and_with_which_model(self):
        llm = MagicMock()
        llm.ask.return_value = "OK"
        llm.provider.name = "google"

        with patch.object(check_mod, "LLM", return_value=llm):
            result = check_connection()

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["provider"], "google")
        self.assertEqual(result["model"], "gemini-3.8-flash")
        self.assertEqual(result["answer"], "OK")

    def test_spends_as_little_as_possible(self):
        llm = MagicMock()
        llm.ask.return_value = "OK"

        with patch.object(check_mod, "LLM", return_value=llm):
            check_connection()

        self.assertLessEqual(llm.ask.call_args.kwargs["max_tokens"], 16)

    def test_a_refused_credential_comes_back_readable(self):
        llm = MagicMock()
        llm.ask.side_effect = LLMError("google", 403, "the service account was refused")

        with patch.object(check_mod, "LLM", return_value=llm):
            result = check_connection()

        self.assertEqual(result["status"], "error")
        self.assertIn("service account", result["message"])

    def test_a_misconfigured_provider_does_not_raise(self):
        # A missing project or an unknown provider fails at build time, before
        # any call, and the button still has to show the reason.
        with patch.object(check_mod, "LLM", side_effect=LLMError("google", None, "no project is configured")):
            result = check_connection()

        self.assertEqual(result["status"], "error")
        self.assertIn("project", result["message"])

    def test_an_unexpected_failure_is_still_an_answer(self):
        with patch.object(check_mod, "LLM", side_effect=RuntimeError("boom")):
            result = check_connection()

        self.assertEqual(result["status"], "error")
        self.assertIn("boom", result["message"])


if __name__ == "__main__":
    unittest.main()
