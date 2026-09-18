import os
import sys
import unittest
from unittest.mock import MagicMock

if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    _fm = MagicMock()
    _fm._ = lambda x: x
    sys.modules["frappe"] = _fm
    sys.modules["frappe.utils"] = _fm.utils

import frappe

from brazil_module.services.intelligence.llm.base import LLMError
from brazil_module.services.intelligence.llm.factory import (
    DEFAULT_MODELS,
    build_provider,
    model_for,
    timeout_for,
)


class _Settings:
    """An I8 Agent Settings document, as the resolver sees it."""

    def __init__(self, **fields):
        self._passwords = fields.pop("passwords", {})
        for name, value in fields.items():
            setattr(self, name, value)

    def get_password(self, fieldname, raise_exception=False):
        return self._passwords.get(fieldname)


def _clean_env():
    keep = dict(os.environ)
    for name in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_CLOUD_PROJECT",
    ):
        os.environ.pop(name, None)
    return keep


class TestBuildProvider(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()
        original = _clean_env()
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(original)))

    def test_anthropic_is_the_default_provider(self):
        provider = build_provider(_Settings(passwords={"anthropic_api_key": "sk-ant"}))

        self.assertEqual(provider.name, "anthropic")

    def test_the_setting_chooses_the_provider(self):
        for chosen, expected in (("Google", "google"), ("OpenAI", "openai"), ("Anthropic", "anthropic")):
            provider = build_provider(_Settings(llm_provider=chosen, google_project="i8-prod"))

            self.assertEqual(provider.name, expected)

    def test_an_unknown_provider_says_so(self):
        with self.assertRaises(LLMError) as raised:
            build_provider(_Settings(llm_provider="Mistral"))

        self.assertIn("Mistral", str(raised.exception))

    def test_the_environment_wins_over_the_stored_key(self):
        os.environ["ANTHROPIC_API_KEY"] = "sk-from-env"

        provider = build_provider(_Settings(passwords={"anthropic_api_key": "sk-from-db"}))

        self.assertEqual(provider._api_key, "sk-from-env")

    def test_google_goes_to_vertex_with_project_and_service_account(self):
        # This is the path that spends the startup credits.
        settings = _Settings(
            llm_provider="Google",
            google_auth_mode="Enterprise",
            google_project="i8-prod",
            google_location="southamerica-east1",
            passwords={"google_service_account_json": '{"type": "service_account"}'},
        )

        provider = build_provider(settings)

        self.assertEqual(provider._project, "i8-prod")
        self.assertEqual(provider._location, "southamerica-east1")
        self.assertEqual(provider._service_account_json, '{"type": "service_account"}')
        self.assertIsNone(provider._api_key)

    def test_google_location_defaults_to_global(self):
        # The global endpoint is the price without the regional surcharge.
        provider = build_provider(_Settings(llm_provider="Google", google_project="i8-prod"))

        self.assertEqual(provider._location, "global")

    def test_google_in_api_key_mode_carries_no_project(self):
        settings = _Settings(
            llm_provider="Google",
            google_auth_mode="API Key",
            google_project="i8-prod",
            passwords={"google_api_key": "AIza-test"},
        )

        provider = build_provider(settings)

        self.assertEqual(provider._api_key, "AIza-test")
        self.assertIsNone(provider._project)

    def test_google_without_a_project_in_enterprise_mode_is_refused(self):
        # Failing here names the missing setting; failing at Google names none.
        with self.assertRaises(LLMError) as raised:
            build_provider(_Settings(llm_provider="Google", google_auth_mode="Enterprise"))

        self.assertIn("project", str(raised.exception).lower())

    def test_openai_reads_its_own_key(self):
        provider = build_provider(_Settings(llm_provider="OpenAI", passwords={"openai_api_key": "sk-oai"}))

        self.assertEqual(provider._api_key, "sk-oai")


class TestModelForTier(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()

    def test_reads_the_model_configured_for_each_tier(self):
        settings = _Settings(model_fast="a", model_standard="b", model_deep="c")

        self.assertEqual(
            [model_for(tier, settings) for tier in ("fast", "standard", "deep")], ["a", "b", "c"]
        )

    def test_falls_back_to_the_providers_default_when_empty(self):
        # Switching provider without filling three fields still has to work.
        settings = _Settings(llm_provider="Google", model_fast="", model_standard=None)

        self.assertEqual(model_for("standard", settings), DEFAULT_MODELS["google"]["standard"])

    def test_still_reads_the_old_field_names_before_the_patch_runs(self):
        # The migration renames haiku/sonnet/opus; a site mid-upgrade must not
        # start talking to a default model nobody chose.
        settings = _Settings(haiku_model="claude-haiku-4-5-20251001", sonnet_model="claude-sonnet-5")

        self.assertEqual(model_for("fast", settings), "claude-haiku-4-5-20251001")
        self.assertEqual(model_for("standard", settings), "claude-sonnet-5")

    def test_an_unknown_tier_is_treated_as_standard(self):
        settings = _Settings(model_standard="b")

        self.assertEqual(model_for("whatever", settings), "b")


class TestTimeoutForTier(unittest.TestCase):
    def test_reads_the_timeout_of_each_tier(self):
        settings = _Settings(timeout_fast=10, timeout_standard=20, timeout_deep=30)

        self.assertEqual(
            [timeout_for(tier, settings) for tier in ("fast", "standard", "deep")], [10, 20, 30]
        )

    def test_falls_back_to_the_old_field_then_to_a_default(self):
        self.assertEqual(timeout_for("deep", _Settings(opus_timeout_seconds=99)), 99)
        self.assertEqual(timeout_for("deep", _Settings()), 120)


class TestSettingsLookup(unittest.TestCase):
    def test_reads_the_singleton_when_no_settings_are_given(self):
        frappe.reset_mock()
        frappe.get_single.return_value = _Settings(llm_provider="OpenAI", passwords={"openai_api_key": "sk"})

        provider = build_provider()

        self.assertEqual(provider.name, "openai")
        frappe.get_single.assert_called_once_with("I8 Agent Settings")


if __name__ == "__main__":
    unittest.main()
