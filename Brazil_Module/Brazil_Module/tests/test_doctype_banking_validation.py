"""Tests for banking DocType controller validation."""

import unittest
from unittest.mock import MagicMock, patch
import sys
from datetime import datetime, timezone

# Ensure frappe mock is in place
if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    _fm = MagicMock()
    _fm._ = lambda x: x
    sys.modules["frappe"] = _fm
    sys.modules["frappe.utils"] = _fm.utils

frappe = sys.modules["frappe"]

# Ensure frappe.model.document.Document is a real base class
_Document = type("Document", (), {"__init__": lambda self, *a, **kw: None})
sys.modules.setdefault("frappe.model.document", MagicMock())
sys.modules["frappe.model.document"].Document = _Document

# Mock requests and cryptography before imports
sys.modules.setdefault("requests", MagicMock())
sys.modules.setdefault("cryptography", MagicMock())
sys.modules.setdefault("cryptography.x509", MagicMock())
sys.modules.setdefault("cryptography.hazmat", MagicMock())
sys.modules.setdefault("cryptography.hazmat.primitives", MagicMock())


def _reset():
    frappe.reset_mock()
    frappe._ = lambda x: x
    frappe.throw.side_effect = None
    frappe.msgprint.side_effect = None
    frappe.db.get_single_value.side_effect = None
    frappe.db.set_value.side_effect = None
    frappe.log_error.side_effect = None


_NOW = "2024-01-15 12:00:00"

# Import controllers
from Brazil_Module.bancos.doctype.inter_company_account.inter_company_account import InterCompanyAccount
from Brazil_Module.bancos.doctype.inter_payment_order.inter_payment_order import InterPaymentOrder
from Brazil_Module.bancos.doctype.inter_boleto.inter_boleto import InterBoleto
from Brazil_Module.bancos.doctype.inter_pix_charge.inter_pix_charge import InterPIXCharge
from Brazil_Module.bancos.doctype.banco_inter_settings.banco_inter_settings import BancoInterSettings
from Brazil_Module.bancos.doctype.inter_api_log.inter_api_log import InterAPILog
from Brazil_Module.bancos.doctype.inter_sync_log.inter_sync_log import InterSyncLog
from Brazil_Module.bancos.doctype.inter_webhook_log.inter_webhook_log import InterWebhookLog


# ---------------------------------------------------------------------------
# InterCompanyAccount
# ---------------------------------------------------------------------------
class TestInterCompanyAccountValidation(unittest.TestCase):

    def setUp(self):
        _reset()

    def tearDown(self):
        _reset()

    def _make_account(self, **overrides):
        a = InterCompanyAccount.__new__(InterCompanyAccount)
        a.cnpj = None
        a.certificate_file = None
        a.key_file = None
        a.certificate_valid = 0
        a.certificate_expiry = None
        a.environment = None
        for k, v in overrides.items():
            setattr(a, k, v)
        return a

    def test_cnpj_must_be_14_digits(self):
        """CNPJ with non-14 digits throws."""
        frappe.throw.side_effect = Exception("thrown")
        a = self._make_account(cnpj="1234567")
        with self.assertRaises(Exception):
            a.validate()
        frappe.throw.assert_called_once()

    def test_valid_cnpj_cleaned(self):
        """CNPJ is cleaned of formatting characters."""
        a = self._make_account(cnpj="12.345.678/0001-95")
        a.validate()
        self.assertEqual(a.cnpj, "12345678000195")

    def test_no_cert_marks_invalid(self):
        """Missing certificate files mark certificate_valid = 0."""
        a = self._make_account(cnpj=None, certificate_file=None, key_file=None)
        a.validate()
        self.assertEqual(a.certificate_valid, 0)

    def test_expired_cert_marks_invalid(self):
        """Expired certificate sets certificate_valid = 0."""
        a = self._make_account(
            cnpj=None,
            certificate_file="/files/cert.pem",
            key_file="/files/key.pem",
        )
        # Mock file existence and cert loading
        mock_cert = MagicMock()
        past = datetime(2020, 1, 1, tzinfo=timezone.utc)
        mock_cert.not_valid_after_utc = past

        with patch("Brazil_Module.bancos.doctype.inter_company_account.inter_company_account.os.path.exists", return_value=True):
            with patch("Brazil_Module.services.banking.auth_manager.resolve_frappe_file_path", side_effect=lambda x: x):
                with patch("builtins.open", MagicMock()):
                    with patch("cryptography.x509.load_pem_x509_certificate", return_value=mock_cert):
                        a.validate()
        self.assertEqual(a.certificate_valid, 0)

    def test_environment_fallback_to_global(self):
        """get_environment falls back to global setting."""
        a = self._make_account(environment=None)
        frappe.db.get_single_value.return_value = "Production"
        self.assertEqual(a.get_environment(), "Production")


# ---------------------------------------------------------------------------
# InterPaymentOrder
# ---------------------------------------------------------------------------
class TestInterPaymentOrderValidation(unittest.TestCase):

    def setUp(self):
        _reset()

    def tearDown(self):
        _reset()

    def _make_order(self, **overrides):
        o = InterPaymentOrder.__new__(InterPaymentOrder)
        o.amount = 100.0
        o.payment_type = "PIX"
        o.pix_key = "email@test.com"
        o.recipient_bank_code = None
        o.recipient_agency = None
        o.recipient_account = None
        o.barcode = None
        for k, v in overrides.items():
            setattr(o, k, v)
        return o

    def test_amount_must_be_positive(self):
        """Zero or negative amount throws."""
        frappe.throw.side_effect = Exception("thrown")
        o = self._make_order(amount=-10)
        with self.assertRaises(Exception):
            o.validate()
        frappe.throw.assert_called_once()

    def test_pix_requires_pix_key(self):
        """PIX payment without pix_key throws."""
        frappe.throw.side_effect = Exception("thrown")
        o = self._make_order(payment_type="PIX", pix_key=None)
        with self.assertRaises(Exception):
            o.validate()
        frappe.throw.assert_called_once()

    def test_ted_requires_bank_code(self):
        """TED without bank code throws."""
        frappe.throw.side_effect = Exception("thrown")
        o = self._make_order(
            payment_type="TED",
            pix_key=None,
            recipient_bank_code=None,
            recipient_agency="0001",
            recipient_account="123456",
        )
        with self.assertRaises(Exception):
            o.validate()
        frappe.throw.assert_called_once()

    def test_ted_requires_agency(self):
        """TED without agency throws."""
        frappe.throw.side_effect = Exception("thrown")
        o = self._make_order(
            payment_type="TED",
            pix_key=None,
            recipient_bank_code="077",
            recipient_agency=None,
            recipient_account="123456",
        )
        with self.assertRaises(Exception):
            o.validate()
        frappe.throw.assert_called_once()

    def test_boleto_payment_requires_barcode(self):
        """Boleto Payment without barcode throws."""
        frappe.throw.side_effect = Exception("thrown")
        o = self._make_order(payment_type="Boleto Payment", pix_key=None, barcode=None)
        with self.assertRaises(Exception):
            o.validate()
        frappe.throw.assert_called_once()

    def test_valid_pix_passes(self):
        """Valid PIX order passes validation."""
        o = self._make_order(payment_type="PIX", pix_key="email@test.com", amount=500)
        o.validate()
        frappe.throw.assert_not_called()


# ---------------------------------------------------------------------------
# InterBoleto
# ---------------------------------------------------------------------------
class TestInterBoletoValidation(unittest.TestCase):

    def setUp(self):
        _reset()

    def tearDown(self):
        _reset()

    def _make_boleto(self, **overrides):
        b = InterBoleto.__new__(InterBoleto)
        b.valor_nominal = 100.0
        b.data_vencimento = None
        b.data_emissao = None
        b.status = "Pending"
        b.indicator_color = None
        for k, v in overrides.items():
            setattr(b, k, v)
        return b

    def test_valor_must_be_positive(self):
        """Zero or negative valor throws."""
        frappe.throw.side_effect = Exception("thrown")
        b = self._make_boleto(valor_nominal=-5)
        with self.assertRaises(Exception):
            b.validate()
        frappe.throw.assert_called_once()

    def test_due_date_before_issue_throws(self):
        """Due date before issue date throws."""
        frappe.throw.side_effect = Exception("thrown")
        b = self._make_boleto(data_emissao="2024-01-15", data_vencimento="2024-01-10")
        with self.assertRaises(Exception):
            b.validate()
        frappe.throw.assert_called_once()

    def test_status_color_mapping(self):
        """before_save sets correct indicator color."""
        b = self._make_boleto(status="Paid")
        b.before_save()
        self.assertEqual(b.indicator_color, "green")

        b.status = "Overdue"
        b.before_save()
        self.assertEqual(b.indicator_color, "orange")

        b.status = "Registered"
        b.before_save()
        self.assertEqual(b.indicator_color, "blue")


# ---------------------------------------------------------------------------
# InterPIXCharge
# ---------------------------------------------------------------------------
class TestInterPIXChargeValidation(unittest.TestCase):

    def setUp(self):
        _reset()

    def tearDown(self):
        _reset()

    def _make_charge(self, **overrides):
        c = InterPIXCharge.__new__(InterPIXCharge)
        c.valor = 100.0
        for k, v in overrides.items():
            setattr(c, k, v)
        return c

    def test_valor_must_be_positive(self):
        """Zero or negative valor throws."""
        frappe.throw.side_effect = Exception("thrown")
        c = self._make_charge(valor=-1)
        with self.assertRaises(Exception):
            c.validate()
        frappe.throw.assert_called_once()

    def test_valid_charge_passes(self):
        """Valid charge passes validation."""
        c = self._make_charge(valor=500)
        c.validate()
        frappe.throw.assert_not_called()


# ---------------------------------------------------------------------------
# BancoInterSettings
# ---------------------------------------------------------------------------
class TestBancoInterSettingsValidation(unittest.TestCase):

    def setUp(self):
        _reset()

    def tearDown(self):
        _reset()

    def _make_settings(self, **overrides):
        s = BancoInterSettings.__new__(BancoInterSettings)
        s.sync_interval_hours = 6
        s.sync_days_back = 7
        s.pix_expiration_seconds = 3600
        for k, v in overrides.items():
            setattr(s, k, v)
        return s

    def test_sync_interval_minimum_1_hour(self):
        """Sync interval below 1 hour throws."""
        frappe.throw.side_effect = Exception("thrown")
        s = self._make_settings(sync_interval_hours=0.5)
        with self.assertRaises(Exception):
            s.validate()
        frappe.throw.assert_called_once()

    def test_sync_days_minimum_1(self):
        """Sync days back below 1 throws."""
        frappe.throw.side_effect = Exception("thrown")
        s = self._make_settings(sync_days_back=-1)
        with self.assertRaises(Exception):
            s.validate()
        frappe.throw.assert_called_once()

    def test_pix_expiration_minimum_60(self):
        """PIX expiration below 60 seconds throws."""
        frappe.throw.side_effect = Exception("thrown")
        s = self._make_settings(pix_expiration_seconds=30)
        with self.assertRaises(Exception):
            s.validate()
        frappe.throw.assert_called_once()

    def test_valid_settings_pass(self):
        """Valid settings pass validation."""
        s = self._make_settings()
        s.validate()
        frappe.throw.assert_not_called()


# ---------------------------------------------------------------------------
# Log DocTypes (new validation)
# ---------------------------------------------------------------------------
class TestLogValidation(unittest.TestCase):

    def setUp(self):
        _reset()

    def tearDown(self):
        _reset()

    def test_api_log_defaults_timestamp(self):
        """InterAPILog.before_insert sets timestamp."""
        log = InterAPILog.__new__(InterAPILog)
        log.timestamp = None
        log.response_body = None
        with patch("Brazil_Module.bancos.doctype.inter_api_log.inter_api_log.now_datetime", return_value=_NOW):
            log.before_insert()
        self.assertEqual(log.timestamp, _NOW)

    def test_api_log_truncates_response(self):
        """InterAPILog.before_insert truncates long response_body."""
        log = InterAPILog.__new__(InterAPILog)
        log.timestamp = _NOW
        log.response_body = "x" * 6000
        log.before_insert()
        self.assertTrue(len(log.response_body) < 6000)
        self.assertTrue(log.response_body.endswith("... [truncated]"))

    def test_sync_log_defaults_started_at(self):
        """InterSyncLog.before_insert sets started_at."""
        log = InterSyncLog.__new__(InterSyncLog)
        log.started_at = None
        with patch("Brazil_Module.bancos.doctype.inter_sync_log.inter_sync_log.now_datetime", return_value=_NOW):
            log.before_insert()
        self.assertEqual(log.started_at, _NOW)

    def test_webhook_log_defaults_received_at(self):
        """InterWebhookLog.before_insert sets received_at."""
        log = InterWebhookLog.__new__(InterWebhookLog)
        log.received_at = None
        log.request_body = None
        with patch("Brazil_Module.bancos.doctype.inter_webhook_log.inter_webhook_log.now_datetime", return_value=_NOW):
            log.before_insert()
        self.assertEqual(log.received_at, _NOW)

    def test_webhook_log_truncates_request_body(self):
        """InterWebhookLog.before_insert truncates long request_body."""
        log = InterWebhookLog.__new__(InterWebhookLog)
        log.received_at = _NOW
        log.request_body = "y" * 12000
        log.before_insert()
        self.assertTrue(len(log.request_body) < 12000)
        self.assertTrue(log.request_body.endswith("... [truncated]"))


if __name__ == "__main__":
    unittest.main()
