# Copyright (c) 2024, Your Company and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class NotaFiscalItem(Document):
    def validate(self):
        """Validate item fields."""
        if not self.descricao:
            frappe.throw(_("Item description (descricao) is required"))

        if self.quantidade is not None and self.quantidade <= 0:
            frappe.throw(_("Quantity must be greater than zero"))

        if self.ncm:
            clean_ncm = self.ncm.replace(".", "")
            if len(clean_ncm) != 8 or not clean_ncm.isdigit():
                frappe.throw(_("NCM must be 8 digits"))

        if self.cfop:
            clean_cfop = str(self.cfop).replace(".", "")
            if len(clean_cfop) != 4 or not clean_cfop.isdigit():
                frappe.throw(_("CFOP must be 4 digits"))
