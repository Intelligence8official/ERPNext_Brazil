import frappe
from frappe.model.document import Document


class I8DecisionLog(Document):
    def before_save(self):
        if not self.is_new() and self.docstatus == 1:
            frappe.throw("Submitted Decision Log entries are immutable and cannot be modified.")

    def on_trash(self):
        frappe.throw("Decision Log entries cannot be deleted.")

    def resolve(self, actor, result, channel="system", human_override=False, human_feedback=None):
        if self.docstatus != 0:
            frappe.throw("Only draft (Pending) decision logs can be resolved.")
        self.actor = actor
        self.result = result
        self.channel = channel
        self.human_override = human_override
        self.human_feedback = human_feedback
        self.save(ignore_permissions=True)
        self.submit()
