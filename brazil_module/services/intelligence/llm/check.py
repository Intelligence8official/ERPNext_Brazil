"""
Does the configured provider answer?

A wrong service-account JSON, a project without the API enabled or a revoked
key all look the same from the desk: nothing happens until a scheduled job
fails at dawn. One cheap call, made the moment the credential is saved, turns
that into an answer on screen.
"""

import frappe

from brazil_module.services.intelligence.llm.base import LLMError
from brazil_module.services.intelligence.llm.client import LLM
from brazil_module.services.intelligence.llm.factory import model_for

PROMPT = "Answer with the single word OK."
MAX_TOKENS = 256
"""Room for the model to think.

On a reasoning model the budget covers reasoning tokens as well as the answer,
and a tight one comes back empty — which would report a perfectly good
credential as broken."""


def check_connection() -> dict:
    """Ask the provider for one word. Never raises: this is a button."""
    try:
        llm = LLM()
        answer = llm.ask(
            system="You are a connection check.",
            prompt=PROMPT,
            max_tokens=MAX_TOKENS,
            module="settings",
            function_name="test_connection",
        )
    except LLMError as e:
        return {"status": "error", "message": str(e)}
    except Exception as e:
        return {"status": "error", "message": str(e)}

    if not (answer or "").strip():
        return {"status": "error", "message": "The provider answered nothing"}

    return {
        "status": "success",
        "provider": getattr(llm.provider, "name", ""),
        "model": model_for("standard", frappe.get_single("I8 Agent Settings")),
        "answer": (answer or "").strip(),
    }
