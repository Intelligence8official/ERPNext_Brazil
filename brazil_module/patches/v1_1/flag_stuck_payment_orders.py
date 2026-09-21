"""
Meet the orders the hourly re-send defect left behind.

A submitted Inter Payment Order still in `Processing` is one whose result could never be
written (saving a submitted order raised, after the bank had answered) and that the old cron
sent again every hour. The ERP does not know what the bank did with it, so it becomes `Needs Verification`:
only a human, with the bank statement, can tell.

Every blocking order also receives its `invoice_lock`, the unique column that lets the database
refuse a second payment of the same invoice. The first blocking order of an invoice wins; any
other is reported to the Error Log for the operator to cancel or resolve.

Runs after the model sync: it reads and writes columns that only exist then. Plain
`frappe.db.set_value` on purpose - this runs inside `bench migrate`, where the payment service
and its outside alerts have no business. The operator is told on the order's timeline and by
the deploy checklist (spec section 5). Safe to run again: the second run finds nothing to do.

See docs/superpowers/specs/2026-09-20-inter-payment-safety-design.md (section 4.7).
"""

import html
import re

import frappe
from frappe import _

from brazil_module.services.banking.payment_guards import DOCTYPE, is_blocking

API_LOG = "Inter API Log"
STUCK_STATUS = "Processing"
FLAGGED_STATUS = "Needs Verification"
ERROR_TITLE = "Inter payment safety patch"
# codigoSolicitacao (Pix) / codigoTransacao (boleto), read with a pattern because the log
# truncates response bodies and a cut JSON document no longer parses.
_BANK_ID = re.compile(r'"(?:codigoSolicitacao|codigoTransacao)"\s*:\s*"([^"]+)"')
_LOCK_FIELDS = ["name", "status", "payment_entry", "purchase_invoice", "invoice_lock"]


API_LOG_SCAN_LIMIT = 50


def execute():
    for name in frappe.get_all(
        DOCTYPE, filters={"docstatus": 1, "status": STUCK_STATUS}, pluck="name", order_by="creation asc"
    ):
        if _flag(name):
            _explain_on_timeline(name)
    _restore_invoice_locks()


def _flag(name: str) -> bool:
    """`Processing` -> `Needs Verification`, only if the row - read under a lock - is still stuck.

    An error here is not swallowed: a migration that stops is better than an order that stays
    `Processing` without anyone knowing. Committed at once, so nothing later can undo it.
    """
    row = frappe.db.get_value(DOCTYPE, name, ["status", "docstatus"], as_dict=True, for_update=True)
    if not row or row.get("docstatus") != 1 or row.get("status") != STUCK_STATUS:
        frappe.db.rollback()
        return False
    frappe.db.set_value(DOCTYPE, name, "status", FLAGGED_STATUS)
    frappe.db.commit()
    return True


def _explain_on_timeline(name: str) -> None:
    """Best effort: the state is already safe, a missing comment must not stop the migration."""
    try:
        order = frappe.db.get_value(DOCTYPE, name, ["name", "purchase_invoice", "barcode"], as_dict=True)
        text = _comment_text(_logged_bank_ids(order))
        frappe.get_doc(DOCTYPE, name).add_comment("Info", text)
        frappe.db.commit()
    except Exception as error:
        frappe.db.rollback()
        _report(f"{name}: flagged as {FLAGGED_STATUS}, but the timeline comment failed: {error!r}")


def _comment_text(bank_ids: list[str]) -> str:
    intro = _(
        "Flagged by the payment safety migration: this order was still Processing, so the ERP does not know "
        "what the bank did with it. It may have been sent to the bank more than once."
    )
    if bank_ids:
        found = _("Bank ids recorded in Inter API Log for this order, oldest first:")
        found = "{0}<br>{1}".format(found, "<br>".join(html.escape(bank_id) for bank_id in bank_ids))
    else:
        found = _(
            "No bank id was found in Inter API Log for this order. That does not mean nothing was sent: "
            "requests that ended without an answer left no trace."
        )
    action = _(
        "Do not pay this invoice any other way. Check the bank statement and the bank's approval queue first: "
        "pending Pix requests cannot be cancelled by API, and unapproved ones expire. "
        "Then use Resolve Verification."
    )
    return "<br><br>".join((intro, found, action))


def _logged_bank_ids(order: dict) -> list[str]:
    """`<bank id> (<when>)` of the log rows whose request mentions this order, oldest first, each id once."""
    rows = {}
    for token in _search_tokens(order):
        mentions = re.compile(rf"(?<![\w-]){re.escape(token)}(?![\w-])")  # ...00031 is not ...00031-1
        for row in frappe.get_all(
            API_LOG,
            filters={"request_body": ["like", f"%{token}%"]},
            fields=["name", "creation", "timestamp", "request_body", "response_body"],
            order_by="creation asc",
            # A leading wildcard cannot use an index and get_all is unbounded by default; this
            # runs inside bench migrate, over a table of long text.
            limit=API_LOG_SCAN_LIMIT,
        ):
            if mentions.search(row.get("request_body") or ""):
                rows[row.get("name")] = row
    found = {}
    for row in sorted(rows.values(), key=lambda logged: str(logged.get("creation") or "")):
        for bank_id in _BANK_ID.findall(row.get("response_body") or ""):
            found.setdefault(bank_id, row.get("timestamp") or row.get("creation"))
    return [f"{bank_id} ({when})" if when else bank_id for bank_id, when in found.items()]


def _search_tokens(order: dict) -> list[str]:
    """What a payment request of this order carried: `Payment <invoice or order>`, or the barcode."""
    barcode = "".join(char for char in str(order.get("barcode") or "") if char.isdigit())
    tokens = (order.get("purchase_invoice"), order.get("name"), barcode)
    return list(dict.fromkeys(token for token in tokens if token))


def _restore_invoice_locks() -> None:
    orders = frappe.get_all(
        DOCTYPE,
        filters={"docstatus": ["<", 2], "purchase_invoice": ["is", "set"]},
        fields=_LOCK_FIELDS,
        order_by="creation asc",
    )
    blocking = [order for order in orders if is_blocking(order.get("status"), order.get("payment_entry"))]
    holders = {
        order.get("purchase_invoice"): order.get("name")
        for order in blocking
        if order.get("invoice_lock") == order.get("purchase_invoice")
    }
    for order in blocking:
        name, invoice = order.get("name"), order.get("purchase_invoice")
        holder = holders.setdefault(invoice, name)
        if holder != name:
            _report(
                f"{name} ({order.get('status')}) and {holder} both cover Purchase Invoice {invoice}. "
                f"{holder} keeps the invoice lock; cancel or resolve {name}."
            )
        elif order.get("invoice_lock") != invoice:
            _lock(name, invoice)


def _lock(name: str, invoice: str) -> None:
    try:
        frappe.db.set_value(DOCTYPE, name, "invoice_lock", invoice)
        frappe.db.commit()
    except Exception as error:
        frappe.db.rollback()
        _report(f"{name}: could not take the invoice lock of Purchase Invoice {invoice}: {error!r}")


def _report(message: str) -> None:
    try:
        frappe.log_error(title=ERROR_TITLE, message=message)
        frappe.db.commit()
    except Exception:
        pass
