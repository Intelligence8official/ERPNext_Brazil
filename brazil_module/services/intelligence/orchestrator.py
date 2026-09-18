"""
Orchestrator -- Routes events to the appropriate module agent.

Routing strategy:
1. approved_action -> read module from Decision Log
2. Configurable mapping (I8 Event Routing child table)
3. LLM fallback (Haiku classifies using module descriptions)
4. Default: "conversational"
"""

import json
import uuid

import frappe


def route_event(event_type: str, event_data: dict) -> list[str]:
    """Determine which module(s) should handle this event.

    Returns:
        Ordered list of module names to execute.
    """
    # Special case: approved_action uses the original module
    if event_type == "approved_action":
        dl_name = event_data.get("decision_log")
        if dl_name:
            module = frappe.db.get_value("I8 Decision Log", dl_name, "module")
            if module:
                return [module]

    # 1. Check configurable routing table
    settings = frappe.get_single("I8 Agent Settings")
    for row in (settings.event_routing or []):
        if row.event_type == event_type:
            return [row.module_name]

    # 2. LLM fallback for human_message and unknown events
    return _classify_with_llm(event_data, settings)


def _classify_with_llm(event_data: dict, settings) -> list[str]:
    """Use Haiku to classify which module(s) should handle this event."""
    modules = frappe.get_all(
        "I8 Module Registry",
        filters={"enabled": 1},
        fields=["module_name", "description"],
    )

    if not modules:
        return ["conversational"]

    module_list = "\n".join(
        f"- {m['module_name']}: {m['description']}" for m in modules
    )

    text = (
        event_data.get("text")
        or event_data.get("subject")
        or json.dumps(event_data, default=str)[:500]
    )

    try:
        from brazil_module.services.intelligence.llm.client import LLM

        result_text = LLM().ask(
            system=(
                "You are a router. Given a user request and a list of available modules, "
                "return ONLY the module name(s) that should handle it. "
                "If the request spans multiple modules, return them comma-separated in execution order. "
                "Return ONLY module names, nothing else."
            ),
            prompt=f"Request: {text}\n\nAvailable modules:\n{module_list}",
            tier="fast",
            # Room for reasoning tokens: the answer itself is a few words.
            max_tokens=512,
            module="orchestrator",
            function_name="route_event",
        ).strip()

        chosen = [m.strip() for m in result_text.split(",")]
        # Validate module names
        valid_names = {m["module_name"] for m in modules}
        validated = [m for m in chosen if m in valid_names]
        return validated if validated else ["conversational"]

    except Exception as e:
        frappe.log_error(str(e), "I8 Orchestrator LLM Classification Error")
        return ["conversational"]


def generate_trace_id() -> str:
    """Generate a unique trace ID for event processing chain."""
    return str(uuid.uuid4())[:12]
