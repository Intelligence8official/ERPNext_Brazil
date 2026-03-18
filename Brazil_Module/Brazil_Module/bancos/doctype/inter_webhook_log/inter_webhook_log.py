"""Inter Webhook Log - Incoming webhook audit trail."""

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


class InterWebhookLog(Document):
    def before_insert(self):
        """Set defaults and truncate large fields."""
        if not self.received_at:
            self.received_at = now_datetime()

        if self.request_body and len(self.request_body) > 10000:
            self.request_body = self.request_body[:10000] + "\n... [truncated]"
