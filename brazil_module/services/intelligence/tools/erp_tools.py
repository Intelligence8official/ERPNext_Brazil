import frappe

TOOL_SCHEMAS = [
    {
        "name": "erp-read_document",
        "description": "Read a document from ERPNext by doctype and name",
        "input_schema": {
            "type": "object",
            "properties": {
                "doctype": {"type": "string", "description": "The DocType name"},
                "name": {"type": "string", "description": "The document name/ID"},
            },
            "required": ["doctype", "name"],
        },
    },
    {
        "name": "erp-list_documents",
        "description": "List documents from ERPNext with optional filters",
        "input_schema": {
            "type": "object",
            "properties": {
                "doctype": {"type": "string"},
                "filters": {"type": "object", "description": "Filter conditions"},
                "fields": {"type": "array", "items": {"type": "string"}},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["doctype"],
        },
    },
]


def execute_tool(tool_name: str, args: dict, executor) -> dict:
    if tool_name == "erp-read_document":
        return executor.execute(args["doctype"], "read", {"name": args["name"]})
    elif tool_name == "erp-list_documents":
        return {
            "data": frappe.get_all(
                args["doctype"],
                filters=args.get("filters"),
                fields=args.get("fields", ["name"]),
                limit_page_length=args.get("limit", 20),
            )
        }
    raise ValueError(f"Unknown tool: {tool_name}")
