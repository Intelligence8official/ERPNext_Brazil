import frappe

TOOL_SCHEMAS = [
    {
        "name": "comm.send_email",
        "description": "Send an email to a recipient",
        "input_schema": {
            "type": "object",
            "properties": {
                "recipients": {"type": "array", "items": {"type": "string"}},
                "subject": {"type": "string"},
                "message": {"type": "string"},
                "reference_doctype": {"type": "string"},
                "reference_name": {"type": "string"},
            },
            "required": ["recipients", "subject", "message"],
        },
    },
    {
        "name": "comm.send_notification",
        "description": "Send a Frappe notification to a user",
        "input_schema": {
            "type": "object",
            "properties": {
                "user": {"type": "string"},
                "message": {"type": "string"},
                "document_type": {"type": "string"},
                "document_name": {"type": "string"},
            },
            "required": ["user", "message"],
        },
    },
]


def execute_tool(tool_name: str, args: dict, executor) -> dict:
    if tool_name == "comm.send_email":
        frappe.sendmail(
            recipients=args["recipients"],
            subject=args["subject"],
            message=args["message"],
            reference_doctype=args.get("reference_doctype"),
            reference_name=args.get("reference_name"),
        )
        return {"status": "sent", "recipients": args["recipients"]}
    elif tool_name == "comm.send_notification":
        doc = frappe.new_doc("Notification Log")
        doc.for_user = args["user"]
        doc.subject = args["message"]
        doc.document_type = args.get("document_type")
        doc.document_name = args.get("document_name")
        doc.type = "Alert"
        doc.insert(ignore_permissions=True)
        return {"status": "sent", "user": args["user"]}
    raise ValueError(f"Unknown tool: {tool_name}")
