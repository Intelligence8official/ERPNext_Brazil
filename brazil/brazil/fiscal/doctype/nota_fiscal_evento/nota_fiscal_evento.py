# Copyright (c) 2024, Your Company and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime


class NotaFiscalEvento(Document):
    def before_insert(self):
        """Set default values before insert."""
        if not self.event_date:
            self.event_date = now_datetime()

    def validate(self):
        """Validate required fields."""
        if not self.nota_fiscal:
            frappe.throw(_("Nota Fiscal is required for an event"))
        if not self.event_type:
            frappe.throw(_("Event Type is required"))
