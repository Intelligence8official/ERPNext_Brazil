"""Tests for Purchase Order matching service."""

import unittest
from unittest.mock import MagicMock
import sys
from datetime import date

# Ensure frappe mock is in place before import
if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    frappe_mock = MagicMock()
    frappe_mock._ = lambda x: x
    sys.modules["frappe"] = frappe_mock
    sys.modules["frappe.utils"] = frappe_mock.utils

import frappe
from brazil.services.fiscal.po_matcher import POMatcher


def _setup_frappe_utils():
    """Configure frappe.utils mocks for date operations."""
    frappe.utils.getdate = lambda x: x if isinstance(x, date) else date.fromisoformat(str(x)[:10]) if x else None
    frappe.utils.add_days = lambda d, n: date.fromordinal(d.toordinal() + n) if isinstance(d, date) else d
    frappe.utils.date_diff = lambda a, b: (a - b).days if isinstance(a, date) and isinstance(b, date) else 0


class TestPOMatcherInit(unittest.TestCase):
    def test_init_loads_settings(self):
        frappe.reset_mock()
        _setup_frappe_utils()
        frappe.get_single.return_value = MagicMock()
        matcher = POMatcher()
        frappe.get_single.assert_called_with("Nota Fiscal Settings")


class TestAutoLinkPo(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()
        _setup_frappe_utils()
        self.settings = MagicMock()
        self.settings.po_date_range_days = 30
        self.settings.po_value_tolerance = 5
        frappe.get_single.return_value = self.settings
        self.matcher = POMatcher()

    def test_no_supplier_returns_not_applicable(self):
        nf = MagicMock()
        nf.supplier = None
        nf.emitente_cnpj = ""
        po_name, status, msg = self.matcher.auto_link_po(nf)
        self.assertIsNone(po_name)
        self.assertEqual(status, "Not Applicable")

    def test_no_candidates_returns_not_found(self):
        nf = MagicMock()
        nf.supplier = "Supplier A"
        nf.emitente_cnpj = "12345678000195"
        nf.data_emissao = date(2024, 1, 15)
        frappe.get_all.return_value = []
        po_name, status, msg = self.matcher.auto_link_po(nf)
        self.assertIsNone(po_name)
        self.assertEqual(status, "Not Found")


class TestCalculateItemMatchScore(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()
        _setup_frappe_utils()
        frappe.get_single.return_value = MagicMock()
        self.matcher = POMatcher()

    def test_no_items_scores_0(self):
        nf = MagicMock()
        nf.items = []
        po = MagicMock()
        po.items = []
        score = self.matcher._calculate_item_match_score(nf, po)
        self.assertEqual(score, 0)


if __name__ == "__main__":
    unittest.main()
