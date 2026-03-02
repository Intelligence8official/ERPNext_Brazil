app_name = "brazil"
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
after_install = "brazil.setup.install.after_install"
after_migrate = "brazil.setup.install.after_migrate"

# Document Events
doc_events = {
    "Nota Fiscal": {
        "after_insert": "brazil.services.fiscal.processor.process_new_nf",
        "validate": "brazil.services.fiscal.processor.validate_nf"
    },
    "Communication": {
        "after_insert": "brazil.services.fiscal.email_monitor.check_nf_attachment"
    },
    "Sales Invoice": {
        "on_submit": "brazil.services.banking.boleto_service.on_invoice_submit",
    },
    "Payment Entry": {
        "on_submit": "brazil.services.banking.reconciliation.on_payment_entry_submit",
    },
}

# Scheduled Tasks
scheduler_events = {
    "cron": {
        # Fiscal: Every 10 minutes - fetch documents from SEFAZ
        "*/10 * * * *": [
            "brazil.services.fiscal.dfe_client.scheduled_fetch"
        ],
        # Fiscal: Every 5 minutes - check emails for NF attachments
        "*/5 * * * *": [
            "brazil.services.fiscal.email_monitor.check_emails"
        ],
        # Banking: Every 6 hours - fetch bank statements
        "0 */6 * * *": [
            "brazil.services.banking.statement_sync.scheduled_statement_sync"
        ],
        # Banking: Every 30 minutes - check boleto payment status
        "*/30 * * * *": [
            "brazil.services.banking.boleto_service.scheduled_boleto_status_check"
        ],
        # Banking: Every 15 minutes - check PIX charge status
        "*/15 * * * *": [
            "brazil.services.banking.pix_service.scheduled_pix_status_check"
        ],
        # Banking: Every hour - check outbound payment status
        "0 * * * *": [
            "brazil.services.banking.payment_service.scheduled_payment_status_check"
        ],
    },
    "daily": [
        "brazil.services.fiscal.processor.cleanup_old_logs",
        "brazil.services.banking.statement_sync.daily_balance_update",
        "brazil.services.banking.boleto_service.cancel_expired_boletos",
    ],
    "weekly": [
        "brazil.services.fiscal.processor.cleanup_processed_xmls",
        "brazil.services.banking.cleanup.cleanup_old_api_logs",
        "brazil.services.banking.cleanup.cleanup_old_webhook_logs",
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
