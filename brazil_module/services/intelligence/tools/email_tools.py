import frappe

TOOL_SCHEMAS = [
    {
        "name": "email-classify",
        "description": "Classify an email into categories: FISCAL, COMMERCIAL, FINANCIAL, OPERATIONAL, SPAM, UNCERTAIN",
        "input_schema": {
            "type": "object",
            "properties": {
                "subject": {"type": "string"},
                "sender": {"type": "string"},
                "body_snippet": {"type": "string", "description": "First 500 chars of email body"},
            },
            "required": ["subject"],
        },
    },
    {
        "name": "email-search",
        "description": "Search emails (Communication documents) with filters",
        "input_schema": {
            "type": "object",
            "properties": {
                "sender": {"type": "string"},
                "subject_contains": {"type": "string"},
                "limit": {"type": "integer", "default": 10},
            },
        },
    },
    {
        "name": "email-get_content",
        "description": "Get full content of an email by Communication name",
        "input_schema": {
            "type": "object",
            "properties": {
                "communication": {"type": "string"},
            },
            "required": ["communication"],
        },
    },
]


def execute_tool(tool_name: str, args: dict, executor) -> dict:
    if tool_name == "email-classify":
        # Classification is done by the LLM itself — this tool just marks the result
        return {"status": "classified", "note": "Classification determined by agent reasoning"}
    elif tool_name == "email-search":
        filters = {"communication_type": "Communication", "sent_or_received": "Received"}
        if args.get("sender"):
            filters["sender"] = ["like", f"%{args['sender']}%"]
        if args.get("subject_contains"):
            filters["subject"] = ["like", f"%{args['subject_contains']}%"]
        emails = frappe.get_all(
            "Communication",
            filters=filters,
            fields=["name", "subject", "sender", "communication_date"],
            limit_page_length=args.get("limit", 10),
            order_by="communication_date desc",
        )
        return {"data": emails}
    elif tool_name == "email-get_content":
        doc = frappe.get_doc("Communication", args["communication"])
        return {
            "name": doc.name,
            "subject": doc.subject,
            "sender": doc.sender,
            "content": doc.content,
            "communication_date": str(doc.communication_date),
        }
    raise ValueError(f"Unknown tool: {tool_name}")
