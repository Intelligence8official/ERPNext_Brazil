"""
Migration patch: brazil_nf + banco_inter → brazil (Fiscal + Bancos modules).

Updates module references in all relevant DocTypes so existing data
continues to work under the new unified app.
"""

import frappe


def execute():
    """Migrate module references from old apps to new unified modules."""
    # Map old module names to new ones
    module_map = {
        "Brazil NF": "Fiscal",
        "Banco Inter": "Bancos",
    }

    # Tables that store module references
    doctypes_with_module = [
        "DocType",
        "Custom Field",
        "Property Setter",
        "Workspace",
        "Report",
    ]

    for old_module, new_module in module_map.items():
        for dt in doctypes_with_module:
            try:
                count = frappe.db.sql(
                    f"UPDATE `tab{dt}` SET module = %s WHERE module = %s",
                    (new_module, old_module),
                )
                if count:
                    frappe.logger().info(
                        f"Migrated {dt}: {old_module} → {new_module}"
                    )
            except Exception as e:
                frappe.logger().warning(
                    f"Could not migrate {dt} module refs: {e}"
                )

    # Create new Module Def entries if they don't exist
    for module_name in ("Fiscal", "Bancos"):
        if not frappe.db.exists("Module Def", module_name):
            mod = frappe.new_doc("Module Def")
            mod.module_name = module_name
            mod.app_name = "brazil_module"
            mod.insert(ignore_permissions=True)
            frappe.logger().info(f"Created Module Def: {module_name}")

    # Remove old Module Def entries
    for old_module in ("Brazil NF", "Banco Inter"):
        if frappe.db.exists("Module Def", old_module):
            frappe.delete_doc("Module Def", old_module, ignore_permissions=True)
            frappe.logger().info(f"Deleted old Module Def: {old_module}")

    # Update Installed Application record
    if frappe.db.exists("Installed Application", "brazil_nf"):
        frappe.db.set_value(
            "Installed Application", "brazil_nf",
            {"app_name": "brazil_module", "app_title": "Brazil"},
        )

    if frappe.db.exists("Installed Application", "banco_inter"):
        try:
            frappe.delete_doc(
                "Installed Application", "banco_inter",
                ignore_permissions=True,
            )
        except Exception:
            pass

    frappe.db.commit()
