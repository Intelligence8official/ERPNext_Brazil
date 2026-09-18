"""
Which provider answers, and with which model.

One provider serves the whole system — the choice lives in I8 Agent Settings,
and there is no per-module override and no automatic fallback to a second
vendor. Tiers are named after the job (`fast`, `standard`, `deep`) instead of
after one vendor's model names, because `haiku_model` holding a Gemini id
would be a setting that lies.

The old field names are still read. A site is mid-upgrade between the moment
the code lands and the moment the patch runs, and starting to talk to a
default model nobody chose would be worse than either state.
"""

import os
from typing import Any

import frappe

from brazil_module.services.intelligence.llm.base import LLMError, LLMProvider

SETTINGS_DOCTYPE = "I8 Agent Settings"

TIERS = ("fast", "standard", "deep")
DEFAULT_TIER = "standard"

DEFAULT_MODELS: dict[str, dict[str, str]] = {
    "anthropic": {
        "fast": "claude-haiku-4-5-20251001",
        "standard": "claude-sonnet-5",
        "deep": "claude-opus-5",
    },
    "google": {
        "fast": "gemini-3.5-flash-lite",
        "standard": "gemini-3.8-flash",
        # The only GA deep-reasoning model Google has: 3.1 Pro is preview.
        "deep": "gemini-2.5-pro",
    },
    "openai": {
        "fast": "gpt-5.6-luna",
        "standard": "gpt-5.6-terra",
        "deep": "gpt-6-astra",
    },
}

DEFAULT_TIMEOUTS = {"fast": 30, "standard": 60, "deep": 120}

LEGACY_MODEL_FIELDS = {"fast": "haiku_model", "standard": "sonnet_model", "deep": "opus_model"}
LEGACY_TIMEOUT_FIELDS = {
    "fast": "haiku_timeout_seconds",
    "standard": "sonnet_timeout_seconds",
    "deep": "opus_timeout_seconds",
}


def build_provider(settings: Any = None) -> LLMProvider:
    """The adapter for the provider this site is pointed at."""
    settings = _settings(settings)
    name = provider_name(settings)

    if name == "anthropic":
        from brazil_module.services.intelligence.llm.anthropic_provider import AnthropicProvider

        return AnthropicProvider(api_key=_secret(settings, "anthropic_api_key", "ANTHROPIC_API_KEY"))

    if name == "openai":
        from brazil_module.services.intelligence.llm.openai_provider import OpenAIProvider

        return OpenAIProvider(api_key=_secret(settings, "openai_api_key", "OPENAI_API_KEY"))

    if name == "google":
        return _google_provider(settings)

    raise LLMError("", None, f"Unknown LLM provider '{_field(settings, 'llm_provider')}' in {SETTINGS_DOCTYPE}")


def provider_name(settings: Any = None) -> str:
    return (_field(_settings(settings), "llm_provider") or "anthropic").strip().lower()


def model_for(tier: str, settings: Any = None) -> str:
    settings = _settings(settings)
    tier = tier if tier in TIERS else DEFAULT_TIER
    configured = _field(settings, f"model_{tier}") or _field(settings, LEGACY_MODEL_FIELDS[tier])
    if configured:
        return str(configured).strip()
    return DEFAULT_MODELS.get(provider_name(settings), DEFAULT_MODELS["anthropic"])[tier]


def timeout_for(tier: str, settings: Any = None) -> int:
    settings = _settings(settings)
    tier = tier if tier in TIERS else DEFAULT_TIER
    configured = _field(settings, f"timeout_{tier}") or _field(settings, LEGACY_TIMEOUT_FIELDS[tier])
    return int(configured or DEFAULT_TIMEOUTS[tier])


def _google_provider(settings: Any):
    from brazil_module.services.intelligence.llm.google_provider import (
        DEFAULT_LOCATION,
        GoogleProvider,
    )

    mode = (_field(settings, "google_auth_mode") or "Enterprise").strip().lower()
    if mode.startswith("api"):
        return GoogleProvider(
            api_key=_secret(settings, "google_api_key", "GOOGLE_API_KEY", "GEMINI_API_KEY")
        )

    project = os.environ.get("GOOGLE_CLOUD_PROJECT") or _field(settings, "google_project")
    if not project:
        # Failing here names the setting that is missing; failing at Google
        # names none of it, and this is the path that spends the credits.
        raise LLMError(
            "google",
            None,
            "Google is set to enterprise mode but no project is configured "
            f"(set google_project in {SETTINGS_DOCTYPE} or GOOGLE_CLOUD_PROJECT)",
        )

    return GoogleProvider(
        project=str(project).strip(),
        location=(_field(settings, "google_location") or DEFAULT_LOCATION).strip(),
        service_account_json=_password(settings, "google_service_account_json"),
    )


def _settings(settings: Any = None) -> Any:
    return settings if settings is not None else frappe.get_single(SETTINGS_DOCTYPE)


def _field(settings: Any, fieldname: str):
    try:
        return getattr(settings, fieldname, None)
    except Exception:
        return None


def _password(settings: Any, fieldname: str) -> str | None:
    try:
        return settings.get_password(fieldname)
    except Exception:
        return None


def _secret(settings: Any, fieldname: str, *env_names: str) -> str | None:
    """The environment wins over the stored value, as it already does for the
    Anthropic key: a deployment sets it once, without touching the database."""
    for name in env_names:
        value = os.environ.get(name)
        if value:
            return value
    return _password(settings, fieldname)
