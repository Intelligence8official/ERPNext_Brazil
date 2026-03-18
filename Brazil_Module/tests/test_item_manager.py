"""Tests for item management service."""

import unittest
from unittest.mock import MagicMock
import sys

# Ensure frappe mock is in place before import
if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    frappe_mock = MagicMock()
    frappe_mock._ = lambda x: x
    sys.modules["frappe"] = frappe_mock
    sys.modules["frappe.utils"] = frappe_mock.utils

import frappe
from Brazil_Module.services.fiscal.item_manager import ItemManager


class TestDescriptionMatches(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()
        frappe.get_single.return_value = MagicMock()
        self.manager = ItemManager()

    def test_exact_match(self):
        self.assertTrue(self.manager._description_matches("Servico Limpeza", "Servico Limpeza"))

    def test_case_insensitive_match(self):
        self.assertTrue(self.manager._description_matches("servico limpeza", "SERVICO LIMPEZA"))

    def test_empty_strings(self):
        self.assertFalse(self.manager._description_matches("", ""))

    def test_none_strings(self):
        self.assertFalse(self.manager._description_matches(None, None))

    def test_one_none(self):
        self.assertFalse(self.manager._description_matches("test", None))
        self.assertFalse(self.manager._description_matches(None, "test"))

    def test_no_match(self):
        self.assertFalse(self.manager._description_matches("Produto A", "Servico B"))


class TestFindItem(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()
        self.settings = MagicMock()
        frappe.get_single.return_value = self.settings
        self.manager = ItemManager()

    def test_find_by_supplier_item_code(self):
        nf_item = MagicMock()
        nf_item.codigo_produto = "PROD001"
        nf_doc = MagicMock()
        nf_doc.supplier = "Test Supplier"
        # pluck="parent" returns flat list of strings
        frappe.get_all.return_value = ["ITEM-001"]
        result = self.manager.find_item(nf_item, nf_doc)
        self.assertEqual(result, "ITEM-001")

    def test_no_supplier_no_code(self):
        nf_item = MagicMock()
        nf_item.codigo_produto = ""
        nf_item.codigo_tributacao_nacional = ""
        nf_item.ncm = ""
        nf_item.descricao = ""
        nf_doc = MagicMock()
        nf_doc.supplier = ""
        frappe.get_all.return_value = []
        frappe.db.sql.return_value = []
        result = self.manager.find_item(nf_item, nf_doc)
        self.assertIsNone(result)


class TestCreateItem(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()
        self.settings = MagicMock()
        self.settings.default_item_group = "Products"
        frappe.get_single.return_value = self.settings
        self.manager = ItemManager()

    def test_create_product_item(self):
        nf_item = MagicMock()
        nf_item.codigo_produto = "PROD001"
        nf_item.descricao = "Produto Teste"
        nf_item.ncm = "84719012"
        nf_item.unidade = "UN"
        nf_doc = MagicMock()
        nf_doc.document_type = "NF-e"
        nf_doc.supplier = "Test Supplier"

        new_item = MagicMock()
        new_item.name = "PROD001"
        frappe.new_doc.return_value = new_item
        frappe.db.exists.return_value = False

        result = self.manager.create_item(nf_item, nf_doc)
        frappe.new_doc.assert_called_with("Item")

    def test_item_code_already_exists(self):
        nf_item = MagicMock()
        nf_item.codigo_produto = "EXISTING"
        nf_item.descricao = "Existing Item"
        nf_doc = MagicMock()
        nf_doc.document_type = "NF-e"

        frappe.db.exists.return_value = True
        result = self.manager.create_item(nf_item, nf_doc)
        self.assertEqual(result, "EXISTING")


class TestProcessNfItems(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()
        self.settings = MagicMock()
        frappe.get_single.return_value = self.settings
        self.manager = ItemManager()

    def test_empty_items_returns_all_created(self):
        nf = MagicMock()
        nf.items = []
        count, total, status = self.manager.process_nf_items(nf)
        self.assertEqual(total, 0)
        self.assertEqual(status, "All Created")

    def test_all_items_already_linked(self):
        nf = MagicMock()
        item1 = MagicMock()
        item1.item = "ITEM-001"
        nf.items = [item1]
        count, total, status = self.manager.process_nf_items(nf)
        self.assertEqual(total, 1)


if __name__ == "__main__":
    unittest.main()
