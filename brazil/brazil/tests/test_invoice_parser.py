"""Tests for international invoice PDF parser."""

import unittest
from unittest.mock import MagicMock, patch
import sys
from datetime import date

# Ensure frappe mock is in place before import
if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    frappe_mock = MagicMock()
    frappe_mock._ = lambda x: x
    sys.modules["frappe"] = frappe_mock
    sys.modules["frappe.utils"] = frappe_mock.utils

import frappe
from brazil.services.fiscal.invoice_parser import (
    InvoiceParser,
    is_international_invoice,
    VENDOR_PATTERNS,
)


class TestIdentifyVendor(unittest.TestCase):
    def setUp(self):
        self.parser = InvoiceParser()

    def test_identify_github(self):
        key, info = self.parser._identify_vendor("Payment to GitHub for services")
        self.assertEqual(key, "github")
        self.assertEqual(info["name"], "GitHub, Inc.")

    def test_identify_github_by_billing(self):
        key, info = self.parser._identify_vendor("gh-billing charge on card")
        self.assertEqual(key, "github")

    def test_identify_microsoft(self):
        key, info = self.parser._identify_vendor("Microsoft Corporation invoice")
        self.assertEqual(key, "microsoft")

    def test_identify_microsoft_by_azure(self):
        key, info = self.parser._identify_vendor("Azure cloud services usage")
        self.assertEqual(key, "microsoft")

    def test_identify_openai(self):
        key, info = self.parser._identify_vendor("OpenAI API usage for March")
        self.assertEqual(key, "openai")

    def test_identify_anthropic(self):
        key, info = self.parser._identify_vendor("Anthropic API billing")
        self.assertEqual(key, "anthropic")

    def test_identify_anthropic_by_claude(self):
        key, info = self.parser._identify_vendor("Claude usage invoice")
        self.assertEqual(key, "anthropic")

    def test_identify_aws(self):
        key, info = self.parser._identify_vendor("Amazon Web Services Inc invoice")
        self.assertEqual(key, "aws")

    def test_identify_google_cloud(self):
        key, info = self.parser._identify_vendor("Google Cloud Platform billing")
        self.assertEqual(key, "google_cloud")

    def test_identify_stripe(self):
        key, info = self.parser._identify_vendor("Stripe payment processing")
        self.assertEqual(key, "stripe")

    def test_identify_digitalocean(self):
        key, info = self.parser._identify_vendor("DigitalOcean droplet hosting")
        self.assertEqual(key, "digitalocean")

    def test_identify_atlassian(self):
        key, info = self.parser._identify_vendor("Atlassian Jira Software subscription")
        self.assertEqual(key, "atlassian")

    def test_identify_atlassian_by_jira(self):
        key, info = self.parser._identify_vendor("Jira Cloud subscription renewal")
        self.assertEqual(key, "atlassian")

    def test_identify_slack(self):
        key, info = self.parser._identify_vendor("Slack Technologies workspace billing")
        self.assertEqual(key, "slack")

    def test_identify_twilio(self):
        key, info = self.parser._identify_vendor("Twilio communications platform")
        self.assertEqual(key, "twilio")

    def test_identify_sendgrid(self):
        key, info = self.parser._identify_vendor("SendGrid email delivery service")
        self.assertEqual(key, "sendgrid")

    def test_identify_heroku(self):
        key, info = self.parser._identify_vendor("Heroku dyno usage charges")
        self.assertEqual(key, "heroku")

    def test_identify_vercel(self):
        key, info = self.parser._identify_vendor("Vercel deployment platform")
        self.assertEqual(key, "vercel")

    def test_unknown_vendor_returns_none(self):
        key, info = self.parser._identify_vendor("Random Company LLC invoice")
        self.assertIsNone(key)
        self.assertIsNone(info)

    def test_case_insensitive(self):
        key, _ = self.parser._identify_vendor("GITHUB ENTERPRISE INVOICE")
        self.assertEqual(key, "github")


class TestParseDate(unittest.TestCase):
    def setUp(self):
        self.parser = InvoiceParser()

    def test_full_month_comma_year(self):
        result = self.parser._parse_date("January 15, 2024")
        self.assertEqual(result, date(2024, 1, 15))

    def test_full_month_space_year(self):
        result = self.parser._parse_date("January 15 2024")
        self.assertEqual(result, date(2024, 1, 15))

    def test_abbreviated_month(self):
        result = self.parser._parse_date("Jan 15, 2024")
        self.assertEqual(result, date(2024, 1, 15))

    def test_day_month_year(self):
        result = self.parser._parse_date("15 January 2024")
        self.assertEqual(result, date(2024, 1, 15))

    def test_us_numeric(self):
        result = self.parser._parse_date("01/15/2024")
        self.assertEqual(result, date(2024, 1, 15))

    def test_iso_format(self):
        result = self.parser._parse_date("2024-01-15")
        self.assertEqual(result, date(2024, 1, 15))

    def test_empty_returns_none(self):
        self.assertIsNone(self.parser._parse_date(""))

    def test_none_returns_none(self):
        self.assertIsNone(self.parser._parse_date(None))

    def test_garbage_returns_none(self):
        self.assertIsNone(self.parser._parse_date("not a date"))


class TestIsInternationalInvoice(unittest.TestCase):
    def test_brazilian_nf_with_chave_returns_false(self):
        text = "Chave: 35220612223333000155550010000000011000000019"
        self.assertFalse(is_international_invoice(text))

    def test_brazilian_nf_with_cnpj_returns_false(self):
        text = "CNPJ: 12.223.333/0001-55 Nota Fiscal"
        self.assertFalse(is_international_invoice(text))

    def test_dollar_amount_returns_true(self):
        text = "Invoice Total: $ 99.00"
        self.assertTrue(is_international_invoice(text))

    def test_usd_keyword_returns_true(self):
        text = "Amount: 150.00 USD"
        self.assertTrue(is_international_invoice(text))

    def test_euro_returns_true(self):
        text = "Total: € 200.00"
        self.assertTrue(is_international_invoice(text))

    def test_invoice_word_returns_true(self):
        text = "Invoice #12345 for services rendered"
        self.assertTrue(is_international_invoice(text))

    def test_plain_text_no_indicators_returns_false(self):
        text = "Documento interno de uso geral"
        self.assertFalse(is_international_invoice(text))


class TestExtractWithVendorPatterns(unittest.TestCase):
    def setUp(self):
        self.parser = InvoiceParser()

    def test_github_full_extraction(self):
        text = """
        GitHub, Inc.
        Invoice #GH-2024-001
        Invoice Date: January 15, 2024
        Amount Due: $49.00
        Billing Period: January 1 – January 31, 2024
        Description: GitHub Team subscription
        """
        vendor_info = VENDOR_PATTERNS["github"]
        data = self.parser._extract_with_vendor_patterns(text, "github", vendor_info)
        self.assertEqual(data["vendor_name"], "GitHub, Inc.")
        self.assertEqual(data["invoice_number"], "GH-2024-001")
        self.assertAlmostEqual(data["valor_total"], 49.0)
        self.assertEqual(data["data_emissao"], date(2024, 1, 15))
        self.assertEqual(data["currency"], "USD")

    def test_aws_extraction(self):
        text = """
        Amazon Web Services, Inc.
        Invoice Number: AWS-2024-100
        Invoice Date: February 1, 2024
        Total Amount: $1,234.56
        Statement Period: January 1 – January 31, 2024
        """
        vendor_info = VENDOR_PATTERNS["aws"]
        data = self.parser._extract_with_vendor_patterns(text, "aws", vendor_info)
        self.assertEqual(data["invoice_number"], "AWS-2024-100")
        self.assertAlmostEqual(data["valor_total"], 1234.56)

    def test_missing_fields_returns_partial(self):
        text = "GitHub billing summary"
        vendor_info = VENDOR_PATTERNS["github"]
        data = self.parser._extract_with_vendor_patterns(text, "github", vendor_info)
        self.assertEqual(data["vendor_name"], "GitHub, Inc.")
        self.assertNotIn("invoice_number", data)

    def test_amount_with_commas(self):
        text = "Total: $12,345.67"
        vendor_info = VENDOR_PATTERNS["github"]
        data = self.parser._extract_with_vendor_patterns(text, "github", vendor_info)
        self.assertAlmostEqual(data["valor_total"], 12345.67)


class TestExtractGeneric(unittest.TestCase):
    def setUp(self):
        self.parser = InvoiceParser()

    def test_dollar_amounts(self):
        text = "Invoice #INV-001\nTotal: $500.00\nDate: March 1, 2024"
        data = self.parser._extract_generic(text)
        self.assertEqual(data["invoice_number"], "INV-001")
        self.assertAlmostEqual(data["valor_total"], 500.0)
        self.assertEqual(data["currency"], "USD")

    def test_euro_currency(self):
        text = "Invoice #EU-001\nTotal: €200.00"
        data = self.parser._extract_generic(text)
        self.assertEqual(data["currency"], "EUR")

    def test_gbp_currency(self):
        text = "Invoice #UK-001\nTotal: £150.00"
        data = self.parser._extract_generic(text)
        self.assertEqual(data["currency"], "GBP")


class TestExtractDescription(unittest.TestCase):
    def setUp(self):
        self.parser = InvoiceParser()

    def test_description_from_service_pattern(self):
        text = "Description: Cloud hosting services monthly"
        result = self.parser._extract_description(text)
        self.assertIn("Cloud hosting services monthly", result)

    def test_description_from_subscription(self):
        text = "Subscription: Pro Plan Annual"
        result = self.parser._extract_description(text)
        self.assertIn("Pro Plan Annual", result)

    def test_vendor_fallback(self):
        text = "No matching patterns in this text at all."
        result = self.parser._extract_description(text, vendor_key="github")
        self.assertEqual(result, "GitHub, Inc. services")

    def test_none_when_no_match_no_vendor(self):
        text = "No description info"
        result = self.parser._extract_description(text, vendor_key=None)
        self.assertIsNone(result)


class TestParsePdf(unittest.TestCase):
    def setUp(self):
        self.parser = InvoiceParser()

    def test_known_vendor_returns_data(self):
        mock_page = MagicMock()
        mock_page.extract_text.return_value = (
            "GitHub, Inc.\nInvoice #GH-001\nAmount Due: $99.00\n"
            "Invoice Date: January 1, 2024"
        )
        mock_reader = MagicMock()
        mock_reader.pages = [mock_page]

        with patch("brazil.services.fiscal.invoice_parser.PdfReader", create=True) as MockPdf:
            # The module tries pypdf first
            with patch.dict("sys.modules", {"pypdf": MagicMock()}):
                # Patch at the point of use inside _extract_text
                import brazil.services.fiscal.invoice_parser as ip_mod
                original = ip_mod.InvoiceParser._extract_text

                def fake_extract(self, content):
                    return (
                        "GitHub, Inc.\nInvoice #GH-001\nAmount Due: $99.00\n"
                        "Invoice Date: January 1, 2024"
                    )

                ip_mod.InvoiceParser._extract_text = fake_extract
                try:
                    result = self.parser.parse_pdf(b"fake pdf bytes")
                    self.assertIsNotNone(result)
                    self.assertEqual(result["vendor_name"], "GitHub, Inc.")
                    self.assertEqual(result["document_type"], "Invoice")
                finally:
                    ip_mod.InvoiceParser._extract_text = original

    def test_empty_text_returns_none(self):
        import brazil.services.fiscal.invoice_parser as ip_mod
        original = ip_mod.InvoiceParser._extract_text
        ip_mod.InvoiceParser._extract_text = lambda self, c: None
        try:
            result = self.parser.parse_pdf(b"fake")
            self.assertIsNone(result)
        finally:
            ip_mod.InvoiceParser._extract_text = original

    def test_no_invoice_data_returns_none(self):
        import brazil.services.fiscal.invoice_parser as ip_mod
        original = ip_mod.InvoiceParser._extract_text
        ip_mod.InvoiceParser._extract_text = lambda self, c: "Just some random text without invoice data"
        try:
            result = self.parser.parse_pdf(b"fake")
            self.assertIsNone(result)
        finally:
            ip_mod.InvoiceParser._extract_text = original


if __name__ == "__main__":
    unittest.main()
