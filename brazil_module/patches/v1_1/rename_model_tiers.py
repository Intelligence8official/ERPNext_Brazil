"""
From vendor names to job names: haiku/sonnet/opus become fast/standard/deep.

Intelligence8 can now be pointed at Anthropic, Google or OpenAI, so a field
called `haiku_model` holding `gemini-3.8-flash` would be a setting that lies.
This carries an existing site over without anyone having to retype anything,
and records the provider it was already using.

Every step is guarded on its own: a site that is half-way through an upgrade
still has to come out of `bench migrate`, and a tier that fails to copy is
better than a migration that stops.
"""

import frappe

SETTINGS = "I8 Agent Settings"

MODEL_FIELDS = {"haiku_model": "model_fast", "sonnet_model": "model_standard", "opus_model": "model_deep"}
TIMEOUT_FIELDS = {
    "haiku_timeout_seconds": "timeout_fast",
    "sonnet_timeout_seconds": "timeout_standard",
    "opus_timeout_seconds": "timeout_deep",
}
TIERS = {"haiku": "fast", "sonnet": "standard", "opus": "deep"}


def execute():
    for old, new in {**MODEL_FIELDS, **TIMEOUT_FIELDS}.items():
        _carry_over(old, new)

    _set_if_empty("llm_provider", "Anthropic")

    for field in ("default_model", "escalation_model"):
        for old, new in TIERS.items():
            _rewrite_registry(field, old, new)


def _carry_over(old_field: str, new_field: str) -> None:
    """Copy a value, never overwriting one that is already there — migrate
    runs more than once, and the second run must not undo a hand-made choice."""
    try:
        if frappe.db.get_single_value(SETTINGS, new_field):
            return
        value = frappe.db.get_single_value(SETTINGS, old_field)
        if value:
            frappe.db.set_single_value(SETTINGS, new_field, value)
    except Exception as e:
        frappe.log_error(str(e), f"I8 Patch: could not carry {old_field} to {new_field}")


def _set_if_empty(field: str, value: str) -> None:
    try:
        if not frappe.db.get_single_value(SETTINGS, field):
            frappe.db.set_single_value(SETTINGS, field, value)
    except Exception as e:
        frappe.log_error(str(e), f"I8 Patch: could not set {field}")


def _rewrite_registry(field: str, old: str, new: str) -> None:
    """Raw SQL on purpose.

    Patches in this app run before the DocType sync, so the Select still
    offers the old options here — but a validated write would load every row
    through the ORM for a value swap, and would start refusing the day this
    patch is re-run after the sync.
    """
    try:
        frappe.db.sql(
            f"UPDATE `tabI8 Module Registry` SET `{field}` = %s WHERE `{field}` = %s",
            (new, old),
        )
    except Exception as e:
        frappe.log_error(str(e), f"I8 Patch: could not rewrite {field}")
