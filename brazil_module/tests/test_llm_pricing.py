import sys
import unittest
from unittest.mock import MagicMock

if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    _fm = MagicMock()
    _fm._ = lambda x: x
    sys.modules["frappe"] = _fm
    sys.modules["frappe.utils"] = _fm.utils

import frappe

from brazil_module.services.intelligence.llm.pricing import calculate_cost_usd, price_for


class TestPriceFor(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()
        frappe.log_error.side_effect = None
        frappe.cache.get_value.return_value = None
        frappe.cache.get_value.side_effect = None

    def test_known_model_has_its_own_price(self):
        price = price_for("anthropic", "claude-sonnet-5")

        self.assertEqual((price.input, price.output, price.cached_input), (2.0, 10.0, 0.20))

    def test_the_models_a_migrating_site_arrives_with_have_a_price(self):
        # The patch carries claude-sonnet-4-6 and claude-opus-4-6 over from
        # the old fields. Without their own row they would be priced as the
        # dearest model of the provider — 3x the real rate, on every call.
        self.assertEqual(price_for("anthropic", "claude-sonnet-4-6").input, 3.0)
        self.assertEqual(price_for("anthropic", "claude-opus-4-6").output, 25.0)

    def test_a_model_nobody_priced_is_only_reported_once(self):
        # The report is an Error Log write; one per call would flood it.
        frappe.cache.get_value.return_value = None

        price_for("openai", "gpt-unknown-1")
        frappe.cache.get_value.return_value = "reported"
        price_for("openai", "gpt-unknown-1")

        self.assertEqual(frappe.log_error.call_count, 1)

    def test_each_provider_has_the_three_tiers_it_is_configured_with(self):
        for provider, model in (
            ("anthropic", "claude-haiku-4-5-20251001"),
            ("google", "gemini-3.8-flash"),
            ("openai", "gpt-5.6-terra"),
        ):
            self.assertIsNotNone(price_for(provider, model), provider)

    def test_unknown_model_is_priced_as_the_dearest_of_its_provider(self):
        # The budget gate spends this number. Guessing low would let an
        # unknown model run past the daily budget without tripping it.
        unknown = price_for("google", "gemini-99-whatever")

        self.assertEqual(unknown, price_for("google", "gemini-3.1-pro-preview"))

    def test_an_unknown_model_never_costs_less_than_a_known_one(self):
        unknown = price_for("openai", "gpt-does-not-exist")

        for model in ("gpt-5.6-luna", "gpt-5.6-terra", "gpt-6-astra"):
            self.assertGreaterEqual(unknown.output, price_for("openai", model).output)

    def test_unknown_model_is_logged(self):
        price_for("openai", "gpt-does-not-exist")

        self.assertTrue(frappe.log_error.called)

    def test_unknown_provider_falls_back_without_raising(self):
        price = price_for("mistral", "mistral-large")

        self.assertGreater(price.output, 0)


class TestCalculateCost(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()
        frappe.log_error.side_effect = None
        frappe.cache.get_value.return_value = None
        frappe.cache.get_value.side_effect = None

    def test_charges_input_and_output_per_million(self):
        cost = calculate_cost_usd("anthropic", "claude-sonnet-5", tokens_in=1_000_000, tokens_out=1_000_000)

        self.assertAlmostEqual(cost, 12.0)

    def test_cached_tokens_are_charged_apart_and_cheaper(self):
        # Cached tokens are NOT part of tokens_in: every adapter subtracts
        # them, because the providers report the two differently.
        cost = calculate_cost_usd(
            "anthropic", "claude-sonnet-5", tokens_in=0, tokens_out=0, cached_tokens=1_000_000
        )

        self.assertAlmostEqual(cost, 0.20)

    def test_adds_up_the_three_parts(self):
        cost = calculate_cost_usd(
            "google", "gemini-3.8-flash", tokens_in=100_000, tokens_out=10_000, cached_tokens=50_000
        )

        expected = (100_000 * 0.75 + 10_000 * 3.75 + 50_000 * 0.075) / 1_000_000
        self.assertAlmostEqual(cost, expected)

    def test_writing_to_the_cache_costs_a_quarter_more_than_plain_input(self):
        # Filling a cache is charged above the input rate everywhere; only
        # reading from it is cheaper.
        cost = calculate_cost_usd(
            "openai", "gpt-5.6-terra", tokens_in=0, tokens_out=0, cache_write_tokens=1_000_000
        )

        self.assertAlmostEqual(cost, 2.50)

    def test_a_long_prompt_pays_the_long_context_rate(self):
        # Gemini Pro doubles above 200k input tokens, and deep-tier prompts
        # are the long ones. Charging the short rate under-reports where it
        # costs most.
        short = calculate_cost_usd("google", "gemini-2.5-pro", tokens_in=100_000, tokens_out=1_000)
        long = calculate_cost_usd("google", "gemini-2.5-pro", tokens_in=300_000, tokens_out=1_000)

        self.assertAlmostEqual(short, (100_000 * 1.25 + 1_000 * 10.0) / 1_000_000)
        self.assertAlmostEqual(long, (300_000 * 2.50 + 1_000 * 15.0) / 1_000_000)

    def test_the_threshold_counts_every_input_bucket(self):
        # Cached tokens are still tokens in the window.
        cost = calculate_cost_usd(
            "google", "gemini-2.5-pro", tokens_in=100_000, tokens_out=0, cached_tokens=150_000
        )

        self.assertAlmostEqual(cost, (100_000 * 2.50 + 150_000 * 0.25) / 1_000_000)

    def test_a_call_with_no_tokens_costs_nothing(self):
        self.assertEqual(calculate_cost_usd("openai", "gpt-5.6-luna", tokens_in=0, tokens_out=0), 0.0)


if __name__ == "__main__":
    unittest.main()
