"""Tests for the bank statement sync service."""

import unittest
from unittest.mock import MagicMock, patch
import sys
from datetime import date

# Ensure frappe mock is in place
if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    _fm = MagicMock()
    _fm._ = lambda x: x
    sys.modules["frappe"] = _fm
    sys.modules["frappe.utils"] = _fm.utils

frappe = sys.modules["frappe"]
sys.modules.setdefault("requests", MagicMock())

import brazil_module.services.banking.statement_sync as _ss_mod
from brazil_module.services.banking.statement_sync import (
    sync_statements_for_company,
    _create_bank_transaction,
    _build_reference,
    _is_duplicate_transaction,
    update_balance,
)

# Patch module-level bindings
_ss_mod.flt = float
_ss_mod.now_datetime = lambda: "2024-01-15 12:00:00"
_ss_mod.getdate = lambda x: x


def _reset():
    frappe.reset_mock()
    frappe.get_doc.side_effect = None
    frappe.get_single.side_effect = None
    frappe.new_doc.side_effect = None
    frappe.get_all.side_effect = None
    frappe.get_all.return_value = []
    frappe.db.exists.side_effect = None
    frappe.db.exists.return_value = None
    frappe.db.set_value.side_effect = None
    frappe.db.commit.side_effect = None
    frappe.db.get_single_value.side_effect = None
    frappe.as_json = lambda x: str(x)


class TestSyncStatements(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_creates_sync_log(self):
        account_doc = MagicMock()
        account_doc.company = "Test Company"
        account_doc.bank_account = "BA-001"

        settings = MagicMock()
        settings.sync_days_back = 3
        settings.auto_reconcile = False

        frappe.get_doc.return_value = account_doc
        frappe.get_single.return_value = settings

        sync_log = MagicMock()
        frappe.new_doc.return_value = sync_log

        mock_client = MagicMock()
        mock_client.get_statement.return_value = []

        with patch("brazil_module.services.banking.inter_client.InterAPIClient", return_value=mock_client):
            result = sync_statements_for_company("TEST-ACCOUNT", date(2024, 1, 1), date(2024, 1, 15))
            self.assertEqual(result["fetched"], 0)
            sync_log.insert.assert_called_once()

    def test_creates_transactions(self):
        account_doc = MagicMock()
        account_doc.company = "Test Company"
        account_doc.bank_account = "BA-001"

        settings = MagicMock()
        settings.sync_days_back = 3
        settings.auto_reconcile = False

        frappe.get_doc.return_value = account_doc
        frappe.get_single.return_value = settings

        sync_log = MagicMock()
        frappe.new_doc.return_value = sync_log

        transactions = [
            {"dataMovimento": "2024-01-15", "tipoTransacao": "CREDITO", "valor": "500.00", "titulo": "PIX received"},
            {"dataMovimento": "2024-01-14", "tipoTransacao": "DEBITO", "valor": "200.00", "titulo": "TED sent"},
        ]

        mock_client = MagicMock()
        mock_client.get_statement.return_value = transactions

        with patch("brazil_module.services.banking.inter_client.InterAPIClient", return_value=mock_client):
            with patch.object(_ss_mod, "_is_duplicate_transaction", return_value=False):
                with patch.object(_ss_mod, "_create_bank_transaction"):
                    result = sync_statements_for_company("TEST-ACCOUNT", date(2024, 1, 1), date(2024, 1, 15))
                    self.assertEqual(result["fetched"], 2)
                    self.assertEqual(result["created"], 2)

    def test_skips_duplicates(self):
        account_doc = MagicMock()
        account_doc.company = "Test Company"
        account_doc.bank_account = "BA-001"

        settings = MagicMock()
        settings.sync_days_back = 3
        settings.auto_reconcile = False

        frappe.get_doc.return_value = account_doc
        frappe.get_single.return_value = settings

        sync_log = MagicMock()
        frappe.new_doc.return_value = sync_log

        mock_client = MagicMock()
        mock_client.get_statement.return_value = [
            {"dataMovimento": "2024-01-15", "tipoTransacao": "CREDITO", "valor": "500.00", "titulo": "Existing"},
        ]

        with patch("brazil_module.services.banking.inter_client.InterAPIClient", return_value=mock_client):
            with patch.object(_ss_mod, "_is_duplicate_transaction", return_value=True):
                result = sync_statements_for_company("TEST-ACCOUNT", date(2024, 1, 1), date(2024, 1, 15))
                self.assertEqual(result["skipped"], 1)
                self.assertEqual(result["created"], 0)

    def test_handles_api_failure(self):
        account_doc = MagicMock()
        account_doc.company = "Test Company"
        account_doc.bank_account = "BA-001"

        settings = MagicMock()
        settings.sync_days_back = 3

        frappe.get_doc.return_value = account_doc
        frappe.get_single.return_value = settings

        sync_log = MagicMock()
        frappe.new_doc.return_value = sync_log

        mock_client = MagicMock()
        mock_client.get_statement.side_effect = Exception("API timeout")

        with patch("brazil_module.services.banking.inter_client.InterAPIClient", return_value=mock_client):
            with self.assertRaises(Exception):
                sync_statements_for_company("TEST-ACCOUNT", date(2024, 1, 1), date(2024, 1, 15))

            # Sync log should be marked as Failed
            self.assertEqual(sync_log.status, "Failed")


class TestCreateBankTransaction(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_credit_transaction(self):
        txn_data = {
            "dataEntrada": "2024-01-15",
            "tipoTransacao": "PIX",
            "valor": "500.00",
            "titulo": "Pix recebido",
            "descricao": "PIX RECEBIDO - Payment from customer",
            "tipoOperacao": "C",
        }

        account_doc = MagicMock()
        account_doc.bank_account = "BA-001"
        account_doc.company = "Test Company"

        bt = MagicMock()
        frappe.new_doc.return_value = bt

        _create_bank_transaction(txn_data, account_doc)

        self.assertEqual(bt.deposit, 500.0)
        self.assertEqual(bt.withdrawal, 0)
        bt.insert.assert_called_once()
        bt.submit.assert_called_once()

    def test_debit_transaction(self):
        txn_data = {
            "dataEntrada": "2024-01-14",
            "tipoTransacao": "PAGAMENTO",
            "valor": "200.00",
            "titulo": "Pagamento efetuado",
            "descricao": "PAGAMENTO DE TITULO",
            "tipoOperacao": "D",
        }

        account_doc = MagicMock()
        account_doc.bank_account = "BA-001"
        account_doc.company = "Test Company"

        bt = MagicMock()
        frappe.new_doc.return_value = bt

        _create_bank_transaction(txn_data, account_doc)

        self.assertEqual(bt.deposit, 0)
        self.assertEqual(bt.withdrawal, 200.0)

    def test_builds_description(self):
        txn_data = {
            "dataMovimento": "2024-01-15",
            "tipoTransacao": "CREDITO",
            "valor": "100.00",
            "titulo": "Payment",
            "descricao": "Monthly service",
            "tipoOperacao": "PIX",
        }

        account_doc = MagicMock()
        account_doc.bank_account = "BA-001"
        account_doc.company = "Test Company"

        bt = MagicMock()
        frappe.new_doc.return_value = bt

        _create_bank_transaction(txn_data, account_doc)

        self.assertIn("Payment", bt.description)
        self.assertIn("Monthly service", bt.description)


class TestBuildReference(unittest.TestCase):
    def test_e2e_id_from_detalhes(self):
        txn_data = {
            "detalhes": {"endToEndId": "E2E123456789"},
            "dataMovimento": "2024-01-15",
        }
        result = _build_reference(txn_data)
        self.assertEqual(result, "E2E123456789")

    def test_fallback_composite(self):
        txn_data = {
            "dataMovimento": "2024-01-15",
            "tipoOperacao": "PIX",
            "valor": "500.00",
            "titulo": "Payment received",
        }
        result = _build_reference(txn_data)
        self.assertIn("2024-01-15", result)
        self.assertIn("PIX", result)
        self.assertIn("500.00", result)


class TestUpdateBalance(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_fetches_and_stores_balance(self):
        mock_client = MagicMock()
        mock_client.get_balance.return_value = {"disponivel": 12345.67}

        with patch("brazil_module.services.banking.inter_client.InterAPIClient", return_value=mock_client):
            result = update_balance("TEST-ACCOUNT")
            self.assertAlmostEqual(result, 12345.67)
            frappe.db.set_value.assert_called_once()
            frappe.db.commit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
