app_name = "brazil_module"
app_title = "Brazil"
app_publisher = "Intelligence8"
app_description = "Brazil ERP - Fiscal documents (NF-e, CT-e, NFS-e) and Banking integration for ERPNext"
app_email = "contact@intelligence8.com"
app_license = "MIT"
required_apps = ["frappe", "erpnext"]

# Global JS included in every desk page
app_include_js = "/assets/brazil_module/js/i8_chat_widget.js"

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
after_install = "brazil_module.setup.install.after_install"
after_migrate = "brazil_module.setup.install.after_migrate"

# Document Events
doc_events = {
    "Nota Fiscal": {
        "after_insert": [
            "brazil_module.services.fiscal.processor.process_new_nf",
            "brazil_module.services.intelligence.agent.on_nota_fiscal",
        ],
        "validate": "brazil_module.services.fiscal.processor.validate_nf",
    },
    "Communication": {
        "after_insert": [
            "brazil_module.services.fiscal.email_monitor.check_nf_attachment",
            "brazil_module.services.intelligence.agent.on_communication",
        ]
    },
    "Sales Invoice": {
        "on_submit": "brazil_module.services.banking.boleto_service.on_invoice_submit",
    },
    "Payment Entry": {
        "on_submit": "brazil_module.services.banking.reconciliation.on_payment_entry_submit",
    },
}

# Scheduled Tasks
scheduler_events = {
    "cron": {
        # Fiscal: Every hour - fetch documents from SEFAZ
        "0 * * * *": [
            "brazil_module.services.fiscal.dfe_client.scheduled_fetch"
        ],
        # Fiscal: Every 5 minutes - check emails for NF attachments
        "*/5 * * * *": [
            "brazil_module.services.fiscal.email_monitor.check_emails"
        ],
        # Banking: Every 6 hours - fetch bank statements
        "0 */6 * * *": [
            "brazil_module.services.banking.statement_sync.scheduled_statement_sync"
        ],
        # Banking: Every 30 minutes - check boleto payment status
        "*/30 * * * *": [
            "brazil_module.services.banking.boleto_service.scheduled_boleto_status_check"
        ],
        # Banking: Every 15 minutes - check PIX charge status
        "*/15 * * * *": [
            "brazil_module.services.banking.pix_service.scheduled_pix_status_check"
        ],
        # Banking: Every hour - check outbound payment status
        "0 * * * *": [
            "brazil_module.services.banking.payment_service.scheduled_payment_status_check"
        ],
        # Intelligence8: Daily expense check at 07:00
        "0 7 * * *": ["brazil_module.services.intelligence.recurring.expense_scheduler.daily_check"],
        # Intelligence8: Follow-up check at 09:00
        "0 9 * * *": ["brazil_module.services.intelligence.recurring.follow_up_manager.check_overdue"],
        # Intelligence8: Daily briefing at 08:00
        "0 8 * * *": [
            "brazil_module.services.intelligence.recurring.daily_briefing.scheduled_briefing"
        ],
        # Intelligence8: Planning loop every hour at :30
        "30 * * * *": [
            "brazil_module.services.intelligence.recurring.planning_loop.hourly_check"
        ],
        # Intelligence8: Weekly payment scheduling (Monday 7:30)
        "30 7 * * 1": [
            "brazil_module.services.intelligence.recurring.planning_loop.schedule_weekly_payments"
        ],
    },
    "daily": [
        "brazil_module.services.fiscal.processor.cleanup_old_logs",
        "brazil_module.services.banking.statement_sync.daily_balance_update",
        "brazil_module.services.banking.boleto_service.cancel_expired_boletos",
    ],
    "weekly": [
        "brazil_module.services.fiscal.processor.cleanup_processed_xmls",
        "brazil_module.services.banking.cleanup.cleanup_old_api_logs",
        "brazil_module.services.banking.cleanup.cleanup_old_webhook_logs",
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
