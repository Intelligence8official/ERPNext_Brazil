"""
Installation hooks for Brazil module.
Merges custom fields and roles from Fiscal + Bancos sub-modules.
"""

import frappe
from frappe import _


def after_install():
    """Post-installation setup."""
    create_custom_fields()
    create_roles()
    setup_workspace()
    setup_desktop_icons()


def after_migrate():
    """Run after bench migrate."""
    create_custom_fields()
    setup_workspace()
    setup_desktop_icons()
    setup_workspace()


def create_custom_fields():
    """Create custom fields on standard ERPNext doctypes."""
    # Fiscal custom fields (module: Fiscal)
    fiscal_fields = {
        "Supplier": [
            {
                "fieldname": "brazil_section",
                "fieldtype": "Section Break",
                "label": "Brazil",
                "insert_after": "tax_id",
                "collapsible": 1
            },
            {
                "fieldname": "inscricao_estadual",
                "fieldtype": "Data",
                "label": "Inscricao Estadual (IE)",
                "insert_after": "brazil_section",
                "description": "State Registration Number"
            },
            {
                "fieldname": "inscricao_municipal",
                "fieldtype": "Data",
                "label": "Inscricao Municipal (IM)",
                "insert_after": "inscricao_estadual",
                "description": "Municipal Registration Number"
            }
        ],
        "Item": [
            {
                "fieldname": "brazil_section",
                "fieldtype": "Section Break",
                "label": "Brazil Fiscal",
                "insert_after": "description",
                "collapsible": 1
            },
            {
                "fieldname": "ncm_code",
                "fieldtype": "Data",
                "label": "NCM Code",
                "insert_after": "brazil_section",
                "description": "Nomenclatura Comum do Mercosul (8 digits)"
            },
            {
                "fieldname": "cest_code",
                "fieldtype": "Data",
                "label": "CEST Code",
                "insert_after": "ncm_code",
                "description": "Codigo Especificador da Substituicao Tributaria (7 digits)"
            },
            {
                "fieldname": "origem_mercadoria",
                "fieldtype": "Select",
                "label": "Product Origin",
                "insert_after": "cest_code",
                "options": "\n0 - Nacional\n1 - Estrangeira - Importacao Direta\n2 - Estrangeira - Adquirida no Mercado Interno\n3 - Nacional com mais de 40% conteudo importado\n4 - Nacional conforme processos produtivos\n5 - Nacional com conteudo importado inferior a 40%\n6 - Estrangeira sem similar nacional\n7 - Estrangeira com similar nacional\n8 - Nacional com conteudo importado superior a 70%",
                "description": "Origin code for ICMS calculation"
            },
            {
                "fieldname": "custom_codigo_servico",
                "fieldtype": "Data",
                "label": "Service Code (cTribNac)",
                "insert_after": "origem_mercadoria",
                "description": "National Service Code for NFS-e (6 digits)"
            }
        ],
        "Purchase Invoice": [
            {
                "fieldname": "brazil_nf_section",
                "fieldtype": "Section Break",
                "label": "Nota Fiscal Reference",
                "insert_after": "bill_date",
                "collapsible": 1
            },
            {
                "fieldname": "nota_fiscal",
                "fieldtype": "Link",
                "label": "Nota Fiscal",
                "options": "Nota Fiscal",
                "insert_after": "brazil_nf_section",
                "read_only": 1
            },
            {
                "fieldname": "chave_de_acesso",
                "fieldtype": "Data",
                "label": "Chave de Acesso",
                "insert_after": "nota_fiscal",
                "read_only": 1,
                "description": "44-digit NF-e/CT-e/NFS-e access key"
            }
        ],
        "Purchase Order": [
            {
                "fieldname": "brazil_nf_section",
                "fieldtype": "Section Break",
                "label": "Nota Fiscal Reference",
                "insert_after": "transaction_date",
                "collapsible": 1
            },
            {
                "fieldname": "nota_fiscal",
                "fieldtype": "Link",
                "label": "Nota Fiscal",
                "options": "Nota Fiscal",
                "insert_after": "brazil_nf_section",
                "read_only": 1
            }
        ],
        "Communication": [
            {
                "fieldname": "nf_processed",
                "fieldtype": "Check",
                "label": "NF Processed",
                "default": "0",
                "hidden": 1,
                "insert_after": "seen",
                "description": "Indicates if this email was processed for NF attachments"
            }
        ]
    }

    # Banking custom fields (module: Bancos)
    banking_fields = {
        "Sales Invoice": [
            {
                "fieldname": "banco_inter_section",
                "fieldtype": "Section Break",
                "label": "Banco Inter",
                "insert_after": "payment_schedule",
                "collapsible": 1,
            },
            {
                "fieldname": "inter_boleto",
                "fieldtype": "Link",
                "label": "Boleto",
                "options": "Inter Boleto",
                "insert_after": "banco_inter_section",
                "read_only": 1,
            },
            {
                "fieldname": "inter_pix_charge",
                "fieldtype": "Link",
                "label": "PIX Charge",
                "options": "Inter PIX Charge",
                "insert_after": "inter_boleto",
                "read_only": 1,
            },
        ],
        "Payment Entry": [
            {
                "fieldname": "banco_inter_section",
                "fieldtype": "Section Break",
                "label": "Banco Inter",
                "insert_after": "reference_date",
                "collapsible": 1,
            },
            {
                "fieldname": "inter_payment_order",
                "fieldtype": "Link",
                "label": "Inter Payment Order",
                "options": "Inter Payment Order",
                "insert_after": "banco_inter_section",
                "read_only": 1,
            },
        ],
        "Bank Account": [
            {
                "fieldname": "banco_inter_section",
                "fieldtype": "Section Break",
                "label": "Banco Inter",
                "insert_after": "bank",
                "collapsible": 1,
            },
            {
                "fieldname": "inter_company_account",
                "fieldtype": "Link",
                "label": "Inter Company Account",
                "options": "Inter Company Account",
                "insert_after": "banco_inter_section",
                "read_only": 1,
            },
        ],
    }

    # Intelligence8 custom fields (module: Intelligence)
    intelligence_fields = {
        "Communication": [
            {
                "fieldname": "i8_section",
                "fieldtype": "Section Break",
                "label": "Intelligence8",
                "insert_after": "nf_processed",
                "collapsible": 1,
            },
            {
                "fieldname": "i8_processed",
                "fieldtype": "Check",
                "label": "I8 Processed",
                "insert_after": "i8_section",
                "read_only": 1,
            },
            {
                "fieldname": "i8_classification",
                "fieldtype": "Select",
                "label": "I8 Classification",
                "options": "\nFISCAL\nCOMMERCIAL\nFINANCIAL\nOPERATIONAL\nSPAM\nUNCERTAIN",
                "insert_after": "i8_processed",
                "read_only": 1,
            },
            {
                "fieldname": "i8_decision_log",
                "fieldtype": "Link",
                "label": "I8 Decision Log",
                "options": "I8 Decision Log",
                "insert_after": "i8_classification",
                "read_only": 1,
            },
        ],
        "Purchase Order": [
            {
                "fieldname": "i8_section",
                "fieldtype": "Section Break",
                "label": "Intelligence8",
                "insert_after": "nota_fiscal",
                "collapsible": 1,
            },
            {
                "fieldname": "i8_recurring_expense",
                "fieldtype": "Link",
                "label": "Recurring Expense",
                "options": "I8 Recurring Expense",
                "insert_after": "i8_section",
                "read_only": 1,
            },
        ],
    }

    _create_fields(fiscal_fields, module="Fiscal")
    _create_fields(banking_fields, module="Bancos")
    _create_fields(intelligence_fields, module="Intelligence8")


def _create_fields(fields_dict, module):
    """Create custom fields for a given module."""
    for doctype, fields in fields_dict.items():
        for field in fields:
            field_name = f"{doctype}-{field['fieldname']}"

            # Check if field already exists
            if frappe.db.exists("Custom Field", field_name):
                # Update module reference if needed
                current_module = frappe.db.get_value("Custom Field", field_name, "module")
                if current_module != module:
                    frappe.db.set_value("Custom Field", field_name, "module", module)
                continue

            # Create custom field
            custom_field = frappe.new_doc("Custom Field")
            custom_field.dt = doctype
            custom_field.module = module

            for key, value in field.items():
                if hasattr(custom_field, key):
                    setattr(custom_field, key, value)

            try:
                custom_field.insert(ignore_permissions=True)
                frappe.logger().info(f"Created custom field: {field_name}")
            except Exception as e:
                frappe.logger().error(f"Error creating custom field {field_name}: {str(e)}")

    frappe.db.commit()


def create_roles():
    """Create custom roles for Brazil module."""
    intelligence_roles = [
        {"role_name": "Intelligence8 Admin", "desk_access": 1},
        {"role_name": "Intelligence8 Viewer", "desk_access": 1},
    ]

    roles = [
        {
            "role_name": "Brazil NF Manager",
            "desk_access": 1,
            "description": "Can manage all Brazil NF settings and documents"
        },
        {
            "role_name": "Brazil NF User",
            "desk_access": 1,
            "description": "Can view and process Brazil NF documents"
        },
        {
            "role_name": "Banco Inter Manager",
            "desk_access": 1,
            "description": "Full access to Banco Inter settings, billing, and payments",
        },
        {
            "role_name": "Banco Inter User",
            "desk_access": 1,
            "description": "Can view transactions and create billing, but cannot execute payments",
        },
    ]

    roles.extend(intelligence_roles)

    for role_data in roles:
        role_name = role_data.pop("role_name")

        if frappe.db.exists("Role", role_name):
            continue

        role = frappe.new_doc("Role")
        role.name = role_name

        for key, value in role_data.items():
            if hasattr(role, key):
                setattr(role, key, value)

        try:
            role.insert(ignore_permissions=True)
            frappe.logger().info(f"Created role: {role_name}")
        except Exception as e:
            frappe.logger().error(f"Error creating role {role_name}: {str(e)}")

    frappe.db.commit()


def setup_workspace():
    """Ensure Intelligence8 workspace and sidebar exist.

    Frappe 16 requires three aligned components:
    - Module Def name = "Intelligence8" (from modules.txt)
    - Workspace name = "Intelligence8" (must match Module Def)
    - Workspace Sidebar name = "Intelligence8" (must match for Desktop Icon link)
    - Desktop Icon link_to = "Intelligence8" (links to Workspace Sidebar)

    Centralizes Agent, Fiscal, and Banking under one workspace.
    """
    # ── Cleanup old entries ──
    for old_name in ("Fiscal", "Bancos", "Intelligence"):
        if frappe.db.exists("Workspace", old_name):
            frappe.db.set_value("Workspace", old_name, "is_hidden", 1)
        if frappe.db.exists("Workspace Sidebar", old_name):
            frappe.delete_doc("Workspace Sidebar", old_name, ignore_permissions=True)

    # Also rename old Module Def if exists
    if frappe.db.exists("Module Def", "Intelligence") and not frappe.db.exists("Module Def", "Intelligence8"):
        frappe.rename_doc("Module Def", "Intelligence", "Intelligence8", force=True)

    # ── Workspace (name MUST = Module Def name) ──
    if frappe.db.exists("Workspace", "Intelligence8"):
        frappe.delete_doc("Workspace", "Intelligence8", ignore_permissions=True)

    ws = frappe.new_doc("Workspace")
    ws.name = "Intelligence8"
    ws.label = "Intelligence8"
    ws.title = "Intelligence8"
    ws.module = "Intelligence8"
    ws.icon = "setting"
    ws.public = 1
    ws.is_hidden = 0
    ws.sequence_id = 3

    for label, doc_view, color in [
        ("I8 Agent Settings", "", "Blue"),
        ("I8 Conversation", "List", "Green"),
        ("I8 Recurring Expense", "List", "Green"),
        ("Nota Fiscal", "List", "Orange"),
        ("Inter Boleto", "List", "Blue"),
        ("I8 Decision Log", "List", "Grey"),
    ]:
        ws.append("shortcuts", {
            "label": label, "link_to": label,
            "type": "DocType", "doc_view": doc_view, "color": color,
        })

    for link_type, label, link_to in [
        ("Card Break", "Agent", ""),
        ("Link", "Settings", "I8 Agent Settings"),
        ("Link", "Conversations", "I8 Conversation"),
        ("Link", "Module Registry", "I8 Module Registry"),
        ("Link", "Decision Log", "I8 Decision Log"),
        ("Link", "Cost Log", "I8 Cost Log"),
        ("Card Break", "P2P (Procure-to-Pay)", ""),
        ("Link", "Recurring Expenses", "I8 Recurring Expense"),
        ("Link", "Supplier Profiles", "I8 Supplier Profile"),
        ("Card Break", "Fiscal", ""),
        ("Link", "Nota Fiscal", "Nota Fiscal"),
        ("Link", "NF Settings", "Nota Fiscal Settings"),
        ("Link", "NF Company Settings", "NF Company Settings"),
        ("Link", "NF Import Log", "NF Import Log"),
        ("Card Break", "Banco Inter", ""),
        ("Link", "Inter Settings", "Banco Inter Settings"),
        ("Link", "Company Accounts", "Inter Company Account"),
        ("Link", "Boletos", "Inter Boleto"),
        ("Link", "PIX Charges", "Inter PIX Charge"),
        ("Link", "Payment Orders", "Inter Payment Order"),
        ("Card Break", "Banking Logs", ""),
        ("Link", "API Log", "Inter API Log"),
        ("Link", "Sync Log", "Inter Sync Log"),
        ("Link", "Webhook Log", "Inter Webhook Log"),
    ]:
        ws.append("links", {
            "type": link_type, "label": label,
            "link_to": link_to, "link_type": "DocType",
        })

    try:
        ws.insert(ignore_permissions=True, ignore_if_duplicate=True)
    except Exception as e:
        frappe.logger().error(f"Error creating workspace: {e}")

    # ── Workspace Sidebar (Frappe 16 sidebar menu) ──
    if frappe.db.exists("Workspace Sidebar", "Intelligence8"):
        frappe.delete_doc("Workspace Sidebar", "Intelligence8", ignore_permissions=True)

    sidebar = frappe.new_doc("Workspace Sidebar")
    sidebar.name = "Intelligence8"
    sidebar.title = "Intelligence8"
    sidebar.header_icon = "setting"
    sidebar.module = "Intelligence8"
    sidebar.app = "brazil_module"
    sidebar.standard = 1

    for item_data in [
        {"label": "Home", "type": "Link", "link_to": "Intelligence8", "link_type": "Workspace"},
        {"label": "Agent Settings", "type": "Link", "link_to": "I8 Agent Settings", "link_type": "DocType"},
        {"label": "Conversations", "type": "Link", "link_to": "I8 Conversation", "link_type": "DocType"},
        {"label": "Decision Log", "type": "Link", "link_to": "I8 Decision Log", "link_type": "DocType"},
        {"label": "Cost Log", "type": "Link", "link_to": "I8 Cost Log", "link_type": "DocType"},
        {"label": "Recurring Expenses", "type": "Link", "link_to": "I8 Recurring Expense", "link_type": "DocType"},
        {"label": "Supplier Profiles", "type": "Link", "link_to": "I8 Supplier Profile", "link_type": "DocType"},
        {"label": "Nota Fiscal", "type": "Link", "link_to": "Nota Fiscal", "link_type": "DocType"},
        {"label": "NF Settings", "type": "Link", "link_to": "Nota Fiscal Settings", "link_type": "DocType"},
        {"label": "NF Company Settings", "type": "Link", "link_to": "NF Company Settings", "link_type": "DocType"},
        {"label": "Import Logs", "type": "Link", "link_to": "NF Import Log", "link_type": "DocType"},
        {"label": "Inter Settings", "type": "Link", "link_to": "Banco Inter Settings", "link_type": "DocType"},
        {"label": "Company Accounts", "type": "Link", "link_to": "Inter Company Account", "link_type": "DocType"},
        {"label": "Boletos", "type": "Link", "link_to": "Inter Boleto", "link_type": "DocType"},
        {"label": "PIX Charges", "type": "Link", "link_to": "Inter PIX Charge", "link_type": "DocType"},
        {"label": "Payment Orders", "type": "Link", "link_to": "Inter Payment Order", "link_type": "DocType"},
    ]:
        sidebar.append("items", item_data)

    try:
        sidebar.insert(ignore_permissions=True)
    except Exception as e:
        frappe.logger().error(f"Error creating Workspace Sidebar: {e}")

    frappe.db.commit()
    frappe.clear_cache()


def setup_desktop_icons():
    """Configure Desktop Icons for the /desk page.

    In Frappe 16, the /desk page is driven by the 'Desktop Icon' DocType.
    Each icon appears as a button on the main desk. Desktop Icon.link_to
    must reference an existing Workspace Sidebar name.
    """
    # Delete old icons
    for old_name in ("Bancos", "Fiscal", "Intelligence"):
        for match in frappe.get_all("Desktop Icon", filters={"label": old_name}, pluck="name"):
            frappe.delete_doc("Desktop Icon", match, ignore_permissions=True)

    # Delete and recreate Intelligence8 icon
    for match in frappe.get_all("Desktop Icon", filters={"label": "Intelligence8"}, pluck="name"):
        frappe.delete_doc("Desktop Icon", match, ignore_permissions=True)

    icon = frappe.new_doc("Desktop Icon")
    icon.label = "Intelligence8"
    icon.icon = "setting"
    icon.app = "brazil_module"
    icon.link_type = "Workspace Sidebar"
    icon.link_to = "Intelligence8"
    icon.icon_type = "Link"
    icon.standard = 1
    icon.hidden = 0
    try:
        icon.insert(ignore_permissions=True)
    except Exception as e:
        frappe.logger().error(f"Error creating Desktop Icon: {e}")

    frappe.db.commit()
