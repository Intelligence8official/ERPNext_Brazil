import frappe
from datetime import date


def check_learned_pattern(action: str, tool_args: dict) -> bool:
    """Check if this action has been approved enough times to auto-approve.

    Returns True if should auto-approve based on learning.
    """
    settings = frappe.get_single("I8 Agent Settings")
    if not settings.learning_enabled:
        return False

    threshold = settings.learning_approval_count or 3
    pattern_key = _build_pattern_key(action, tool_args)

    pattern = frappe.db.get_value(
        "I8 Learning Pattern",
        {"action": action, "pattern_key": pattern_key, "active": 1},
        ["consecutive_approvals", "name"],
        as_dict=True,
    )

    if pattern and pattern["consecutive_approvals"] >= threshold:
        # Increment auto_approved_count
        frappe.db.set_value("I8 Learning Pattern", pattern["name"], {
            "auto_approved_count": frappe.db.get_value(
                "I8 Learning Pattern", pattern["name"], "auto_approved_count"
            ) + 1,
            "last_approval_date": date.today(),
        })
        return True

    return False


def record_approval(action: str, tool_args: dict) -> None:
    """Record that a human approved this action. Increments the approval counter."""
    pattern_key = _build_pattern_key(action, tool_args)

    existing = frappe.db.get_value(
        "I8 Learning Pattern",
        {"action": action, "pattern_key": pattern_key},
        "name",
    )

    if existing:
        current = frappe.db.get_value(
            "I8 Learning Pattern", existing, "consecutive_approvals"
        ) or 0
        frappe.db.set_value("I8 Learning Pattern", existing, {
            "consecutive_approvals": current + 1,
            "last_approval_date": date.today(),
            "active": 1,
        })
    else:
        doc = frappe.new_doc("I8 Learning Pattern")
        doc.action = action
        doc.pattern_key = pattern_key
        doc.consecutive_approvals = 1
        doc.last_approval_date = date.today()
        doc.active = 1
        doc.insert(ignore_permissions=True)


def record_rejection(action: str, tool_args: dict) -> None:
    """Record that a human rejected this action. Resets the approval counter."""
    pattern_key = _build_pattern_key(action, tool_args)

    existing = frappe.db.get_value(
        "I8 Learning Pattern",
        {"action": action, "pattern_key": pattern_key},
        "name",
    )

    if existing:
        frappe.db.set_value("I8 Learning Pattern", existing, {
            "consecutive_approvals": 0,
            "last_approval_date": date.today(),
        })


def get_confidence_adjustment(action: str, tool_args: dict) -> float:
    """Get a confidence adjustment based on learning history.

    Returns a bonus (0.0 to 0.3) to add to the extracted confidence.
    More approvals = higher bonus, rejections = negative adjustment.
    """
    settings = frappe.get_single("I8 Agent Settings")
    if not settings.learning_enabled:
        return 0.0

    pattern_key = _build_pattern_key(action, tool_args)

    pattern = frappe.db.get_value(
        "I8 Learning Pattern",
        {"action": action, "pattern_key": pattern_key, "active": 1},
        ["consecutive_approvals", "auto_approved_count"],
        as_dict=True,
    )

    if not pattern:
        return 0.0

    approvals = (pattern.get("consecutive_approvals") or 0) + (pattern.get("auto_approved_count") or 0)

    # Gradual confidence boost: 0.05 per approval, max 0.3
    bonus = min(approvals * 0.05, 0.3)
    return bonus


def _build_pattern_key(action: str, tool_args: dict) -> str:
    """Build a pattern key from the action and relevant args.

    Groups actions by supplier for P2P, by doctype for ERP operations.
    """
    if "supplier" in tool_args:
        supplier = tool_args["supplier"]
        # Truncate to 100 chars for storage
        return f"supplier:{supplier[:100]}"
    if "nota_fiscal" in tool_args:
        return "nf_processing"
    if "communication" in tool_args:
        return "email_classification"
    if "doctype" in tool_args:
        return f"doctype:{tool_args['doctype']}"
    # Generic key
    return f"generic:{action}"
