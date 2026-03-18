"""Inter API Log - Audit trail for all API calls."""

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


class InterAPILog(Document):
    def before_insert(self):
        """Set defaults and truncate large fields."""
        if not self.timestamp:
            self.timestamp = now_datetime()

        if self.response_body and len(self.response_body) > 5000:
            self.response_body = self.response_body[:5000] + "\n... [truncated]"
