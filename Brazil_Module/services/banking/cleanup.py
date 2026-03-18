"""
Log cleanup service.

Removes old API logs and webhook logs to prevent database bloat.
"""

from datetime import timedelta

import frappe
from frappe.utils import now_datetime


def cleanup_old_api_logs(days: int = 90):
    """Delete Inter API Log entries older than N days."""
    if not frappe.db.get_single_value("Banco Inter Settings", "enabled"):
        return

    cutoff = now_datetime() - timedelta(days=days)

    old_logs = frappe.get_all(
        "Inter API Log",
        filters={"timestamp": ["<", cutoff]},
        pluck="name",
        limit=1000,
    )

    for log_name in old_logs:
        try:
            frappe.delete_doc("Inter API Log", log_name, ignore_permissions=True)
        except Exception:
            pass

    if old_logs:
        frappe.db.commit()


def cleanup_old_webhook_logs(days: int = 90):
    """Delete Inter Webhook Log entries older than N days."""
    if not frappe.db.get_single_value("Banco Inter Settings", "enabled"):
        return

    cutoff = now_datetime() - timedelta(days=days)

    old_logs = frappe.get_all(
        "Inter Webhook Log",
        filters={"received_at": ["<", cutoff]},
        pluck="name",
        limit=1000,
    )

    for log_name in old_logs:
        try:
            frappe.delete_doc("Inter Webhook Log", log_name, ignore_permissions=True)
        except Exception:
            pass

    if old_logs:
        frappe.db.commit()
