import os
import frappe
from frappe.model.document import Document


class I8AgentSettings(Document):
    def validate(self):
        if self.default_confidence_threshold < 0 or self.default_confidence_threshold > 1:
            frappe.throw("Confidence threshold must be between 0.0 and 1.0")
        if self.max_requests_per_minute and self.max_requests_per_minute < 1:
            frappe.throw("Max requests per minute must be at least 1")

    @staticmethod
    def get_settings():
        return frappe.get_single("I8 Agent Settings")

    @staticmethod
    def is_enabled():
        return bool(frappe.db.get_single_value("I8 Agent Settings", "enabled"))

    @staticmethod
    def get_api_key():
        key = os.environ.get("ANTHROPIC_API_KEY")
        if key:
            return key
        return frappe.get_single("I8 Agent Settings").get_password("anthropic_api_key")

    @staticmethod
    def get_telegram_token():
        token = os.environ.get("I8_TELEGRAM_BOT_TOKEN")
        if token:
            return token
        return frappe.get_single("I8 Agent Settings").get_password("telegram_bot_token")
