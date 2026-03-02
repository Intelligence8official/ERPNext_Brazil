"""Inter PIX Charge - PIX cobranca tracking."""

import frappe
from frappe import _
from frappe.model.document import Document


class InterPIXCharge(Document):

    def validate(self):
        if self.valor and self.valor <= 0:
            frappe.throw(_("Valor must be greater than zero"))

    @frappe.whitelist()
    def check_status(self):
        """Check PIX charge status at the bank."""
        from brazil.services.banking.pix_service import poll_pix_charge_status

        result = poll_pix_charge_status(self.name)
        self.reload()
        return result
