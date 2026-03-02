"""Banco Inter Settings - Global singleton configuration."""

import frappe
from frappe.model.document import Document


class BancoInterSettings(Document):

    def validate(self):
        if self.sync_interval_hours and self.sync_interval_hours < 1:
            frappe.throw("Sync interval must be at least 1 hour")
        if self.sync_days_back and self.sync_days_back < 1:
            frappe.throw("Sync days back must be at least 1")
        if self.pix_expiration_seconds and self.pix_expiration_seconds < 60:
            frappe.throw("PIX expiration must be at least 60 seconds")

    @staticmethod
    def get_settings():
        """Get the singleton settings document."""
        return frappe.get_single("Banco Inter Settings")

    @staticmethod
    def is_enabled():
        """Check if the integration is enabled."""
        return bool(frappe.db.get_single_value("Banco Inter Settings", "enabled"))
