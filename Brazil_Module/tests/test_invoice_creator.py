"""Tests for invoice creation service."""

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
from Brazil_Module.services.fiscal.invoice_creator import InvoiceCreator
import Brazil_Module.services.fiscal.invoice_creator as _ic_mod


class TestFindExistingInvoice(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()
        # Patch module-level imports so flt/add_days/getdate work properly
        _ic_mod.flt = float
        _ic_mod.add_days = lambda d, n: d
        _ic_mod.getdate = lambda x: x
        self.settings = MagicMock()
        frappe.get_single.return_value = self.settings
        self.creator = InvoiceCreator()

    def test_find_by_chave_de_acesso(self):
        nf = MagicMock()
        nf.chave_de_acesso = "35220612345678000155550010000000011000000019"
        nf.document_type = "NF-e"
        frappe.db.get_value.return_value = "PINV-001"
        result = self.creator.find_existing_invoice(nf)
        self.assertEqual(result, "PINV-001")

    def test_not_found(self):
        nf = MagicMock()
        nf.chave_de_acesso = ""
        nf.emitente_cnpj = ""
        nf.supplier = ""
        nf.numero = ""
        nf.document_type = "NF-e"
        frappe.db.get_value.return_value = None
        frappe.db.sql.return_value = []
        result = self.creator.find_existing_invoice(nf)
        self.assertIsNone(result)

    def test_international_invoice_routing(self):
        nf = MagicMock()
        nf.document_type = "Invoice"
        nf.numero = "INV-123"
        nf.supplier = "GitHub Inc"
        frappe.db.get_value.return_value = "PINV-INTL-001"
        result = self.creator.find_existing_invoice(nf)
        self.assertEqual(result, "PINV-INTL-001")


class TestCreatePurchaseInvoice(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()
        # Patch module-level imports so flt/add_days/getdate work properly
        _ic_mod.flt = float
        _ic_mod.add_days = lambda d, n: d
        _ic_mod.getdate = lambda x: x
        self.settings = MagicMock()
        self.settings.auto_create_invoice = True
        frappe.get_single.return_value = self.settings
        frappe.utils.today.return_value = "2024-01-20"
        self.creator = InvoiceCreator()

    def test_no_supplier_throws(self):
        nf = MagicMock()
        nf.supplier = ""
        nf.document_type = "NF-e"
        frappe.throw = MagicMock(side_effect=Exception("No supplier"))
        with self.assertRaises(Exception):
            self.creator.create_purchase_invoice(nf)

    def test_creates_new_invoice(self):
        nf = MagicMock()
        nf.supplier = "Test Supplier"
        nf.document_type = "NF-e"
        nf.chave_de_acesso = "12345"
        nf.numero = "1"
        nf.data_emissao = date(2024, 1, 15)
        nf.valor_total = 1000
        nf.items = []
        nf.purchase_order = ""
        nf.name = "NF-001"

        frappe.db.get_value.return_value = None
        frappe.db.sql.return_value = []

        new_invoice = MagicMock()
        new_invoice.name = "PINV-001"
        frappe.new_doc.return_value = new_invoice

        result = self.creator.create_purchase_invoice(nf, submit=False)
        frappe.new_doc.assert_called_with("Purchase Invoice")

    def test_links_existing_when_found(self):
        nf = MagicMock()
        nf.supplier = "Test Supplier"
        nf.document_type = "NF-e"
        nf.chave_de_acesso = "35220612345678000155550010000000011000000019"
        nf.name = "NF-001"

        frappe.db.get_value.return_value = "PINV-EXISTING"

        result = self.creator.create_purchase_invoice(nf, check_existing=True)
        self.assertEqual(result, "PINV-EXISTING")


class TestLinkExistingInvoice(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()
        self.settings = MagicMock()
        frappe.get_single.return_value = self.settings
        self.creator = InvoiceCreator()

    def test_link_updates_nf_document(self):
        nf = MagicMock()
        nf.name = "NF-001"
        nf.chave_de_acesso = "12345678"
        self.creator.link_existing_invoice(nf, "PINV-001")
        self.assertEqual(nf.purchase_invoice, "PINV-001")
        self.assertEqual(nf.invoice_status, "Linked")


if __name__ == "__main__":
    unittest.main()
