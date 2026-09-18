import sys
import unittest
from unittest.mock import MagicMock

if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    _fm = MagicMock()
    _fm._ = lambda x: x
    sys.modules["frappe"] = _fm
    sys.modules["frappe.utils"] = _fm.utils

import frappe

from brazil_module.patches.v1_1.rename_model_tiers import execute


class TestRenameModelTiers(unittest.TestCase):
    """Carries a site from haiku/sonnet/opus to fast/standard/deep."""

    def setUp(self):
        frappe.reset_mock()
        frappe.db.get_single_value.side_effect = None
        frappe.db.set_single_value.side_effect = None
        frappe.db.sql.side_effect = None
        self.stored = {
            "haiku_model": "claude-haiku-4-5-20251001",
            "sonnet_model": "claude-sonnet-5",
            "opus_model": "claude-opus-5",
            "haiku_timeout_seconds": 30,
            "sonnet_timeout_seconds": 60,
            "opus_timeout_seconds": 120,
        }
        frappe.db.get_single_value.side_effect = lambda doctype, field: self.stored.get(field)
        self.written = {}
        frappe.db.set_single_value.side_effect = lambda doctype, field, value: self.written.__setitem__(
            field, value
        )

    def test_carries_the_models_over_to_the_new_fields(self):
        execute()

        self.assertEqual(self.written["model_fast"], "claude-haiku-4-5-20251001")
        self.assertEqual(self.written["model_standard"], "claude-sonnet-5")
        self.assertEqual(self.written["model_deep"], "claude-opus-5")

    def test_carries_the_timeouts_over(self):
        execute()

        self.assertEqual(self.written["timeout_fast"], 30)
        self.assertEqual(self.written["timeout_deep"], 120)

    def test_leaves_alone_what_was_already_filled_in(self):
        # Running migrate twice must not undo a model chosen by hand.
        self.stored["model_standard"] = "gemini-3.8-flash"

        execute()

        self.assertNotIn("model_standard", self.written)

    def test_sets_the_provider_that_the_site_was_already_using(self):
        execute()

        self.assertEqual(self.written["llm_provider"], "Anthropic")

    def test_does_not_change_a_provider_someone_chose(self):
        self.stored["llm_provider"] = "Google"

        execute()

        self.assertNotIn("llm_provider", self.written)

    def test_rewrites_the_tier_of_every_registry_row(self):
        execute()

        updates = [call.args for call in frappe.db.sql.call_args_list]
        pairs = {(args[1][1], args[1][0]) for args in updates if len(args) > 1}
        self.assertIn(("haiku", "fast"), pairs)
        self.assertIn(("sonnet", "standard"), pairs)
        self.assertIn(("opus", "deep"), pairs)

    def test_rewrites_the_escalation_tier_as_well(self):
        execute()

        statements = " ".join(str(call.args[0]) for call in frappe.db.sql.call_args_list)
        self.assertIn("escalation_model", statements)

    def test_a_step_that_fails_does_not_stop_the_others(self):
        # A half-migrated site still has to come out of `bench migrate`.
        frappe.db.sql.side_effect = Exception("no such table")

        execute()

        self.assertEqual(self.written["model_fast"], "claude-haiku-4-5-20251001")


if __name__ == "__main__":
    unittest.main()
