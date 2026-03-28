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
    setup_number_cards()


def after_migrate():
    """Run after bench migrate."""
    create_custom_fields()
    setup_workspace()
    setup_desktop_icons()
    setup_workspace()
    setup_number_cards()


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
        "Purchase Invoice": [
            {
                "fieldname": "i8_section",
                "fieldtype": "Section Break",
                "label": "Intelligence8",
                "insert_after": "chave_de_acesso",
                "collapsible": 1,
            },
            {
                "fieldname": "boleto_barcode",
                "fieldtype": "Data",
                "label": "Linha Digitavel (Boleto)",
                "insert_after": "i8_section",
                "description": "Barcode for boleto payment scheduling",
            },
        ],
        "Supplier": [
            {
                "fieldname": "i8_section",
                "fieldtype": "Section Break",
                "label": "Intelligence8",
                "insert_after": "payment_terms",
                "collapsible": 1,
            },
            {
                "fieldname": "pix_key",
                "fieldtype": "Data",
                "label": "Chave PIX",
                "insert_after": "i8_section",
            },
            {
                "fieldname": "pix_key_type",
                "fieldtype": "Select",
                "label": "Tipo Chave PIX",
                "options": "\nCPF\nCNPJ\nEmail\nTelefone\nAleatoria",
                "insert_after": "pix_key",
            },
            {
                "fieldname": "i8_expected_nf_days",
                "fieldtype": "Int",
                "label": "Prazo NF (dias)",
                "default": "5",
                "insert_after": "pix_key_type",
                "description": "Days to expect NF after PO",
            },
            {
                "fieldname": "i8_nf_due_day",
                "fieldtype": "Int",
                "label": "Dia Emissao NF",
                "insert_after": "i8_expected_nf_days",
                "description": "Day of month supplier issues NF",
            },
            {
                "fieldname": "i8_enable_followup",
                "fieldtype": "Check",
                "label": "Habilitar Follow-up",
                "insert_after": "i8_nf_due_day",
                "description": "Send automatic follow-up emails when NF is overdue",
            },
            {
                "fieldname": "i8_follow_up_after_days",
                "fieldtype": "Int",
                "label": "Follow-up Apos (dias)",
                "default": "7",
                "insert_after": "i8_enable_followup",
                "depends_on": "i8_enable_followup",
            },
            {
                "fieldname": "i8_max_follow_ups",
                "fieldtype": "Int",
                "label": "Max Follow-ups",
                "default": "3",
                "insert_after": "i8_follow_up_after_days",
            },
            {
                "fieldname": "i8_auto_pay",
                "fieldtype": "Check",
                "label": "Auto Pay",
                "insert_after": "i8_max_follow_ups",
                "description": "Auto-pay when NF arrives",
            },
            {
                "fieldname": "i8_agent_notes",
                "fieldtype": "Long Text",
                "label": "Notas do Agente",
                "insert_after": "i8_auto_pay",
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
    # ── Cleanup old entries (Sidebar BEFORE Workspace to avoid link errors) ──
    for old_name in ("Fiscal", "Bancos", "Intelligence", "Intelligence8"):
        if frappe.db.exists("Workspace Sidebar", old_name):
            frappe.delete_doc("Workspace Sidebar", old_name, ignore_permissions=True, force=True)
    for old_name in ("Fiscal", "Bancos", "Intelligence"):
        if frappe.db.exists("Workspace", old_name):
            frappe.db.set_value("Workspace", old_name, "is_hidden", 1)

    # Also rename old Module Def if exists
    if frappe.db.exists("Module Def", "Intelligence") and not frappe.db.exists("Module Def", "Intelligence8"):
        frappe.rename_doc("Module Def", "Intelligence", "Intelligence8", force=True)

    # ── Workspace (delete sidebar first, then workspace) ──
    if frappe.db.exists("Workspace", "Intelligence8"):
        # Use SQL to avoid link validation errors
        frappe.db.sql("DELETE FROM `tabWorkspace Shortcut` WHERE parent = 'Intelligence8'")
        frappe.db.sql("DELETE FROM `tabWorkspace Link` WHERE parent = 'Intelligence8'")
        frappe.db.sql("DELETE FROM `tabWorkspace` WHERE name = 'Intelligence8'")
        frappe.db.commit()

    ws = frappe.new_doc("Workspace")
    ws.name = "Intelligence8"
    ws.label = "Intelligence8"
    ws.title = "Intelligence8"
    ws.module = "Intelligence8"
    ws.icon = "brain-circuit"
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
        ("Link", "Suppliers", "Supplier"),
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
    sidebar.header_icon = "brain-circuit"
    sidebar.module = "Intelligence8"
    sidebar.app = "brazil_module"
    sidebar.standard = 1

    # Sidebar items: top-level links + collapsible sections with children
    # type="Section Break" + indent=1 + collapsible=1 creates a collapsible group
    # child=1 + collapsible=1 makes items appear inside the group
    sidebar_items = [
        # ── Top-level links (with icons) ──
        {"label": "Home", "type": "Link", "link_to": "Intelligence8", "link_type": "Workspace", "icon": "home", "collapsible": 1},
        {"label": "Conversations", "type": "Link", "link_to": "I8 Conversation", "link_type": "DocType", "icon": "message-circle", "collapsible": 1},
        # ── P2P (Procure-to-Pay) ──
        {"label": "P2P", "type": "Section Break", "link_type": "DocType", "icon": "shopping-cart", "collapsible": 1, "indent": 1},
        {"label": "Recurring Expenses", "type": "Link", "link_to": "I8 Recurring Expense", "link_type": "DocType", "collapsible": 1, "child": 1},
        {"label": "Suppliers", "type": "Link", "link_to": "Supplier", "link_type": "DocType", "collapsible": 1, "child": 1},
        {"label": "Purchase Order", "type": "Link", "link_to": "Purchase Order", "link_type": "DocType", "collapsible": 1, "child": 1},
        {"label": "Purchase Invoice", "type": "Link", "link_to": "Purchase Invoice", "link_type": "DocType", "collapsible": 1, "child": 1},
        # ── Fiscal ──
        {"label": "Fiscal", "type": "Section Break", "link_type": "DocType", "icon": "file-text", "collapsible": 1, "indent": 1},
        {"label": "Nota Fiscal", "type": "Link", "link_to": "Nota Fiscal", "link_type": "DocType", "collapsible": 1, "child": 1},
        {"label": "NF Company Settings", "type": "Link", "link_to": "NF Company Settings", "link_type": "DocType", "collapsible": 1, "child": 1},
        {"label": "Nota Fiscal Settings", "type": "Link", "link_to": "Nota Fiscal Settings", "link_type": "DocType", "collapsible": 1, "child": 1},
        {"label": "Import Logs", "type": "Link", "link_to": "NF Import Log", "link_type": "DocType", "collapsible": 1, "child": 1},
        # ── Banco Inter ──
        {"label": "Banco Inter", "type": "Section Break", "link_type": "DocType", "icon": "landmark", "collapsible": 1, "indent": 1},
        {"label": "Boletos", "type": "Link", "link_to": "Inter Boleto", "link_type": "DocType", "collapsible": 1, "child": 1},
        {"label": "PIX Charges", "type": "Link", "link_to": "Inter PIX Charge", "link_type": "DocType", "collapsible": 1, "child": 1},
        {"label": "Payment Orders", "type": "Link", "link_to": "Inter Payment Order", "link_type": "DocType", "collapsible": 1, "child": 1},
        {"label": "Company Accounts", "type": "Link", "link_to": "Inter Company Account", "link_type": "DocType", "collapsible": 1, "child": 1},
        {"label": "Inter Settings", "type": "Link", "link_to": "Banco Inter Settings", "link_type": "DocType", "collapsible": 1, "child": 1},
        # ── Agent ──
        {"label": "Agent", "type": "Section Break", "link_type": "DocType", "icon": "bot", "collapsible": 1, "indent": 1},
        {"label": "Agent Settings", "type": "Link", "link_to": "I8 Agent Settings", "link_type": "DocType", "collapsible": 1, "child": 1},
        {"label": "Module Registry", "type": "Link", "link_to": "I8 Module Registry", "link_type": "DocType", "collapsible": 1, "child": 1},
        {"label": "Decision Log", "type": "Link", "link_to": "I8 Decision Log", "link_type": "DocType", "collapsible": 1, "child": 1},
        {"label": "Cost Log", "type": "Link", "link_to": "I8 Cost Log", "link_type": "DocType", "collapsible": 1, "child": 1},
        # ── Banking Logs ──
        {"label": "Banking Logs", "type": "Section Break", "link_type": "DocType", "icon": "scroll-text", "collapsible": 1, "indent": 1},
        {"label": "API Log", "type": "Link", "link_to": "Inter API Log", "link_type": "DocType", "collapsible": 1, "child": 1},
        {"label": "Sync Log", "type": "Link", "link_to": "Inter Sync Log", "link_type": "DocType", "collapsible": 1, "child": 1},
        {"label": "Webhook Log", "type": "Link", "link_to": "Inter Webhook Log", "link_type": "DocType", "collapsible": 1, "child": 1},
    ]

    for item_data in sidebar_items:
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
    icon.icon = "brain-circuit"
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


def setup_number_cards():
    """Create Number Cards for the Intelligence8 workspace dashboard."""
    cards = [
        {
            "name": "I8 Today's Decisions",
            "label": "Decisions Today",
            "document_type": "I8 Decision Log",
            "function": "Count",
            "filters_json": '[]',
            "dynamic_filters_json": '{"timestamp":[">=","Today"]}',
            "show_percentage_stats": 1,
            "stats_time_interval": "Daily",
            "module": "Intelligence8",
            "color": "#4299E1",
        },
        {
            "name": "I8 Pending Approvals",
            "label": "Pending Approvals",
            "document_type": "I8 Decision Log",
            "function": "Count",
            "filters_json": '[["result","=","Pending"],["docstatus","=",0]]',
            "color": "#ED8936",
        },
        {
            "name": "I8 Today's LLM Cost",
            "label": "LLM Cost Today (USD)",
            "document_type": "I8 Cost Log",
            "function": "Sum",
            "aggregate_function_based_on": "cost_usd",
            "dynamic_filters_json": '{"timestamp":[">=","Today"]}',
            "color": "#48BB78",
        },
        {
            "name": "I8 Unreconciled Transactions",
            "label": "Unreconciled Bank Txns",
            "document_type": "Bank Transaction",
            "function": "Count",
            "filters_json": '[["docstatus","=",1],["unallocated_amount",">",0]]',
            "color": "#F56565",
        },
    ]

    for card_data in cards:
        name = card_data.pop("name")
        if frappe.db.exists("Number Card", name):
            continue

        nc = frappe.new_doc("Number Card")
        nc.name = name
        for key, value in card_data.items():
            if hasattr(nc, key):
                setattr(nc, key, value)
        nc.owner = "Administrator"
        nc.is_standard = 0
        try:
            nc.insert(ignore_permissions=True)
        except Exception as e:
            frappe.logger().error(f"Error creating Number Card {name}: {e}")

    frappe.db.commit()
