import frappe


class ChannelRouter:
    def route_message(
        self,
        channel: str,
        direction: str,
        actor: str,
        content: str,
        related_doctype: str | None = None,
        related_docname: str | None = None,
        telegram_message_id: str | None = None,
    ) -> str:
        conversation = self._get_or_create_conversation(related_doctype, related_docname)
        conversation.append("messages", {
            "channel": channel,
            "direction": direction,
            "actor": actor,
            "content": content,
            "timestamp": frappe.utils.now_datetime(),
            "related_doctype": related_doctype,
            "related_docname": related_docname,
            "telegram_message_id": telegram_message_id,
        })
        conversation.save(ignore_permissions=True)
        return conversation.name

    def _get_or_create_conversation(
        self,
        related_doctype: str | None = None,
        related_docname: str | None = None,
    ):
        # Try to find existing active conversation for this document
        if related_doctype and related_docname:
            existing = frappe.get_all(
                "I8 Conversation",
                filters={
                    "related_doctype": related_doctype,
                    "related_docname": related_docname,
                    "status": "Active",
                },
                fields=["name"],
                limit=1,
            )
            if existing:
                return frappe.get_doc("I8 Conversation", existing[0]["name"])

        # Try to find any active general conversation (no related doc)
        if not related_doctype:
            existing = frappe.get_all(
                "I8 Conversation",
                filters={"status": "Active", "related_doctype": ["is", "not set"]},
                fields=["name"],
                limit=1,
            )
            if existing:
                return frappe.get_doc("I8 Conversation", existing[0]["name"])

        # Create new conversation
        doc = frappe.new_doc("I8 Conversation")
        doc.subject = f"{related_doctype or 'General'}: {related_docname or 'New conversation'}"
        doc.status = "Active"
        doc.related_doctype = related_doctype
        doc.related_docname = related_docname
        doc.insert(ignore_permissions=True)
        return doc
