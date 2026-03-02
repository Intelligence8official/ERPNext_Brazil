"""Inter Boleto - BoletoPIX tracking document."""

import frappe
from frappe import _
from frappe.model.document import Document


class InterBoleto(Document):

    def validate(self):
        if self.valor_nominal and self.valor_nominal <= 0:
            frappe.throw(_("Valor Nominal must be greater than zero"))

        if self.data_vencimento and self.data_emissao:
            if self.data_vencimento < self.data_emissao:
                frappe.throw(_("Due date cannot be before issue date"))

    def before_save(self):
        self.set_status_color()

    def set_status_color(self):
        """Set indicator color based on status."""
        colors = {
            "Pending": "yellow",
            "Registered": "blue",
            "Paid": "green",
            "Overdue": "orange",
            "Cancelled": "grey",
            "Error": "red",
        }
        self.indicator_color = colors.get(self.status, "grey")

    @frappe.whitelist()
    def check_status(self):
        """Check payment status at the bank."""
        from brazil.services.banking.boleto_service import poll_boleto_status

        result = poll_boleto_status(self.name)
        self.reload()
        return result

    @frappe.whitelist()
    def cancel_boleto(self):
        """Cancel this boleto at the bank."""
        from brazil.services.banking.boleto_service import cancel_boleto

        result = cancel_boleto(self.name, reason="Cancelled by user")
        self.reload()
        return result

    @frappe.whitelist()
    def download_pdf(self):
        """Download boleto PDF from the bank."""
        from brazil.services.banking.boleto_service import download_boleto_pdf

        result = download_boleto_pdf(self.name)
        self.reload()
        return result
