"""Inter Payment Order - outbound payment tracking (submittable).

The controller never talks to the bank and never writes the state of an order in flight. It
checks, hands over to ``payment_service`` and lets that module's compare-and-set transitions
write. No whitelisted method trusts ``self.status``: in ``frm.call`` the document is rebuilt
from client JSON, so every decision re-reads the database row.

See docs/superpowers/specs/2026-09-20-inter-payment-safety-design.md (sections 3 and 4.3).
"""

import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from brazil_module.services.banking.payment_guards import (
    CANCELLABLE_STATUSES,
    DOCTYPE,
    check_invoice_payable,
    is_integration_enabled,
)

MANAGER_ROLES = ("Banco Inter Manager", "System Manager")
# What a run leaves behind. Amend ignores ``no_copy`` in Frappe v15, so ``before_insert`` clears it.
RESULT_FIELDS = (
    "idempotency_key",
    "bank_status",
    "bank_request_at",
    "transaction_id",
    "approval_code",
    "execution_date",
    "inter_response",
    "payment_entry",
)


class InterPaymentOrder(Document):

    def before_insert(self):
        """An amended, duplicated or agent-created order always starts clean."""
        self.status = "Draft"
        for fieldname in RESULT_FIELDS:
            setattr(self, fieldname, None)

    def validate(self):
        if self.docstatus == 0:
            # `status` is read-only in the form only; a draft saved through the API cannot forge it.
            self.status = "Draft"
        if flt(self.amount) <= 0:
            frappe.throw(_("Amount must be greater than zero"))
        self.validate_payment_details()
        self.validate_purchase_invoice()

    def validate_payment_details(self):
        """Validate that required fields are set based on payment type."""
        if self.payment_type == "TED":
            frappe.throw(_("TED is not available: Banco Inter's Banking API has no TED endpoint. Use PIX instead."))
        if self.payment_type == "PIX" and not self.pix_key:
            frappe.throw(_("PIX Key is required for PIX payments"))
        if self.payment_type == "Boleto Payment":
            self.validate_boleto_details()

    def validate_boleto_details(self):
        self.barcode = re.sub(r"\D", "", self.barcode or "")
        if not self.barcode:
            frappe.throw(_("Barcode is required for Boleto Payment"))
        if not self.boleto_due_date:
            frappe.throw(_("Boleto Due Date is required for Boleto Payment"))

    def validate_purchase_invoice(self):
        """Refuse an invoice that cannot be paid now, then take its lock (I6).

        ``invoice_lock`` is a unique column: when two orders race past the check, the database
        refuses the second insert. Without an invoice it is NULL, never '' (two '' collide).
        """
        if not self.purchase_invoice:
            self.invoice_lock = None
            return
        reason = check_invoice_payable(
            self.purchase_invoice, self.amount, order_name=self.name, company=self.company
        )
        if reason:
            frappe.throw(reason)
        self.invoice_lock = self.purchase_invoice

    def on_submit(self):
        """Draft -> Pending Approval, or straight to Approved when no approval is required."""
        approval_required = frappe.db.get_single_value("Banco Inter Settings", "payment_approval_required")
        # db_set inside the save keeps the row's `modified` equal to the one the form receives,
        # so the next form action still passes Frappe's check_if_latest.
        self.db_set("status", "Pending Approval" if approval_required else "Approved")

    def before_cancel(self):
        """The database row decides, under a row lock: an order the bank may hold stays (I6)."""
        status = frappe.db.get_value(DOCTYPE, self.name, "status", for_update=True)
        if status in CANCELLABLE_STATUSES:
            return
        if status == "Completed":
            frappe.throw(_("Payment order {0} is 'Completed': it was paid and cannot be cancelled.").format(self.name))
        frappe.throw(
            _(
                "Payment order {0} is '{1}': the bank may hold this payment, so it cannot be cancelled. "
                "Check the bank status or resolve the verification first."
            ).format(self.name, status)
        )

    def on_cancel(self):
        frappe.db.set_value(DOCTYPE, self.name, {"status": "Cancelled", "invoice_lock": None})
        self.status = "Cancelled"
        self.invoice_lock = None

    @frappe.whitelist()
    def approve_payment(self):
        """Pending Approval -> Approved, as a compare-and-set on the database row (I9)."""
        frappe.only_for(MANAGER_ROLES)
        current = self._stored_state(for_update=True)
        if current.get("docstatus") != 1 or current.get("status") != "Pending Approval":
            frappe.throw(
                _("Only submitted payments with status 'Pending Approval' can be approved (this one is '{0}')").format(
                    current.get("status")
                )
            )
        frappe.db.set_value(DOCTYPE, self.name, "status", "Approved")
        frappe.db.commit()
        self.status = "Approved"
        frappe.msgprint(_("Payment approved"), indicator="green", alert=True)

    @frappe.whitelist()
    def execute_payment(self):
        """Queue the execution. The job claims the order; nothing here writes its status (I2, I3)."""
        self.check_permission("submit")
        current = self._stored_state()
        if current.get("docstatus") != 1 or current.get("status") != "Approved":
            frappe.throw(
                _("Only approved payments can be executed (this one is '{0}')").format(current.get("status"))
            )
        if not is_integration_enabled():
            frappe.throw(_("Banco Inter integration is disabled in Banco Inter Settings. No payment can be sent."))

        from brazil_module.services.banking.payment_service import enqueue_payment_execution

        if enqueue_payment_execution(self.name):
            frappe.msgprint(
                _("Payment execution queued. The status is updated when the bank answers."),
                indicator="blue",
                alert=True,
            )
            return {"status": "queued", "payment_order": self.name}
        frappe.msgprint(
            _("The execution of this payment is already queued or running. Nothing was queued again."),
            indicator="orange",
            alert=True,
        )
        return {"status": "already_queued", "payment_order": self.name}

    @frappe.whitelist()
    def check_bank_status(self):
        """Ask the bank about an order it accepted. The service decides what the answer means."""
        self.check_permission("write")

        from brazil_module.services.banking.payment_service import poll_bank_status

        return poll_bank_status(self.name)

    @frappe.whitelist()
    def resolve_verification(self, outcome, bank_reference="", paid_on=None, note=""):
        """The operator's verdict on a ``Needs Verification`` order: at_bank, paid or not_paid."""
        frappe.only_for(MANAGER_ROLES)

        from brazil_module.services.banking.payment_service import resolve_verification as resolve

        return resolve(
            self.name,
            outcome,
            bank_reference=(bank_reference or "").strip(),
            paid_on=paid_on or None,
            note=(note or "").strip(),
        )

    @frappe.whitelist()
    def create_payment_entry(self):
        """Settle a ``Completed`` order whose Payment Entry failed. Never for an unpaid order (I7)."""
        frappe.only_for(MANAGER_ROLES)
        current = self._stored_state()
        if current.get("docstatus") != 1 or current.get("status") != "Completed":
            frappe.throw(
                _("A Payment Entry is only created for a completed payment (this one is '{0}')").format(
                    current.get("status")
                )
            )

        from brazil_module.services.banking.payment_service import create_payment_entry_for_order

        return create_payment_entry_for_order(self.name)

    def _stored_state(self, for_update: bool = False) -> dict:
        """``status`` and ``docstatus`` as the database has them; empty when the row is gone."""
        return (
            frappe.db.get_value(DOCTYPE, self.name, ["status", "docstatus"], as_dict=True, for_update=for_update)
            or {}
        )
