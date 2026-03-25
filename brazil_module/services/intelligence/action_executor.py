import frappe

ACTION_ALLOWLIST = {
    "Purchase Order": ["create", "submit", "cancel"],
    "Purchase Invoice": ["create", "submit"],
    "Payment Entry": ["create", "submit"],
    "Journal Entry": ["create", "submit"],
    "Inter Payment Order": ["create"],
    "Nota Fiscal": ["read", "update_status"],
    "Bank Transaction": ["read", "reconcile"],
    "Supplier": ["read", "create", "update"],
    "Item": ["read", "create"],
    "Communication": ["read", "create"],
}

STATUS_FIELD_ALLOWLIST = {
    "Nota Fiscal": [
        "processing_status",
        "supplier_status",
        "item_creation_status",
        "po_status",
        "invoice_status",
    ],
}


class ActionExecutor:
    """Sandboxed executor that enforces a DocType/operation allowlist.

    The agent is NEVER permitted to delete documents. Every other operation
    must be explicitly listed in ACTION_ALLOWLIST for the target DocType.
    """

    def execute(self, doctype: str, operation: str, data: dict | None = None) -> dict:
        """Validate permission then dispatch to the appropriate handler.

        Args:
            doctype: Frappe DocType name (e.g. "Purchase Order").
            operation: Operation name (create, read, update, submit, cancel,
                       update_status, reconcile).
            data: Payload dict – never mutated by this method.

        Returns:
            dict with at minimum a "name" key describing the affected document.

        Raises:
            PermissionError: When the operation or doctype is not permitted.
            ValueError: When required payload fields are missing.
        """
        if operation == "delete":
            raise PermissionError("Agent is never allowed to delete documents")

        allowed_ops = ACTION_ALLOWLIST.get(doctype)
        if allowed_ops is None:
            raise PermissionError(f"Agent has no access to DocType: {doctype}")

        if operation not in allowed_ops:
            raise PermissionError(
                f"Operation '{operation}' not allowed on {doctype}"
            )

        handler = getattr(self, f"_do_{operation}", None)
        if handler is None:
            raise PermissionError(f"No handler for operation: {operation}")

        return handler(doctype, data or {})

    # ------------------------------------------------------------------
    # Private handlers — one per allowed operation
    # ------------------------------------------------------------------

    def _do_create(self, doctype: str, data: dict) -> dict:
        doc = frappe.new_doc(doctype)
        # Build a new dict so we never mutate the caller's data
        doc.update({k: v for k, v in data.items()})
        doc.insert(ignore_permissions=True)
        return {"name": doc.name, "doctype": doctype}

    def _do_read(self, doctype: str, data: dict) -> dict:
        doc = frappe.get_doc(doctype, data.get("name"))
        return doc.as_dict()

    def _do_submit(self, doctype: str, data: dict) -> dict:
        doc = frappe.get_doc(doctype, data.get("name"))
        doc.submit()
        return {"name": doc.name, "status": "Submitted"}

    def _do_cancel(self, doctype: str, data: dict) -> dict:
        doc = frappe.get_doc(doctype, data.get("name"))
        doc.cancel()
        return {"name": doc.name, "status": "Cancelled"}

    def _do_update(self, doctype: str, data: dict) -> dict:
        name = data.get("name")
        if not name:
            raise ValueError("'name' is required for update operation")
        # Immutable pattern: build a new dict without "name"
        fields = {k: v for k, v in data.items() if k != "name"}
        doc = frappe.get_doc(doctype, name)
        doc.update(fields)
        doc.save(ignore_permissions=True)
        return {"name": doc.name}

    def _do_update_status(self, doctype: str, data: dict) -> dict:
        field = data.get("field")
        allowed_fields = STATUS_FIELD_ALLOWLIST.get(doctype, [])
        if field not in allowed_fields:
            raise PermissionError(
                f"Agent cannot update field '{field}' on {doctype}. "
                f"Allowed fields: {allowed_fields}"
            )
        frappe.db.set_value(doctype, data["name"], field, data["value"])
        return {"name": data["name"]}

    def _do_reconcile(self, doctype: str, data: dict) -> dict:
        from brazil_module.services.banking.reconciliation import batch_reconcile

        return batch_reconcile(data["bank_account"])
