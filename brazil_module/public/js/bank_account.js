frappe.ui.form.on("Bank Account", {
    refresh(frm) {
        if (!frm.doc.inter_company_account) return;

        frm.add_custom_button(__("Sync Statements"), function () {
            frappe.call({
                method: "brazil_module.api.sync_statements",
                args: { company_account_name: frm.doc.inter_company_account },
                freeze: true,
                freeze_message: __("Syncing bank statements..."),
                callback(r) {
                    if (r.message) {
                        frappe.show_alert({
                            message: __("Statement sync initiated"),
                            indicator: "blue",
                        });
                    }
                },
            });
        }, __("Banco Inter"));

        frm.add_custom_button(__("Fetch Balance"), function () {
            frappe.call({
                method: "brazil_module.api.get_balance",
                args: { company_account_name: frm.doc.inter_company_account },
                callback(r) {
                    if (r.message && r.message.status === "success") {
                        frappe.show_alert({
                            message: __("Balance: R$ {0}", [
                                format_currency(r.message.balance),
                            ]),
                            indicator: "green",
                        });
                    }
                },
            });
        }, __("Banco Inter"));

        frm.add_custom_button(__("Reconcile Transactions"), function () {
            frappe.call({
                method: "brazil_module.api.reconcile_transactions",
                args: { bank_account: frm.doc.name },
                freeze: true,
                freeze_message: __("Running auto-reconciliation..."),
                callback(r) {
                    if (r.message) {
                        frappe.msgprint({
                            title: __("Reconciliation Complete"),
                            message: __("Matched: {0} | Unmatched: {1} | Errors: {2}", [
                                r.message.matched,
                                r.message.unmatched,
                                r.message.errors,
                            ]),
                            indicator: r.message.matched > 0 ? "green" : "orange",
                        });
                    }
                },
            });
        }, __("Banco Inter"));
    },
});
