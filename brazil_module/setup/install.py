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


def after_migrate():
    """Run after bench migrate."""
    create_custom_fields()
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
    _create_fields(intelligence_fields, module="Intelligence")


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
    """Ensure Intelligence8 workspace exists and is visible.

    Centralizes all Brazil module screens (Agent, Fiscal, Banking) in one workspace.
    Hides the old Fiscal and Bancos workspaces to avoid duplication.
    """
    # Hide old workspaces — everything is under Intelligence8 now
    for old_ws in ("Fiscal", "Bancos"):
        if frappe.db.exists("Workspace", old_ws):
            frappe.db.set_value("Workspace", old_ws, "is_hidden", 1)

    # Delete and recreate to keep in sync with code
    if frappe.db.exists("Workspace", "Intelligence8"):
        frappe.delete_doc("Workspace", "Intelligence8", ignore_permissions=True)

    ws = frappe.new_doc("Workspace")
    ws.name = "Intelligence8"
    ws.label = "Intelligence8"
    ws.title = "Intelligence8"
    ws.module = "Intelligence"
    ws.icon = "setting"
    ws.public = 1
    ws.is_hidden = 0
    ws.sequence_id = 3

    shortcuts = [
        ("I8 Agent Settings", "", "Blue"),
        ("I8 Conversation", "List", "Green"),
        ("I8 Recurring Expense", "List", "Green"),
        ("Nota Fiscal", "List", "Orange"),
        ("Inter Boleto", "List", "Blue"),
        ("I8 Decision Log", "List", "Grey"),
    ]
    for label, doc_view, color in shortcuts:
        ws.append("shortcuts", {
            "label": label,
            "link_to": label,
            "type": "DocType",
            "doc_view": doc_view,
            "color": color,
        })

    links = [
        # Agent
        ("Card Break", "Agent", ""),
        ("Link", "Settings", "I8 Agent Settings"),
        ("Link", "Conversations", "I8 Conversation"),
        ("Link", "Module Registry", "I8 Module Registry"),
        ("Link", "Decision Log", "I8 Decision Log"),
        ("Link", "Cost Log", "I8 Cost Log"),
        # P2P
        ("Card Break", "P2P (Procure-to-Pay)", ""),
        ("Link", "Recurring Expenses", "I8 Recurring Expense"),
        ("Link", "Supplier Profiles", "I8 Supplier Profile"),
        # Fiscal
        ("Card Break", "Fiscal", ""),
        ("Link", "Nota Fiscal", "Nota Fiscal"),
        ("Link", "NF Settings", "Nota Fiscal Settings"),
        ("Link", "NF Company Settings", "NF Company Settings"),
        ("Link", "NF Import Log", "NF Import Log"),
        # Banking
        ("Card Break", "Banco Inter", ""),
        ("Link", "Inter Settings", "Banco Inter Settings"),
        ("Link", "Company Accounts", "Inter Company Account"),
        ("Link", "Boletos", "Inter Boleto"),
        ("Link", "PIX Charges", "Inter PIX Charge"),
        ("Link", "Payment Orders", "Inter Payment Order"),
        # Banking Logs
        ("Card Break", "Banking Logs", ""),
        ("Link", "API Log", "Inter API Log"),
        ("Link", "Sync Log", "Inter Sync Log"),
        ("Link", "Webhook Log", "Inter Webhook Log"),
    ]
    for link_type, label, link_to in links:
        ws.append("links", {
            "type": link_type,
            "label": label,
            "link_to": link_to,
            "link_type": "DocType",
        })

    try:
        ws.insert(ignore_permissions=True, ignore_if_duplicate=True)
        frappe.logger().info("Created Intelligence8 workspace")
    except Exception as e:
        frappe.logger().error(f"Error creating workspace: {e}")

    frappe.db.commit()
    frappe.clear_cache()
