"""
ERPNext desk notifications for Intelligence8 actions.

Sends notifications via Frappe's built-in notification system (bell icon)
in addition to Telegram.
"""

import frappe


def notify_desk(
    title: str,
    message: str,
    document_type: str | None = None,
    document_name: str | None = None,
    user: str = "Administrator",
) -> None:
    """Send a notification to the ERPNext desk (bell icon).

    Args:
        title: Notification title/subject
        message: Notification body
        document_type: Optional linked DocType
        document_name: Optional linked document name
        user: User to notify (default Administrator)
    """
    try:
        notification = frappe.new_doc("Notification Log")
        notification.subject = title
        notification.email_content = message
        notification.for_user = user
        notification.type = "Alert"
        if document_type:
            notification.document_type = document_type
        if document_name:
            notification.document_name = document_name
        notification.insert(ignore_permissions=True)
    except Exception as e:
        frappe.log_error(str(e), "I8 Desk Notification Error")
