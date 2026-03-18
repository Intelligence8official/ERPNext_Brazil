"""Inter Payment Order - Outbound payment tracking (submittable)."""

import frappe
from frappe import _
from frappe.model.document import Document


class InterPaymentOrder(Document):

    def validate(self):
        if self.amount and self.amount <= 0:
            frappe.throw(_("Amount must be greater than zero"))
        self.validate_payment_details()

    def validate_payment_details(self):
        """Validate that required fields are set based on payment type."""
        if self.payment_type == "PIX" and not self.pix_key:
            frappe.throw(_("PIX Key is required for PIX payments"))
        elif self.payment_type == "TED":
            if not self.recipient_bank_code:
                frappe.throw(_("Bank Code is required for TED transfers"))
            if not self.recipient_agency:
                frappe.throw(_("Agency is required for TED transfers"))
            if not self.recipient_account:
                frappe.throw(_("Account is required for TED transfers"))
        elif self.payment_type == "Boleto Payment" and not self.barcode:
            frappe.throw(_("Barcode is required for Boleto Payment"))

    def on_submit(self):
        """Handle submission - check if approval is required."""
        approval_required = frappe.db.get_single_value(
            "Banco Inter Settings", "payment_approval_required"
        )
        if approval_required:
            self.status = "Pending Approval"
        else:
            self.status = "Approved"
        self.db_set("status", self.status, update_modified=False)

    def on_cancel(self):
        self.db_set("status", "Cancelled", update_modified=False)

    @frappe.whitelist()
    def approve_payment(self):
        """Approve the payment for execution."""
        if self.status != "Pending Approval":
            frappe.throw(_("Only payments with status 'Pending Approval' can be approved"))
        self.db_set("status", "Approved")
        frappe.msgprint(_("Payment approved"), indicator="green", alert=True)

    @frappe.whitelist()
    def execute_payment(self):
        """Execute the approved payment via Banco Inter API."""
        if self.status not in ("Approved",):
            frappe.throw(_("Only approved payments can be executed"))

        from brazil_module.services.banking.payment_service import execute_payment_order

        self.db_set("status", "Processing")
        frappe.enqueue(
            execute_payment_order,
            payment_order_name=self.name,
            queue="short",
        )
        frappe.msgprint(
            _("Payment execution initiated. Check status for updates."),
            indicator="blue",
            alert=True,
        )
