"""Inter Sync Log - Statement and status sync tracking."""

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


class InterSyncLog(Document):
    def before_insert(self):
        """Set default values."""
        if not self.started_at:
            self.started_at = now_datetime()
