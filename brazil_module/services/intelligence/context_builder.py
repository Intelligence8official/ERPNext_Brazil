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

    def _get_supplier_profile(self, supplier: str) -> dict | None:
        profiles = frappe.get_all(
            "I8 Supplier Profile",
            filters={"supplier": supplier},
            fields=["name"],
            limit=1,
        )
        if not profiles:
            return None
        doc = frappe.get_doc("I8 Supplier Profile", profiles[0]["name"])
        return doc.as_dict()
