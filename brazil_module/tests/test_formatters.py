"""Tests for Brazilian formatting utilities."""

import unittest

from brazil_module.utils.formatters import (
    clean_cpf_cnpj,
    format_cnpj,
    format_cpf,
    format_currency_brl,
    format_phone_br,
)


class TestFormatCnpj(unittest.TestCase):
    def test_format_14_digits(self):
        self.assertEqual(format_cnpj("12345678000195"), "12.345.678/0001-95")

    def test_format_wrong_length_short(self):
        self.assertEqual(format_cnpj("1234"), "1234")

    def test_format_wrong_length_long(self):
        self.assertEqual(format_cnpj("123456789012345"), "123456789012345")

    def test_format_already_formatted(self):
        self.assertEqual(format_cnpj("12.345.678/0001-95"), "12.345.678/0001-95")

    def test_format_with_other_chars(self):
        self.assertEqual(format_cnpj("12-345-678-0001-95"), "12.345.678/0001-95")


class TestFormatCpf(unittest.TestCase):
    def test_format_11_digits(self):
        self.assertEqual(format_cpf("12345678901"), "123.456.789-01")

    def test_format_wrong_length_short(self):
        self.assertEqual(format_cpf("1234"), "1234")

    def test_format_wrong_length_long(self):
        self.assertEqual(format_cpf("123456789012"), "123456789012")

    def test_format_already_formatted(self):
        self.assertEqual(format_cpf("123.456.789-01"), "123.456.789-01")


class TestCleanCpfCnpj(unittest.TestCase):
    def test_clean_formatted_cnpj(self):
        self.assertEqual(clean_cpf_cnpj("12.345.678/0001-95"), "12345678000195")

    def test_clean_formatted_cpf(self):
        self.assertEqual(clean_cpf_cnpj("123.456.789-01"), "12345678901")

    def test_clean_already_clean(self):
        self.assertEqual(clean_cpf_cnpj("12345678000195"), "12345678000195")

    def test_clean_only_digits(self):
        self.assertEqual(clean_cpf_cnpj("abc"), "")

    def test_clean_mixed(self):
        self.assertEqual(clean_cpf_cnpj("a1b2c3"), "123")


class TestFormatCurrencyBrl(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(format_currency_brl(1234.56), "R$ 1.234,56")

    def test_zero(self):
        self.assertEqual(format_currency_brl(0.0), "R$ 0,00")

    def test_large_number(self):
        self.assertEqual(format_currency_brl(1000000.00), "R$ 1.000.000,00")

    def test_small_number(self):
        self.assertEqual(format_currency_brl(0.99), "R$ 0,99")

    def test_integer_like(self):
        self.assertEqual(format_currency_brl(100.00), "R$ 100,00")

    def test_negative(self):
        result = format_currency_brl(-100.50)
        self.assertIn("100", result)
        self.assertIn("50", result)
        self.assertIn("-", result)

    def test_cents_only(self):
        self.assertEqual(format_currency_brl(0.01), "R$ 0,01")

    def test_thousands(self):
        self.assertEqual(format_currency_brl(9999.99), "R$ 9.999,99")


class TestFormatPhoneBr(unittest.TestCase):
    def test_mobile_11_digits(self):
        self.assertEqual(format_phone_br("11999887766"), "(11) 99988-7766")

    def test_landline_10_digits(self):
        self.assertEqual(format_phone_br("1133445566"), "(11) 3344-5566")

    def test_short_number(self):
        self.assertEqual(format_phone_br("1234"), "1234")

    def test_already_formatted_mobile(self):
        # Strips non-digits first, then reformats
        self.assertEqual(format_phone_br("(11) 99988-7766"), "(11) 99988-7766")

    def test_already_formatted_landline(self):
        self.assertEqual(format_phone_br("(11) 3344-5566"), "(11) 3344-5566")

    def test_with_country_code(self):
        # 13 digits with +55 country code -> strips +, becomes 13 digits -> returns as-is
        result = format_phone_br("+5511999887766")
        self.assertEqual(result, "5511999887766")

    def test_empty(self):
        self.assertEqual(format_phone_br(""), "")


if __name__ == "__main__":
    unittest.main()
