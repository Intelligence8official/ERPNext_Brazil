import frappe


MAX_CONVERSATION_MESSAGES = 20


class ContextBuilder:
    def build(self, event_type: str, event_data: dict) -> dict:
        context = {
            "system_context": self._get_system_context(),
            "module_context": self._get_module_context(event_data.get("module", "")),
            "event_data": event_data,
            "history": [],
            "supplier_profile": None,
        }

        if "conversation_name" in event_data:
            context["history"] = self._get_conversation_context(event_data["conversation_name"])

        if "supplier" in event_data:
            context["supplier_profile"] = self._get_supplier_profile(event_data["supplier"])

        if "recurring_expense" in event_data:
            context["recurring_expense"] = self._get_recurring_expense(event_data["recurring_expense"])

        return context

    def _get_system_context(self) -> str:
        today = frappe.utils.today()
        return (
            f"Date: {today}\n"
            f"System: ERPNext Brazil (Intelligence8 Agent)\n"
            f"You are an autonomous ERP operator. Make decisions and execute actions using your tools."
        )

    def _get_module_context(self, module_name: str) -> str:
        if not module_name:
            return ""
        registries = frappe.get_all(
            "I8 Module Registry",
            filters={"module_name": module_name, "enabled": 1},
            fields=["name"],
            limit=1,
        )
        if not registries:
            return ""
        doc = frappe.get_doc("I8 Module Registry", registries[0]["name"])
        return doc.context_prompt or ""

    def _get_conversation_context(self, conversation_name: str) -> list:
        try:
            conv = frappe.get_doc("I8 Conversation", conversation_name)
        except Exception:
            return []
        messages = conv.messages or []
        # Sliding window: return last N messages
        recent = messages[-MAX_CONVERSATION_MESSAGES:]
        return [
            {
                "content": m.content,
                "timestamp": str(m.timestamp),
                "actor": m.actor,
                "channel": m.channel,
            }
            if hasattr(m, "content")
            else m
            for m in recent
        ]

    def _get_recurring_expense(self, expense_name: str) -> dict | None:
        try:
            doc = frappe.get_doc("I8 Recurring Expense", expense_name)
            result = {
                "name": doc.name,
                "title": doc.title,
                "supplier": doc.supplier,
                "document_type": doc.document_type,
                "estimated_amount": float(doc.estimated_amount or 0),
                "currency": doc.currency,
                "frequency": doc.frequency,
                "day_of_month": doc.day_of_month,
                "next_due": str(doc.next_due) if doc.next_due else None,
                "notify_supplier": doc.notify_supplier,
                "items": [],
            }
            for item in (doc.items or []):
                result["items"].append({
                    "item_code": item.item_code,
                    "qty": float(item.qty or 0),
                    "rate": float(item.rate or 0),
                })
            return result
        except Exception:
            return None

    def _get_supplier_profile(self, supplier: str) -> dict | None:
        """Get supplier I8 configuration from Supplier custom fields."""
        if not frappe.db.exists("Supplier", supplier):
            # Try by tax_id/CNPJ
            supplier = frappe.db.get_value("Supplier", {"tax_id": ["like", f"%{supplier}%"]}, "name")
            if not supplier:
                return None

        fields = [
            "name", "supplier_name", "tax_id",
            "pix_key", "pix_key_type",
            "i8_expected_nf_days", "i8_nf_due_day",
            "i8_follow_up_after_days", "i8_max_follow_ups",
            "i8_auto_pay", "i8_agent_notes",
            "default_payment_terms_template",
        ]
        data = frappe.db.get_value("Supplier", supplier, fields, as_dict=True)
        return data
