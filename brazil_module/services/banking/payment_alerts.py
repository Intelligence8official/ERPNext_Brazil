"""Tell a human that an outbound payment needs attention.

``alert_operator`` fans out to three channels - Error Log, Notification Log (the desk bell)
and Telegram. Each one runs in its own ``try``: a channel that fails never silences the
others, and the function never raises, because it is called from ``except`` blocks and
right after the state that protects the money was committed.

It does not commit and does not roll back: the transaction belongs to the caller.

See docs/superpowers/specs/2026-09-20-inter-payment-safety-design.md (section 4.1).
"""

import html
import sys

import frappe
from frappe.utils import get_url_to_form

from brazil_module.services.banking.payment_common import ERROR_LOG_TITLE_LENGTH, JobTimeoutException
from brazil_module.services.banking.payment_guards import DOCTYPE

MANAGER_ROLE = "Banco Inter Manager"
FALLBACK_USER = "Administrator"
AGENT_SETTINGS = "I8 Agent Settings"


def alert_operator(subject: str, message: str, order_name: str | None = None) -> None:
    """Error Log + bell for every ``Banco Inter Manager`` + Telegram. Never raises."""
    try:
        subject = _one_line(subject)
        order_name = str(order_name) if order_name else None
        body = _compose_body(message, order_name)
    except Exception as error:  # arguments whose str() fails: still say *something*
        subject, body, order_name = "Inter payment alert", f"(alert could not be composed: {error!r})", None

    _run_channel("Error Log", _write_error_log, subject, body, order_name)
    _run_channel("Notification Log", _notify_managers, subject, body, order_name)
    _run_channel("Telegram", _send_telegram, subject, body)


def _one_line(subject) -> str:
    # frappe.log_error swaps title and message when the title has a line break.
    text = " ".join(str(subject or "Inter payment alert").split())
    return text[:ERROR_LOG_TITLE_LENGTH]


def _compose_body(message, order_name: str | None) -> str:
    lines = [str(message or "")]
    if order_name:
        lines.append(f"{DOCTYPE}: {order_name}")
        link = _order_link(order_name)
        if link:
            lines.append(link)
    return "\n".join(line for line in lines if line)


def _order_link(order_name: str) -> str | None:
    try:
        return str(get_url_to_form(DOCTYPE, order_name))
    except Exception:
        return None  # no host outside a request: the alert goes out without the link


def _run_channel(channel: str, send, *args) -> None:
    try:
        send(*args)
    except JobTimeoutException:
        # Telegram waits up to 10 s twice. RQ's death penalty must not be swallowed here: the rest
        # of the module lets it through so the job stops in order instead of being killed later.
        raise
    except Exception as error:
        _report_channel_failure(channel, error)


def _report_channel_failure(channel: str, error: Exception) -> None:
    """A failed channel is never silent: Error Log, and the worker's stderr as the last resort."""
    try:
        frappe.log_error(title=f"Inter payment alert: {channel} failed", message=repr(error))
    except JobTimeoutException:
        raise
    except Exception:
        try:
            print(f"Inter payment alert: {channel} failed: {error!r}", file=sys.stderr)
        except Exception:
            pass


def _write_error_log(subject: str, body: str, order_name: str | None) -> None:
    frappe.log_error(
        title=subject,
        message=body,
        reference_doctype=DOCTYPE if order_name else None,
        reference_name=order_name,
    )


def _notify_managers(subject: str, body: str, order_name: str | None) -> None:
    for user in _manager_users():
        _run_channel(f"Notification Log for {user}", _insert_notification, user, subject, body, order_name)


def _manager_users() -> list[str]:
    holders = frappe.get_all("Has Role", filters={"role": MANAGER_ROLE, "parenttype": "User"}, pluck="parent")
    holders = sorted({holder for holder in holders if holder})
    users = frappe.get_all("User", filters={"name": ["in", holders], "enabled": 1}, pluck="name") if holders else []
    return list(users) or [FALLBACK_USER]


def _insert_notification(user: str, subject: str, body: str, order_name: str | None) -> None:
    # The desk renders both fields as HTML and the text may quote the bank: escape it.
    notification = frappe.new_doc("Notification Log")
    notification.subject = html.escape(subject)
    notification.email_content = html.escape(body).replace("\n", "<br>")
    notification.for_user = user
    notification.type = "Alert"
    if order_name:
        notification.document_type = DOCTYPE
        notification.document_name = order_name
    notification.insert(ignore_permissions=True)


def _send_telegram(subject: str, body: str) -> None:
    chat_id = frappe.db.get_single_value(AGENT_SETTINGS, "telegram_chat_id")
    if not chat_id:
        return
    from brazil_module.services.intelligence.channels.telegram_bot import TelegramBot

    TelegramBot().send_message(chat_id, f"**{subject}**\n{body}")
