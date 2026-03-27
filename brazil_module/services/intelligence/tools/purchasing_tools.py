import frappe


def _resolve_item_code(item_code: str) -> str:
    """Resolve an item code to its exact name in ERPNext.

    The LLM may pass item_name instead of the full item_code (e.g., 'DEVELOPMENT SERVICES'
    instead of 'DEVELOPMENT SERVICES (ATIVO)'). This function tries exact match first,
    then falls back to item_name match, then fuzzy search.
    """
    # Exact match
    if frappe.db.exists("Item", item_code):
        return item_code

    # Match by item_name
    match = frappe.db.get_value("Item", {"item_name": item_code}, "name")
    if match:
        return match

    # Fuzzy: search by partial name
    matches = frappe.get_all(
        "Item",
        filters={"name": ["like", f"%{item_code}%"]},
        fields=["name"],
        limit=1,
    )
    if matches:
        return matches[0]["name"]

    # Last resort: return as-is and let ERPNext validate
    return item_code


TOOL_SCHEMAS = [
    {
        "name": "p2p-create_purchase_order",
        "description": "Create a Purchase Order in ERPNext",
        "input_schema": {
            "type": "object",
            "properties": {
                "supplier": {"type": "string"},
                "required_by": {"type": "string", "format": "date"},
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "item_code": {"type": "string"},
                            "qty": {"type": "number"},
                            "rate": {"type": "number"},
                        },
                        "required": ["item_code", "qty", "rate"],
                    },
                },
            },
            "required": ["supplier", "required_by", "items"],
        },
    },
    {
        "name": "p2p-send_po_to_supplier",
        "description": "Send email to supplier with PO details",
        "input_schema": {
            "type": "object",
            "properties": {
                "purchase_order": {"type": "string", "description": "PO name"},
                "message": {"type": "string", "description": "Optional custom message"},
            },
            "required": ["purchase_order"],
        },
    },
    {
        "name": "p2p-list_due_invoices",
        "description": "List Purchase Invoices due for payment within N days",
        "input_schema": {
            "type": "object",
            "properties": {
                "days_ahead": {"type": "integer", "default": 7},
            },
        },
    },
]


def execute_tool(tool_name: str, args: dict, executor) -> dict:
    if tool_name == "p2p-create_purchase_order":
        from datetime import date as _date

        required_by = args["required_by"]
        today = _date.today().isoformat()

        # ERPNext requires schedule_date >= transaction_date
        # If the due date is in the past, use today instead
        if required_by < today:
            required_by = today

        po_data = {
            "supplier": args["supplier"],
            "schedule_date": required_by,
            "items": [
                {
                    "item_code": _resolve_item_code(item["item_code"]),
                    "qty": item["qty"],
                    "rate": item["rate"],
                    "schedule_date": required_by,
                }
                for item in args["items"]
            ],
        }
        return executor.execute("Purchase Order", "create", po_data)
    elif tool_name == "p2p-send_po_to_supplier":
        po = frappe.get_doc("Purchase Order", args["purchase_order"])
        # Get email from Supplier's native email_id field
        contact_email = frappe.db.get_value("Supplier", po.supplier, "email_id")
        if contact_email:
            frappe.sendmail(
                recipients=[contact_email],
                subject=f"Purchase Order {po.name}",
                message=args.get("message", f"Please find attached PO {po.name}."),
                reference_doctype="Purchase Order",
                reference_name=po.name,
            )
            return {"status": "sent", "recipient": contact_email}
        return {"status": "no_contact", "message": "Supplier has no email_id configured"}
    elif tool_name == "p2p-list_due_invoices":
        from datetime import date, timedelta
        days = args.get("days_ahead", 7)
        due_date = (date.today() + timedelta(days=days)).isoformat()
        invoices = frappe.get_all(
            "Purchase Invoice",
            filters={"docstatus": 1, "outstanding_amount": [">", 0], "due_date": ["<=", due_date]},
            fields=["name", "supplier", "grand_total", "outstanding_amount", "due_date"],
            order_by="due_date asc",
        )
        return {"data": invoices}
    raise ValueError(f"Unknown tool: {tool_name}")
