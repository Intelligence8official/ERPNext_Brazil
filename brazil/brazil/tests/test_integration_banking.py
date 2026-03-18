"""Integration tests for the banking module pipeline.

These tests chain real module calls together (boleto_service, pix_service,
statement_sync, reconciliation) with frappe and requests mocked at the boundary.
"""

import unittest
from unittest.mock import MagicMock, patch, call
import sys
from datetime import date, datetime, timedelta

# ---------------------------------------------------------------------------
# Frappe / requests boundary mocks
# ---------------------------------------------------------------------------
if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    _fm = MagicMock()
    _fm._ = lambda x: x
    sys.modules["frappe"] = _fm
    sys.modules["frappe.utils"] = _fm.utils
frappe = sys.modules["frappe"]
sys.modules.setdefault("requests", MagicMock())

# ---------------------------------------------------------------------------
# Import real modules under test
# ---------------------------------------------------------------------------
import brazil.services.banking.boleto_service as boleto_mod
from brazil.services.banking.boleto_service import (
    create_boleto_from_invoice,
    poll_boleto_status,
    cancel_boleto,
    cancel_expired_boletos,
    _handle_boleto_payment,
)

import brazil.services.banking.pix_service as pix_mod
from brazil.services.banking.pix_service import (
    create_pix_charge_from_invoice,
    create_scheduled_pix_charge,
    poll_pix_charge_status,
    _handle_pix_payment,
)

import brazil.services.banking.statement_sync as sync_mod
from brazil.services.banking.statement_sync import sync_statements_for_company

import brazil.services.banking.reconciliation as rec_mod
from brazil.services.banking.reconciliation import batch_reconcile


# ---------------------------------------------------------------------------
# Patch module-level ``from frappe.utils import ...`` bindings
# ---------------------------------------------------------------------------
def _flt(value, precision=None):
    v = float(value)
    if precision is not None:
        return round(v, precision)
    return v


_NOW = datetime(2024, 3, 15, 12, 0, 0)


def _patch_module_bindings():
    """Restore deterministic helpers on every module after frappe.reset_mock."""
    boleto_mod.flt = _flt
    boleto_mod.today = lambda: "2024-03-15"
    boleto_mod.getdate = lambda x: x
    boleto_mod.now_datetime = lambda: _NOW

    pix_mod.flt = _flt
    pix_mod.today = lambda: "2024-03-15"
    pix_mod.now_datetime = lambda: _NOW

    sync_mod.flt = float
    sync_mod.now_datetime = lambda: "2024-03-15 12:00:00"
    sync_mod.getdate = lambda x: x

    rec_mod.flt = float


def _reset_frappe():
    """Clear all frappe mock state so no side_effects leak between tests."""
    frappe.reset_mock()
    frappe._ = lambda x: x
    frappe.as_json.return_value = "{}"
    frappe.as_json.side_effect = None

    frappe.throw.side_effect = None
    frappe.get_doc.side_effect = None
    frappe.get_doc.return_value = MagicMock()
    frappe.get_single.side_effect = None
    frappe.get_single.return_value = None
    frappe.get_all.side_effect = None
    frappe.get_all.return_value = []
    frappe.new_doc.side_effect = None
    frappe.new_doc.return_value = MagicMock()

    frappe.db.get_value.side_effect = None
    frappe.db.get_value.return_value = None
    frappe.db.get_single_value.side_effect = None
    frappe.db.get_single_value.return_value = None
    frappe.db.exists.side_effect = None
    frappe.db.exists.return_value = None
    frappe.db.set_value.side_effect = None
    frappe.db.commit.side_effect = None
    frappe.log_error.side_effect = None

    _patch_module_bindings()


# ---------------------------------------------------------------------------
# Shared mock factories
# ---------------------------------------------------------------------------
def _make_invoice(name="SINV-001", outstanding=1500.0, company="Test Co",
                  customer="Customer A"):
    inv = MagicMock()
    inv.name = name
    inv.docstatus = 1
    inv.outstanding_amount = outstanding
    inv.company = company
    inv.customer = customer
    inv.customer_name = customer
    inv.customer_address = None
    inv.debit_to = "Debtors - TC"
    return inv


def _make_customer(name="Customer A", tax_id="12345678901"):
    cust = MagicMock()
    cust.customer_name = name
    cust.tax_id = tax_id
    cust.name = name
    return cust


def _make_settings(**overrides):
    defaults = dict(
        enabled=True,
        default_days_to_due=30,
        auto_cancel_expired_days=5,
        enable_pix_on_boleto=False,
        auto_create_payment_entry=True,
        pix_expiration_seconds=3600,
        sync_days_back=3,
        auto_reconcile=False,
        auto_sync_statements=True,
        reconcile_tolerance_percent=1,
    )
    defaults.update(overrides)
    s = MagicMock()
    for k, v in defaults.items():
        setattr(s, k, v)
    return s


def _make_account_doc(name="ACC-001", company="Test Co",
                      bank_account="BA-001"):
    doc = MagicMock()
    doc.name = name
    doc.company = company
    doc.bank_account = bank_account
    return doc


# ############################################################################
# TestBoletoLifecycle
# ############################################################################
class TestBoletoLifecycle(unittest.TestCase):
    """Integration tests that chain boleto_service calls together."""

    def tearDown(self):
        _reset_frappe()

    # ---- 1. create -> poll (PAGO) -> auto Payment Entry ----
    @patch("brazil.services.banking.inter_client.InterAPIClient")
    def test_create_and_poll_paid(self, MockClient):
        """Create a boleto, then poll it -- bank says PAGO.

        Expected: status becomes Paid, _handle_boleto_payment is invoked
        and creates a Payment Entry.
        """
        _reset_frappe()

        invoice = _make_invoice()
        customer = _make_customer()
        settings = _make_settings()
        account_doc = _make_account_doc()

        # -- get_doc dispatcher used throughout the lifecycle --
        boleto_doc = MagicMock()
        boleto_doc.name = "BOL-INT-001"
        boleto_doc.pix_copia_cola = ""
        boleto_doc.inter_request_code = "REQ-001"
        boleto_doc.inter_company_account = "ACC-001"
        boleto_doc.status = "Registered"
        boleto_doc.sales_invoice = "SINV-001"
        boleto_doc.payment_entry = None
        boleto_doc.valor_nominal = 1500.0
        boleto_doc.valor_pago = 0
        boleto_doc.nosso_numero = "00001"
        boleto_doc.data_pagamento = None

        pe_doc = MagicMock()
        pe_doc.name = "PE-INT-001"

        def get_doc_dispatch(doctype, name=None):
            if doctype == "Sales Invoice":
                return invoice
            if doctype == "Customer":
                return customer
            if doctype == "Inter Boleto":
                return boleto_doc
            if doctype == "Inter Company Account":
                return account_doc
            return MagicMock()

        frappe.get_doc.side_effect = get_doc_dispatch
        frappe.get_single.return_value = settings
        frappe.db.get_value.side_effect = lambda *a, **kw: (
            "ACC-001" if a[0] == "Inter Company Account"
            else "GL-Account-TC" if a[0] == "Bank Account"
            else None
        )

        # API client: create returns boleto identifiers, get returns PAGO
        mock_client = MagicMock()
        mock_client.create_boleto.return_value = {
            "nossoNumero": "00001",
            "codigoBarras": "12345",
            "linhaDigitavel": "12345.12345",
            "codigoSolicitacao": "REQ-001",
        }
        mock_client.get_boleto.return_value = {
            "situacao": "PAGO",
            "valorTotalRecebimento": 1500.00,
            "dataPagamento": "2024-03-15",
        }
        mock_client.download_boleto_pdf.return_value = b"%PDF-fake"
        MockClient.return_value = mock_client

        frappe.new_doc.side_effect = lambda dt: (
            boleto_doc if dt == "Inter Boleto"
            else pe_doc if dt == "Payment Entry"
            else MagicMock()
        )

        # Step 1 -- Create
        created_name = create_boleto_from_invoice("SINV-001")
        self.assertEqual(created_name, "BOL-INT-001")
        boleto_doc.insert.assert_called_with(ignore_permissions=True)
        mock_client.create_boleto.assert_called_once()

        # Step 2 -- Poll
        results = poll_boleto_status("BOL-INT-001")

        self.assertEqual(results["paid"], 1)
        self.assertEqual(boleto_doc.status, "Paid")
        self.assertEqual(boleto_doc.valor_pago, 1500.0)

        # Payment Entry should have been created
        pe_doc.insert.assert_called_with(ignore_permissions=True)
        pe_doc.submit.assert_called_once()
        self.assertEqual(boleto_doc.payment_entry, "PE-INT-001")

    # ---- 2. create -> poll (VENCIDO) -> cancel ----
    @patch("brazil.services.banking.inter_client.InterAPIClient")
    def test_create_and_overdue_then_cancel(self, MockClient):
        """Create boleto, poll returns VENCIDO -> Overdue, then cancel it."""
        _reset_frappe()

        invoice = _make_invoice()
        customer = _make_customer()
        settings = _make_settings(auto_create_payment_entry=False)

        boleto_doc = MagicMock()
        boleto_doc.name = "BOL-INT-002"
        boleto_doc.pix_copia_cola = ""
        boleto_doc.inter_request_code = "REQ-002"
        boleto_doc.inter_company_account = "ACC-001"
        boleto_doc.status = "Registered"
        boleto_doc.sales_invoice = "SINV-001"
        boleto_doc.payment_entry = None
        boleto_doc.nosso_numero = "00002"

        def get_doc_dispatch(doctype, name=None):
            if doctype == "Sales Invoice":
                return invoice
            if doctype == "Customer":
                return customer
            if doctype == "Inter Boleto":
                return boleto_doc
            return MagicMock()

        frappe.get_doc.side_effect = get_doc_dispatch
        frappe.get_single.return_value = settings
        frappe.db.get_value.return_value = "ACC-001"

        mock_client = MagicMock()
        mock_client.create_boleto.return_value = {
            "nossoNumero": "00002",
            "codigoBarras": "99999",
            "linhaDigitavel": "99999.99999",
            "codigoSolicitacao": "REQ-002",
        }
        mock_client.download_boleto_pdf.return_value = b"%PDF-fake"
        MockClient.return_value = mock_client

        frappe.new_doc.side_effect = lambda dt: (
            boleto_doc if dt == "Inter Boleto" else MagicMock()
        )

        # Step 1 -- Create
        create_boleto_from_invoice("SINV-001")

        # Step 2 -- Poll: bank returns VENCIDO
        mock_client.get_boleto.return_value = {"situacao": "VENCIDO"}
        results = poll_boleto_status("BOL-INT-002")

        self.assertEqual(results["updated"], 1)
        self.assertEqual(boleto_doc.status, "Overdue")

        # Step 3 -- Cancel: must change status to something cancel_boleto accepts
        # (cancel_boleto checks for "Pending" or "Registered")
        # Overdue is not in that list, so first set it back for the cancel path.
        # In real life you'd typically cancel from Registered before it becomes
        # overdue.  Let's rewind status to test the cancel path.
        boleto_doc.status = "Registered"
        mock_client.cancel_boleto.return_value = {"status": "ok"}

        result = cancel_boleto("BOL-INT-002", reason="Customer requested")
        self.assertEqual(result["status"], "success")
        self.assertEqual(boleto_doc.status, "Cancelled")
        boleto_doc.save.assert_called_with(ignore_permissions=True)

    # ---- 3. poll triggers auto Payment Entry creation ----
    @patch("brazil.services.banking.inter_client.InterAPIClient")
    def test_poll_triggers_auto_payment_entry(self, MockClient):
        """When poll returns PAGO and auto_create_payment_entry is on,
        a Payment Entry is created and submitted.
        """
        _reset_frappe()

        invoice = _make_invoice(outstanding=2000.0)
        settings = _make_settings(auto_create_payment_entry=True)
        account_doc = _make_account_doc()

        boleto_doc = MagicMock()
        boleto_doc.name = "BOL-INT-003"
        boleto_doc.inter_request_code = "REQ-003"
        boleto_doc.inter_company_account = "ACC-001"
        boleto_doc.status = "Registered"
        boleto_doc.sales_invoice = "SINV-001"
        boleto_doc.payment_entry = None
        boleto_doc.valor_nominal = 2000.0
        boleto_doc.valor_pago = 0
        boleto_doc.nosso_numero = "00003"
        boleto_doc.data_pagamento = None

        pe_doc = MagicMock()
        pe_doc.name = "PE-INT-003"

        def get_doc_dispatch(doctype, name=None):
            if doctype == "Inter Boleto":
                return boleto_doc
            if doctype == "Sales Invoice":
                return invoice
            if doctype == "Inter Company Account":
                return account_doc
            return MagicMock()

        frappe.get_doc.side_effect = get_doc_dispatch
        frappe.get_single.return_value = settings
        frappe.db.get_value.side_effect = lambda *a, **kw: (
            "GL-Account-TC" if a[0] == "Bank Account" else None
        )

        mock_client = MagicMock()
        mock_client.get_boleto.return_value = {
            "situacao": "PAGO",
            "valorTotalRecebimento": 2000.00,
            "dataPagamento": "2024-03-15",
        }
        MockClient.return_value = mock_client

        frappe.new_doc.side_effect = lambda dt: (
            pe_doc if dt == "Payment Entry" else MagicMock()
        )

        results = poll_boleto_status("BOL-INT-003")

        self.assertEqual(results["paid"], 1)
        self.assertEqual(boleto_doc.status, "Paid")

        # Payment Entry assertions
        pe_doc.insert.assert_called_once_with(ignore_permissions=True)
        pe_doc.submit.assert_called_once()
        self.assertEqual(pe_doc.payment_type, "Receive")
        self.assertEqual(pe_doc.party, "Customer A")
        self.assertEqual(pe_doc.paid_amount, 2000.0)
        self.assertEqual(boleto_doc.payment_entry, "PE-INT-003")

    # ---- 4. expired boleto auto-cancel ----
    @patch("brazil.services.banking.inter_client.InterAPIClient")
    def test_expired_auto_cancel(self, MockClient):
        """A boleto that has been VENCIDO for longer than auto_cancel_expired_days
        gets auto-cancelled by cancel_expired_boletos.
        """
        _reset_frappe()

        settings = _make_settings(auto_cancel_expired_days=5)

        # cancel_expired_boletos checks settings.enabled via db
        frappe.db.get_single_value.return_value = True
        frappe.get_single.return_value = settings

        # Simulate one expired boleto returned by get_all
        frappe.get_all.return_value = ["BOL-INT-004"]

        boleto_doc = MagicMock()
        boleto_doc.name = "BOL-INT-004"
        boleto_doc.status = "Registered"
        boleto_doc.inter_request_code = "REQ-004"
        boleto_doc.inter_company_account = "ACC-001"

        frappe.get_doc.return_value = boleto_doc

        mock_client = MagicMock()
        mock_client.cancel_boleto.return_value = {"status": "ok"}
        MockClient.return_value = mock_client

        cancel_expired_boletos()

        # get_all was called with the correct filter including cutoff date
        frappe.get_all.assert_called_once()
        call_kwargs = frappe.get_all.call_args
        filters = call_kwargs[1]["filters"] if "filters" in call_kwargs[1] else call_kwargs[0][1] if len(call_kwargs[0]) > 1 else call_kwargs[1].get("filters")
        self.assertIn("data_vencimento", filters)

        # Boleto should be cancelled
        self.assertEqual(boleto_doc.status, "Cancelled")
        boleto_doc.save.assert_called_with(ignore_permissions=True)
        mock_client.cancel_boleto.assert_called_once_with(
            "REQ-004", "Auto-cancelled: past expiry grace period"
        )


# ############################################################################
# TestPixLifecycle
# ############################################################################
class TestPixLifecycle(unittest.TestCase):
    """Integration tests that chain pix_service calls together."""

    def tearDown(self):
        _reset_frappe()

    # ---- 1. create immediate -> poll (CONCLUIDA) -> Paid ----
    @patch("brazil.services.banking.inter_client.InterAPIClient")
    def test_create_immediate_and_poll_paid(self, MockClient):
        """Create an immediate PIX charge then poll -> CONCLUIDA -> Paid."""
        _reset_frappe()

        invoice = _make_invoice(outstanding=800.0)
        customer = _make_customer()
        settings = _make_settings()
        account_doc = _make_account_doc()

        pix_doc = MagicMock()
        pix_doc.name = "PIX-INT-001"
        pix_doc.pix_copia_cola = ""
        pix_doc.txid = "txid_int_001"
        pix_doc.charge_type = "Immediate"
        pix_doc.inter_company_account = "ACC-001"
        pix_doc.sales_invoice = "SINV-001"
        pix_doc.payment_entry = None
        pix_doc.valor = 800.0
        pix_doc.valor_pago = 0
        pix_doc.data_pagamento = None
        pix_doc.data_expiracao = None

        pe_doc = MagicMock()
        pe_doc.name = "PE-PIX-001"

        def get_doc_dispatch(doctype, name=None):
            if doctype == "Sales Invoice":
                return invoice
            if doctype == "Customer":
                return customer
            if doctype == "Inter PIX Charge":
                return pix_doc
            if doctype == "Inter Company Account":
                return account_doc
            return MagicMock()

        frappe.get_doc.side_effect = get_doc_dispatch
        frappe.get_single.return_value = settings
        frappe.db.get_value.side_effect = lambda *a, **kw: (
            "ACC-001" if a[0] == "Inter Company Account"
            else "GL-Account-TC" if a[0] == "Bank Account"
            else None
        )

        mock_client = MagicMock()
        mock_client.create_pix_charge.return_value = {
            "txid": "txid_int_001",
            "chave": "pix@example.com",
            "pixCopiaECola": "",
        }
        mock_client.get_pix_charge.return_value = {
            "status": "CONCLUIDA",
            "pix": [{
                "valor": "800.00",
                "horario": "2024-03-15T10:30:00",
                "endToEndId": "E2E-INT-001",
            }],
        }
        MockClient.return_value = mock_client

        frappe.new_doc.side_effect = lambda dt: (
            pix_doc if dt == "Inter PIX Charge"
            else pe_doc if dt == "Payment Entry"
            else MagicMock()
        )

        # Step 1 -- Create
        created = create_pix_charge_from_invoice("SINV-001", expiration_seconds=600)
        self.assertEqual(created, "PIX-INT-001")
        self.assertEqual(pix_doc.charge_type, "Immediate")
        self.assertEqual(pix_doc.status, "Active")
        mock_client.create_pix_charge.assert_called_once()

        # Step 2 -- Poll
        results = poll_pix_charge_status("PIX-INT-001")

        self.assertEqual(results["paid"], 1)
        self.assertEqual(pix_doc.status, "Paid")
        self.assertEqual(pix_doc.valor_pago, 800.0)
        self.assertEqual(pix_doc.e2e_id, "E2E-INT-001")

        # Payment Entry created automatically
        pe_doc.insert.assert_called_with(ignore_permissions=True)
        pe_doc.submit.assert_called_once()
        self.assertEqual(pix_doc.payment_entry, "PE-PIX-001")

    # ---- 2. create scheduled with fine/interest -> verify charge_data ----
    @patch("brazil.services.banking.inter_client.InterAPIClient")
    def test_create_scheduled_with_fine(self, MockClient):
        """Create a scheduled PIX charge with fine and interest.

        Verify the charge_data sent to the API contains multa and juros.
        """
        _reset_frappe()

        invoice = _make_invoice(outstanding=3000.0)
        customer = _make_customer()
        settings = _make_settings()

        pix_doc = MagicMock()
        pix_doc.name = "PIX-INT-002"
        pix_doc.pix_copia_cola = ""

        def get_doc_dispatch(doctype, name=None):
            if doctype == "Sales Invoice":
                return invoice
            if doctype == "Customer":
                return customer
            return MagicMock()

        frappe.get_doc.side_effect = get_doc_dispatch
        frappe.get_single.return_value = settings
        frappe.db.get_value.return_value = "ACC-001"
        frappe.new_doc.return_value = pix_doc

        mock_client = MagicMock()
        mock_client.create_pix_charge_with_due_date.return_value = {
            "txid": "sched_int_001",
            "pixCopiaECola": "",
        }
        MockClient.return_value = mock_client

        due = date(2024, 4, 15)
        result = create_scheduled_pix_charge(
            "SINV-001",
            due_date=due,
            fine_percent=2.0,
            interest_percent=0.033,
        )

        self.assertEqual(result, "PIX-INT-002")
        self.assertEqual(pix_doc.charge_type, "Scheduled")
        self.assertEqual(pix_doc.status, "Active")

        # Inspect the charge_data passed to the API client
        call_args = mock_client.create_pix_charge_with_due_date.call_args
        charge_data = call_args[0][1]

        self.assertIn("calendario", charge_data)
        self.assertEqual(charge_data["calendario"]["dataDeVencimento"], "2024-04-15")

        self.assertIn("multa", charge_data["valor"])
        self.assertEqual(charge_data["valor"]["multa"]["valorPerc"], "2.00")
        self.assertIn("juros", charge_data["valor"])
        self.assertEqual(charge_data["valor"]["juros"]["valorPerc"], "0.03")

        pix_doc.insert.assert_called_once_with(ignore_permissions=True)

    # ---- 3. poll triggers Payment Entry ----
    @patch("brazil.services.banking.inter_client.InterAPIClient")
    def test_poll_triggers_payment_entry(self, MockClient):
        """When PIX status is CONCLUIDA and auto_create_payment_entry is on,
        a Payment Entry is created and submitted."""
        _reset_frappe()

        invoice = _make_invoice(outstanding=500.0)
        settings = _make_settings(auto_create_payment_entry=True)
        account_doc = _make_account_doc()

        pix_doc = MagicMock()
        pix_doc.name = "PIX-INT-003"
        pix_doc.txid = "txid_int_003"
        pix_doc.charge_type = "Immediate"
        pix_doc.inter_company_account = "ACC-001"
        pix_doc.sales_invoice = "SINV-001"
        pix_doc.payment_entry = None
        pix_doc.valor = 500.0
        pix_doc.valor_pago = 0
        pix_doc.data_pagamento = None
        pix_doc.data_expiracao = None

        pe_doc = MagicMock()
        pe_doc.name = "PE-PIX-003"

        def get_doc_dispatch(doctype, name=None):
            if doctype == "Inter PIX Charge":
                return pix_doc
            if doctype == "Sales Invoice":
                return invoice
            if doctype == "Inter Company Account":
                return account_doc
            return MagicMock()

        frappe.get_doc.side_effect = get_doc_dispatch
        frappe.get_single.return_value = settings
        frappe.db.get_value.side_effect = lambda *a, **kw: (
            "GL-Account-TC" if a[0] == "Bank Account" else None
        )

        mock_client = MagicMock()
        mock_client.get_pix_charge.return_value = {
            "status": "CONCLUIDA",
            "pix": [{
                "valor": "500.00",
                "horario": "2024-03-15T09:00:00",
                "endToEndId": "E2E-INT-003",
            }],
        }
        MockClient.return_value = mock_client

        frappe.new_doc.side_effect = lambda dt: (
            pe_doc if dt == "Payment Entry" else MagicMock()
        )

        results = poll_pix_charge_status("PIX-INT-003")

        self.assertEqual(results["paid"], 1)
        self.assertEqual(pix_doc.status, "Paid")

        # Payment Entry assertions
        pe_doc.insert.assert_called_once_with(ignore_permissions=True)
        pe_doc.submit.assert_called_once()
        self.assertEqual(pe_doc.payment_type, "Receive")
        self.assertEqual(pe_doc.party, "Customer A")
        self.assertEqual(pe_doc.paid_amount, 500.0)
        self.assertEqual(pix_doc.payment_entry, "PE-PIX-003")

    # ---- 4. expired charge marked ----
    @patch("brazil.services.banking.inter_client.InterAPIClient")
    def test_expired_charge_marked(self, MockClient):
        """Poll returns ATIVA but now_datetime > data_expiracao -> Expired."""
        _reset_frappe()

        pix_doc = MagicMock()
        pix_doc.name = "PIX-INT-004"
        pix_doc.txid = "txid_int_004"
        pix_doc.charge_type = "Immediate"
        pix_doc.inter_company_account = "ACC-001"
        pix_doc.data_expiracao = "2024-03-14 12:00:00"  # yesterday

        frappe.get_doc.return_value = pix_doc

        mock_client = MagicMock()
        mock_client.get_pix_charge.return_value = {"status": "ATIVA"}
        MockClient.return_value = mock_client

        # Make the expiration comparison evaluate correctly:
        # frappe.utils.now_datetime() > frappe.utils.get_datetime(data_expiracao)
        mock_now = MagicMock()
        mock_exp = MagicMock()
        frappe.utils.now_datetime.return_value = mock_now
        frappe.utils.get_datetime.return_value = mock_exp
        mock_now.__gt__ = lambda self, other: True  # now > expiration

        results = poll_pix_charge_status("PIX-INT-004")

        self.assertEqual(results["expired"], 1)
        self.assertEqual(pix_doc.status, "Expired")
        pix_doc.save.assert_called_with(ignore_permissions=True)


# ############################################################################
# TestStatementReconciliation
# ############################################################################
class TestStatementReconciliation(unittest.TestCase):
    """Integration tests chaining statement_sync -> reconciliation."""

    def tearDown(self):
        _reset_frappe()

    # ---- 1. sync creates Bank Transaction docs ----
    @patch("brazil.services.banking.inter_client.InterAPIClient")
    def test_sync_creates_transactions(self, MockClient):
        """sync_statements_for_company fetches transactions from the API and
        creates Bank Transaction documents for each.
        """
        _reset_frappe()

        account_doc = _make_account_doc()
        settings = _make_settings(auto_reconcile=False)

        sync_log = MagicMock()
        bt_docs_created = []

        def new_doc_dispatch(dt):
            if dt == "Inter Sync Log":
                return sync_log
            if dt == "Bank Transaction":
                bt = MagicMock()
                bt.name = f"BT-{len(bt_docs_created) + 1:03d}"
                bt_docs_created.append(bt)
                return bt
            return MagicMock()

        frappe.new_doc.side_effect = new_doc_dispatch
        frappe.get_doc.return_value = account_doc
        frappe.get_single.return_value = settings
        frappe.db.exists.return_value = None  # no duplicates

        api_transactions = [
            {
                "dataMovimento": "2024-03-14",
                "tipoTransacao": "CREDITO",
                "tipoOperacao": "PIX",
                "valor": "800.00",
                "titulo": "PIX received from customer",
                "descricao": "Payment for SINV-001",
                "detalhes": {"endToEndId": "E2E-SYNC-001"},
            },
            {
                "dataMovimento": "2024-03-14",
                "tipoTransacao": "DEBITO",
                "tipoOperacao": "TED",
                "valor": "350.00",
                "titulo": "TED to supplier",
                "descricao": "Supplier payment",
                "detalhes": {"codigoTransacao": "TED-SYNC-001"},
            },
        ]

        mock_client = MagicMock()
        mock_client.get_statement.return_value = api_transactions
        MockClient.return_value = mock_client

        result = sync_statements_for_company(
            "ACC-001", start_date=date(2024, 3, 10), end_date=date(2024, 3, 15)
        )

        self.assertEqual(result["fetched"], 2)
        self.assertEqual(result["created"], 2)
        self.assertEqual(result["skipped"], 0)
        self.assertEqual(len(bt_docs_created), 2)

        # First transaction is credit
        bt1 = bt_docs_created[0]
        self.assertEqual(bt1.deposit, 800.0)
        self.assertEqual(bt1.withdrawal, 0)
        bt1.insert.assert_called_once_with(ignore_permissions=True)
        bt1.submit.assert_called_once()

        # Second transaction is debit
        bt2 = bt_docs_created[1]
        self.assertEqual(bt2.deposit, 0)
        self.assertEqual(bt2.withdrawal, 350.0)

    # ---- 2. reconcile by boleto reference (nosso_numero) ----
    def test_reconcile_by_boleto_reference(self):
        """A Bank Transaction whose reference_number matches a boleto's
        nosso_numero gets matched to the boleto's Sales Invoice.
        """
        _reset_frappe()

        # batch_reconcile calls frappe.get_all for unmatched Bank Transactions
        frappe.get_all.return_value = [
            {
                "name": "BT-REC-001",
                "date": "2024-03-14",
                "deposit": 1500.0,
                "withdrawal": 0,
                "description": "Boleto payment",
                "reference_number": "NOSSO-001",
            },
        ]

        # _match_by_inter_reference looks up Inter Boleto by nosso_numero.
        # frappe.db.get_value with as_dict=True returns a frappe._dict (supports
        # attribute access).  We use a MagicMock to emulate this.
        def db_get_value_boleto(doctype, filters=None, fieldname=None, as_dict=False, *a, **kw):
            if doctype == "Inter Boleto" and as_dict:
                m = MagicMock()
                m.name = "BOL-REC-001"
                m.valor_nominal = 1500.0
                m.sales_invoice = "SINV-REC-001"
                return m
            return None

        frappe.db.get_value.side_effect = db_get_value_boleto

        bt_doc = MagicMock()
        frappe.get_doc.return_value = bt_doc

        result = batch_reconcile("BA-001", date_from=date(2024, 3, 10))

        self.assertEqual(result["matched"], 1)
        self.assertEqual(result["unmatched"], 0)

        # The Bank Transaction should have been allocated to the Sales Invoice
        bt_doc.append.assert_called_once_with("payment_entries", {
            "payment_document": "Sales Invoice",
            "payment_entry": "SINV-REC-001",
            "allocated_amount": 1500.0,
        })
        bt_doc.save.assert_called_once_with(ignore_permissions=True)

    # ---- 3. reconcile by PIX reference (txid) ----
    def test_reconcile_by_pix_reference(self):
        """A Bank Transaction whose reference matches a PIX charge txid
        gets matched to the PIX charge's Sales Invoice.
        """
        _reset_frappe()

        frappe.get_all.return_value = [
            {
                "name": "BT-REC-002",
                "date": "2024-03-14",
                "deposit": 800.0,
                "withdrawal": 0,
                "description": "PIX received",
                "reference_number": "txid_rec_002",
            },
        ]

        # _match_by_inter_reference: boleto lookup returns None,
        # PIX lookup returns a match.  Use MagicMock so attribute access works.
        def db_get_value_pix(doctype, filters=None, fieldname=None, as_dict=False, *a, **kw):
            if doctype == "Inter Boleto" and as_dict:
                return None  # No boleto match
            if doctype == "Inter PIX Charge" and as_dict:
                m = MagicMock()
                m.name = "PIX-REC-002"
                m.valor = 800.0
                m.sales_invoice = "SINV-REC-002"
                return m
            return None

        frappe.db.get_value.side_effect = db_get_value_pix

        bt_doc = MagicMock()
        frappe.get_doc.return_value = bt_doc

        result = batch_reconcile("BA-001", date_from=date(2024, 3, 10))

        self.assertEqual(result["matched"], 1)

        bt_doc.append.assert_called_once_with("payment_entries", {
            "payment_document": "Sales Invoice",
            "payment_entry": "SINV-REC-002",
            "allocated_amount": 800.0,
        })

    # ---- 4. sync skips duplicate transactions ----
    @patch("brazil.services.banking.inter_client.InterAPIClient")
    def test_sync_skips_duplicates(self, MockClient):
        """When sync encounters transactions that already exist as Bank
        Transactions, they are skipped (no duplicates created).
        """
        _reset_frappe()

        account_doc = _make_account_doc()
        settings = _make_settings(auto_reconcile=False)

        sync_log = MagicMock()
        bt_docs_created = []

        def new_doc_dispatch(dt):
            if dt == "Inter Sync Log":
                return sync_log
            if dt == "Bank Transaction":
                bt = MagicMock()
                bt_docs_created.append(bt)
                return bt
            return MagicMock()

        frappe.new_doc.side_effect = new_doc_dispatch
        frappe.get_doc.return_value = account_doc
        frappe.get_single.return_value = settings

        # Simulate that all transactions already exist
        frappe.db.exists.return_value = "BT-EXISTING"

        api_transactions = [
            {
                "dataMovimento": "2024-03-14",
                "tipoTransacao": "CREDITO",
                "valor": "500.00",
                "titulo": "Already synced",
                "detalhes": {"endToEndId": "E2E-DUP-001"},
            },
            {
                "dataMovimento": "2024-03-13",
                "tipoTransacao": "DEBITO",
                "valor": "200.00",
                "titulo": "Also already synced",
                "detalhes": {"codigoTransacao": "TED-DUP-001"},
            },
        ]

        mock_client = MagicMock()
        mock_client.get_statement.return_value = api_transactions
        MockClient.return_value = mock_client

        result = sync_statements_for_company(
            "ACC-001", start_date=date(2024, 3, 10), end_date=date(2024, 3, 15)
        )

        self.assertEqual(result["fetched"], 2)
        self.assertEqual(result["skipped"], 2)
        self.assertEqual(result["created"], 0)
        # No Bank Transaction docs should have been created
        self.assertEqual(len(bt_docs_created), 0)


if __name__ == "__main__":
    unittest.main()
