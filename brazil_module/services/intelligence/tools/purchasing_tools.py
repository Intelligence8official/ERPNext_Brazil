import frappe

TOOL_SCHEMAS = [
    {
        "name": "p2p.create_purchase_order",
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
        "name": "p2p.send_po_to_supplier",
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
        "name": "p2p.list_due_invoices",
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
    if tool_name == "p2p.create_purchase_order":
        po_data = {
            "supplier": args["supplier"],
            "schedule_date": args["required_by"],
            "items": [
                {
                    "item_code": item["item_code"],
                    "qty": item["qty"],
                    "rate": item["rate"],
                    "schedule_date": args["required_by"],
                }
                for item in args["items"]
            ],
        }
        return executor.execute("Purchase Order", "create", po_data)
    elif tool_name == "p2p.send_po_to_supplier":
        po = frappe.get_doc("Purchase Order", args["purchase_order"])
        supplier_profile = frappe.get_all(
            "I8 Supplier Profile",
            filters={"supplier": po.supplier},
            fields=["contact_email", "email_template"],
            limit=1,
        )
        if supplier_profile and supplier_profile[0].get("contact_email"):
            frappe.sendmail(
                recipients=[supplier_profile[0]["contact_email"]],
                subject=f"Purchase Order {po.name}",
                message=args.get("message", f"Please find attached PO {po.name}."),
                reference_doctype="Purchase Order",
                reference_name=po.name,
            )
            return {"status": "sent", "recipient": supplier_profile[0]["contact_email"]}
        return {"status": "no_contact", "message": "No supplier profile or contact email found"}
    elif tool_name == "p2p.list_due_invoices":
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
