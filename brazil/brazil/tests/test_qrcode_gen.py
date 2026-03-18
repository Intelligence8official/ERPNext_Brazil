"""Tests for QR code generation utility."""

import unittest
from unittest.mock import MagicMock, patch
import sys

# Ensure frappe mock is in place before import
if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    frappe_mock = MagicMock()
    sys.modules["frappe"] = frappe_mock

import frappe
from brazil.utils.qrcode_gen import generate_qrcode_for_doc


class TestGenerateQrcodeForDoc(unittest.TestCase):
    def test_no_pix_payload_returns_none(self):
        doc = MagicMock()
        doc.pix_copia_cola = ""
        result = generate_qrcode_for_doc(doc)
        self.assertIsNone(result)

    def test_none_pix_payload_returns_none(self):
        doc = MagicMock()
        doc.pix_copia_cola = None
        result = generate_qrcode_for_doc(doc)
        self.assertIsNone(result)

    def test_falsy_pix_payload_returns_none(self):
        doc = MagicMock()
        doc.pix_copia_cola = 0
        result = generate_qrcode_for_doc(doc)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
