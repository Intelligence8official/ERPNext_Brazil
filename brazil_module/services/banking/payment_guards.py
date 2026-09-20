"""Read-only guards shared by every entry point of the outbound payment path.

The controller, the payment service, the API and the weekly scheduler all ask the same
questions here, so the rule "may this invoice be paid now?" has a single answer.
Nothing in this module writes, locks, commits or talks to the bank.

See docs/superpowers/specs/2026-09-20-inter-payment-safety-design.md (sections 3 and 4.1).
"""

import frappe
from frappe import _
from frappe.utils import flt

DOCTYPE = "Inter Payment Order"

# The bank may hold the payment: the order cannot be cancelled, re-sent or forgotten.
IN_FLIGHT_STATUSES = ("Processing", "Awaiting Bank", "Needs Verification")
# The bank definitely does not hold the payment: the invoice is free for a new attempt.
NON_BLOCKING_STATUSES = ("Failed", "Cancelled")
CANCELLABLE_STATUSES = ("Draft", "Pending Approval", "Approved", "Failed")
AMOUNT_TOLERANCE = 0.01

ACCOUNT_DOCTYPE = "Inter Company Account"
SUPPLIER_PAYMENT_HOLD_TYPES = ("All", "Payments")
_INVOICE_FIELDS = ["docstatus", "on_hold", "outstanding_amount", "supplier", "company"]


def is_integration_enabled() -> bool:
    """The kill switch (I8): ``Banco Inter Settings.enabled``."""
    return bool(frappe.db.get_single_value("Banco Inter Settings", "enabled"))


def is_blocking(status: str, payment_entry: str | None) -> bool:
    """Whether an order in this state still protects its invoice (I6).

    ``Completed`` stops blocking once it has a Payment Entry: from then on the payment is in
    the invoice's ``outstanding_amount``. An unknown status blocks - the safe direction.
    """
    if status in NON_BLOCKING_STATUSES:
        return False
    return not (status == "Completed" and payment_entry)


def find_blocking_payment_order(purchase_invoice: str, exclude: str | None = None) -> dict | None:
    """``{"name", "status"}`` of another blocking order (``docstatus < 2``) of the invoice."""
    if not purchase_invoice:
        return None
    orders = frappe.get_all(
        DOCTYPE,
        filters={"purchase_invoice": purchase_invoice, "docstatus": ["<", 2]},
        fields=["name", "status", "payment_entry"],
        order_by="creation asc",
    )
    for order in orders:
        if order.get("name") == exclude:
            continue
        if is_blocking(order.get("status"), order.get("payment_entry")):
            return {"name": order.get("name"), "status": order.get("status")}
    return None


def find_draft_payment_entry(purchase_invoice: str, exclude_order: str | None = None) -> dict | None:
    """``{"name"}`` of a DRAFT Payment Entry that references the invoice.

    Submitted entries are already reflected in ``outstanding_amount``. Entries that belong to
    ``exclude_order`` are ignored. The order link is compared here, not in the query: it is
    NULL on entries typed by a human and ``NULL != 'x'`` is not true in SQL.
    """
    if not purchase_invoice:
        return None
    parents = frappe.get_all(
        "Payment Entry Reference",
        filters={
            "reference_doctype": "Purchase Invoice",
            "reference_name": purchase_invoice,
            "parenttype": "Payment Entry",
        },
        pluck="parent",
    )
    parents = sorted({parent for parent in parents if parent})
    if not parents:
        return None
    entries = frappe.get_all(
        "Payment Entry",
        filters={"name": ["in", parents], "docstatus": 0},
        fields=["name", "inter_payment_order"],
        order_by="creation asc",
    )
    for entry in entries:
        if exclude_order and entry.get("inter_payment_order") == exclude_order:
            continue
        return {"name": entry.get("name")}
    return None


def check_invoice_payable(
    purchase_invoice: str,
    amount: float,
    order_name: str | None = None,
    company: str | None = None,
) -> str | None:
    """``None`` when the invoice may be paid now, otherwise the reason in plain words.

    ``order_name`` is the order asking (it does not block itself, nor does its own draft
    Payment Entry); ``company`` is the company of that order.
    """
    if not purchase_invoice:
        return _("No Purchase Invoice was given")
    invoice = frappe.db.get_value("Purchase Invoice", purchase_invoice, _INVOICE_FIELDS, as_dict=True)
    if not invoice:
        return _("Purchase Invoice {0} was not found").format(purchase_invoice)
    reason = _invoice_state_reason(purchase_invoice, invoice, company)
    if reason:
        return reason
    reason = _outstanding_reason(purchase_invoice, invoice.get("outstanding_amount"), amount)
    if reason:
        return reason
    return _competing_document_reason(purchase_invoice, order_name)


def get_inter_account_for_company(company: str) -> str | None:
    """The ``Inter Company Account`` that pays for the company."""
    if not company:
        return None
    return frappe.db.get_value(ACCOUNT_DOCTYPE, {"company": company, "sync_enabled": 1}, "name") or None


def _invoice_state_reason(purchase_invoice: str, invoice: dict, company: str | None) -> str | None:
    if invoice.get("docstatus") == 2:
        return _("Purchase Invoice {0} is cancelled").format(purchase_invoice)
    if invoice.get("docstatus") != 1:
        return _("Purchase Invoice {0} is not submitted").format(purchase_invoice)
    if invoice.get("on_hold"):
        return _("Purchase Invoice {0} is on hold").format(purchase_invoice)
    supplier = invoice.get("supplier")
    if supplier and _is_supplier_on_payment_hold(supplier):
        return _("Supplier {0} is on payment hold").format(supplier)
    if company and invoice.get("company") != company:
        return _("Purchase Invoice {0} belongs to company {1}, not to {2}").format(
            purchase_invoice, invoice.get("company"), company
        )
    return None


def _is_supplier_on_payment_hold(supplier: str) -> bool:
    hold = frappe.db.get_value("Supplier", supplier, ["on_hold", "hold_type"], as_dict=True)
    return bool(hold and hold.get("on_hold") and hold.get("hold_type") in SUPPLIER_PAYMENT_HOLD_TYPES)


def _outstanding_reason(purchase_invoice: str, outstanding, amount) -> str | None:
    if flt(amount) <= 0:
        return _("The payment amount for Purchase Invoice {0} must be greater than zero").format(purchase_invoice)
    # Spec: outstanding_amount + AMOUNT_TOLERANCE < amount. Compared in cents, because in binary
    # floats 2.03 + 0.01 < 2.04 is True and would refuse one cent of tolerance.
    if flt(flt(amount) - flt(outstanding), 2) <= AMOUNT_TOLERANCE:
        return None
    return _("Purchase Invoice {0} has only {1} outstanding, less than the payment of {2}").format(
        purchase_invoice, f"{flt(outstanding):.2f}", f"{flt(amount):.2f}"
    )


def _competing_document_reason(purchase_invoice: str, order_name: str | None) -> str | None:
    blocking = find_blocking_payment_order(purchase_invoice, exclude=order_name)
    if blocking:
        return _("Inter Payment Order {0} ({1}) already covers Purchase Invoice {2}").format(
            blocking["name"], blocking["status"], purchase_invoice
        )
    draft = find_draft_payment_entry(purchase_invoice, exclude_order=order_name)
    if draft:
        return _("Draft Payment Entry {0} already references Purchase Invoice {1}").format(
            draft["name"], purchase_invoice
        )
    return None
