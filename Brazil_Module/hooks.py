app_name = "Brazil_Module"
app_title = "Brazil"
app_publisher = "Intelligence8"
app_description = "Brazil ERP - Fiscal documents (NF-e, CT-e, NFS-e) and Banking integration for ERPNext"
app_email = "contact@intelligence8.com"
app_license = "MIT"
required_apps = ["frappe", "erpnext"]

# DocType JS overrides
doctype_js = {
    "Nota Fiscal": "fiscal/doctype/nota_fiscal/nota_fiscal.js",
    "Sales Invoice": "public/js/sales_invoice.js",
    "Purchase Invoice": "public/js/purchase_invoice.js",
    "Bank Account": "public/js/bank_account.js",
}

doctype_list_js = {
    "Nota Fiscal": "fiscal/doctype/nota_fiscal/nota_fiscal_list.js"
}

# Installation
after_install = "Brazil_Module.setup.install.after_install"
after_migrate = "Brazil_Module.setup.install.after_migrate"

# Document Events
doc_events = {
    "Nota Fiscal": {
        "after_insert": "Brazil_Module.services.fiscal.processor.process_new_nf",
        "validate": "Brazil_Module.services.fiscal.processor.validate_nf"
    },
    "Communication": {
        "after_insert": "Brazil_Module.services.fiscal.email_monitor.check_nf_attachment"
    },
    "Sales Invoice": {
        "on_submit": "Brazil_Module.services.banking.boleto_service.on_invoice_submit",
    },
    "Payment Entry": {
        "on_submit": "Brazil_Module.services.banking.reconciliation.on_payment_entry_submit",
    },
}

# Scheduled Tasks
scheduler_events = {
    "cron": {
        # Fiscal: Every 10 minutes - fetch documents from SEFAZ
        "*/10 * * * *": [
            "Brazil_Module.services.fiscal.dfe_client.scheduled_fetch"
        ],
        # Fiscal: Every 5 minutes - check emails for NF attachments
        "*/5 * * * *": [
            "Brazil_Module.services.fiscal.email_monitor.check_emails"
        ],
        # Banking: Every 6 hours - fetch bank statements
        "0 */6 * * *": [
            "Brazil_Module.services.banking.statement_sync.scheduled_statement_sync"
        ],
        # Banking: Every 30 minutes - check boleto payment status
        "*/30 * * * *": [
            "Brazil_Module.services.banking.boleto_service.scheduled_boleto_status_check"
        ],
        # Banking: Every 15 minutes - check PIX charge status
        "*/15 * * * *": [
            "Brazil_Module.services.banking.pix_service.scheduled_pix_status_check"
        ],
        # Banking: Every hour - check outbound payment status
        "0 * * * *": [
            "Brazil_Module.services.banking.payment_service.scheduled_payment_status_check"
        ],
    },
    "daily": [
        "Brazil_Module.services.fiscal.processor.cleanup_old_logs",
        "Brazil_Module.services.banking.statement_sync.daily_balance_update",
        "Brazil_Module.services.banking.boleto_service.cancel_expired_boletos",
    ],
    "weekly": [
        "Brazil_Module.services.fiscal.processor.cleanup_processed_xmls",
        "Brazil_Module.services.banking.cleanup.cleanup_old_api_logs",
        "Brazil_Module.services.banking.cleanup.cleanup_old_webhook_logs",
    ],
}

# Fixtures
fixtures = [
    {
        "dt": "Custom Field",
        "filters": [["module", "in", ["Fiscal", "Bancos"]]]
    },
    {
        "dt": "Property Setter",
        "filters": [["module", "in", ["Fiscal", "Bancos"]]]
    },
    {
        "dt": "Role",
        "filters": [["name", "in", [
            "Brazil NF Manager", "Brazil NF User",
            "Banco Inter Manager", "Banco Inter User"
        ]]]
    }
]
