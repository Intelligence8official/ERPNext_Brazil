"""Tests for QR code generation utility."""

import unittest
from unittest.mock import MagicMock, patch
import sys

# Ensure frappe mock is in place before import
if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    frappe_mock = MagicMock()
    sys.modules["frappe"] = frappe_mock

import frappe
from brazil_module.utils.qrcode_gen import generate_qrcode_for_doc


class TestGenerateQrcodeForDoc(unittest.TestCase):
    def _make_doc(self, pix_value):
        """Create a mock doc where .get() returns values like a Frappe Document."""
        doc = MagicMock()
        attrs = {"pix_copia_cola": pix_value}
        doc.get.side_effect = lambda key, default=None: attrs.get(key, default)
        return doc

    def test_no_pix_payload_returns_none(self):
        doc = self._make_doc("")
        result = generate_qrcode_for_doc(doc)
        self.assertIsNone(result)

    def test_none_pix_payload_returns_none(self):
        doc = self._make_doc(None)
        result = generate_qrcode_for_doc(doc)
        self.assertIsNone(result)

    def test_falsy_pix_payload_returns_none(self):
        doc = self._make_doc(0)
        result = generate_qrcode_for_doc(doc)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
