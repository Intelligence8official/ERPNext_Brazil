"""Tests for supplier management service."""

import unittest
from unittest.mock import MagicMock, patch, PropertyMock
import sys

# Ensure frappe mock is in place before import
if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    frappe_mock = MagicMock()
    frappe_mock._ = lambda x: x
    sys.modules["frappe"] = frappe_mock
    sys.modules["frappe.utils"] = frappe_mock.utils

import frappe
from brazil.services.fiscal.supplier_manager import SupplierManager


class TestFindSupplierByCnpj(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()
        frappe.get_single.return_value = MagicMock()
        self.manager = SupplierManager()

    def test_find_by_exact_clean_cnpj(self):
        # pluck="name" returns flat list of strings
        frappe.get_all.return_value = ["Supplier A"]
        result = self.manager.find_supplier_by_cnpj("12345678000195")
        self.assertEqual(result, "Supplier A")

    def test_find_returns_none_when_not_found(self):
        frappe.get_all.return_value = []
        frappe.db.sql.return_value = []
        result = self.manager.find_supplier_by_cnpj("00000000000000")
        self.assertIsNone(result)

    def test_empty_cnpj(self):
        frappe.get_all.return_value = []
        frappe.db.sql.return_value = []
        result = self.manager.find_supplier_by_cnpj("")
        self.assertIsNone(result)

    def test_none_cnpj(self):
        result = self.manager.find_supplier_by_cnpj(None)
        self.assertIsNone(result)


class TestProcessNfSupplier(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()
        self.settings = MagicMock()
        self.settings.auto_create_supplier = True
        self.settings.default_supplier_group = "All Supplier Groups"
        frappe.get_single.return_value = self.settings
        self.manager = SupplierManager()

    def test_no_cnpj_returns_failed(self):
        nf = MagicMock()
        nf.emitente_cnpj = ""
        nf.document_type = "NF-e"
        supplier, status, msg = self.manager.process_nf_supplier(nf)
        self.assertIsNone(supplier)
        self.assertEqual(status, "Failed")

    def test_existing_supplier_returns_linked(self):
        nf = MagicMock()
        nf.emitente_cnpj = "12345678000195"
        nf.document_type = "NF-e"
        # pluck="name" returns flat list
        frappe.get_all.return_value = ["Existing Supplier"]
        supplier, status, msg = self.manager.process_nf_supplier(nf)
        self.assertEqual(supplier, "Existing Supplier")
        self.assertEqual(status, "Linked")

    def test_auto_create_when_not_found(self):
        nf = MagicMock()
        nf.emitente_cnpj = "12345678000195"
        nf.emitente_razao_social = "New Company Ltda"
        nf.document_type = "NF-e"
        frappe.get_all.return_value = []
        frappe.db.sql.return_value = []

        new_supplier = MagicMock()
        new_supplier.name = "New Company Ltda"
        frappe.new_doc.return_value = new_supplier
        frappe.db.exists.return_value = False

        supplier, status, msg = self.manager.process_nf_supplier(nf)
        self.assertEqual(status, "Created")

    def test_auto_create_disabled_returns_not_found(self):
        nf = MagicMock()
        nf.emitente_cnpj = "12345678000195"
        nf.document_type = "NF-e"
        self.settings.auto_create_supplier = False
        frappe.get_all.return_value = []
        frappe.db.sql.return_value = []

        supplier, status, msg = self.manager.process_nf_supplier(nf)
        self.assertIsNone(supplier)
        self.assertEqual(status, "Not Found")

    def test_invoice_type_routes_to_international(self):
        nf = MagicMock()
        nf.document_type = "Invoice"
        nf.vendor_name = "GitHub"
        # pluck="name" returns flat list
        frappe.get_all.return_value = ["GitHub Inc"]

        supplier, status, msg = self.manager.process_nf_supplier(nf)
        self.assertEqual(supplier, "GitHub Inc")


if __name__ == "__main__":
    unittest.main()
