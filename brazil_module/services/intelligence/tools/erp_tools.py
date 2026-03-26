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
    {
        "name": "erp-get_report_data",
        "description": "Get aggregated report data from ERPNext. Use for questions like 'how much did we spend on X' or 'what are the totals for Y'",
        "input_schema": {
            "type": "object",
            "properties": {
                "doctype": {"type": "string", "description": "DocType to query (e.g., Purchase Invoice, Sales Invoice, Payment Entry)"},
                "filters": {"type": "object", "description": "Filter conditions"},
                "group_by": {"type": "string", "description": "Field to group by (e.g., supplier, cost_center)"},
                "aggregate": {"type": "string", "description": "Aggregate function: SUM, COUNT, AVG"},
                "aggregate_field": {"type": "string", "description": "Field to aggregate (e.g., grand_total, outstanding_amount)"},
                "order_by": {"type": "string", "description": "Simple field name to order by"},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["doctype"],
        },
    },
    {
        "name": "erp-get_account_balance",
        "description": "Get the balance of a specific account or group of accounts from GL Entry",
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string", "description": "Account name or partial match"},
                "from_date": {"type": "string", "description": "Start date YYYY-MM-DD"},
                "to_date": {"type": "string", "description": "End date YYYY-MM-DD"},
            },
        },
    },
]


_FINANCIAL_DOCTYPES = frozenset({"Purchase Invoice", "Sales Invoice", "Payment Entry", "Journal Entry"})


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
    elif tool_name == "erp-get_report_data":
        doctype = args["doctype"]
        filters = dict(args.get("filters") or {})

        # Only allow docstatus=1 (submitted) for financial queries
        if doctype in _FINANCIAL_DOCTYPES:
            filters.setdefault("docstatus", 1)

        group_by = args.get("group_by")
        aggregate = (args.get("aggregate") or "SUM").upper()
        agg_field = args.get("aggregate_field") or "grand_total"

        if aggregate not in ("SUM", "COUNT", "AVG"):
            aggregate = "SUM"

        if group_by:
            # Validate field names are simple identifiers (prevent SQL injection)
            if not group_by.isidentifier() or not agg_field.isidentifier():
                return {"error": "Invalid field names"}

            data = frappe.db.sql(
                f"""
                SELECT `{group_by}`, {aggregate}(`{agg_field}`) as value, COUNT(*) as count
                FROM `tab{doctype}`
                WHERE docstatus = %(docstatus)s
                GROUP BY `{group_by}`
                ORDER BY value DESC
                LIMIT %(limit)s
                """,
                {"docstatus": filters.get("docstatus", 1), "limit": args.get("limit", 20)},
                as_dict=True,
            )
            return {"data": data, "aggregate": aggregate, "field": agg_field, "grouped_by": group_by}
        else:
            data = frappe.get_all(
                doctype,
                filters=filters,
                fields=["name", agg_field] if agg_field != "name" else ["name"],
                limit_page_length=args.get("limit", 20),
                order_by=args.get("order_by", "creation desc"),
            )
            return {"data": data}

    elif tool_name == "erp-get_account_balance":
        account = args.get("account", "")
        from_date = args.get("from_date")
        to_date = args.get("to_date")

        date_filter = ""
        query_params: dict = {"account": f"%{account}%"}

        if from_date and to_date:
            date_filter = "AND posting_date BETWEEN %(from_date)s AND %(to_date)s"
            query_params["from_date"] = from_date
            query_params["to_date"] = to_date
        elif from_date:
            date_filter = "AND posting_date >= %(from_date)s"
            query_params["from_date"] = from_date
        elif to_date:
            date_filter = "AND posting_date <= %(to_date)s"
            query_params["to_date"] = to_date

        data = frappe.db.sql(
            f"""
            SELECT account,
                   SUM(debit) as total_debit,
                   SUM(credit) as total_credit,
                   SUM(debit) - SUM(credit) as balance
            FROM `tabGL Entry`
            WHERE account LIKE %(account)s AND is_cancelled = 0
            {date_filter}
            GROUP BY account
            ORDER BY balance DESC
            """,
            query_params,
            as_dict=True,
        )
        return {"data": data}

    raise ValueError(f"Unknown tool: {tool_name}")
