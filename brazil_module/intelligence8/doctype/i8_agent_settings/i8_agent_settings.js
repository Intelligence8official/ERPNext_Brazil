frappe.ui.form.on("I8 Agent Settings", {
    refresh: function(frm) {
        // Manual execution buttons in the Actions menu
        frm.add_custom_button(__("Run Daily Briefing"), function() {
            frappe.call({
                method: "brazil_module.api.i8_run_briefing",
                callback: function(r) {
                    frappe.msgprint(__("Daily Briefing sent to Telegram."));
                }
            });
        }, __("Execute Now"));

        frm.add_custom_button(__("Run Expense Scheduler"), function() {
            frappe.call({
                method: "brazil_module.api.i8_run_expense_scheduler",
                callback: function(r) {
                    frappe.msgprint(__("Expense scheduler executed. Check Telegram for results."));
                }
            });
        }, __("Execute Now"));

        frm.add_custom_button(__("Run Bank Reconciliation"), function() {
            frappe.call({
                method: "brazil_module.api.i8_run_reconciliation",
                callback: function(r) {
                    frappe.msgprint(__("Bank reconciliation started. Check Telegram for results."));
                }
            });
        }, __("Execute Now"));

        frm.add_custom_button(__("Run Follow-up Check"), function() {
            frappe.call({
                method: "brazil_module.api.i8_run_followup_check",
                callback: function(r) {
                    frappe.msgprint(__("Follow-up check executed. Check Telegram for results."));
                }
            });
        }, __("Execute Now"));

        frm.add_custom_button(__("Schedule Weekly Payments"), function() {
            frappe.call({
                method: "brazil_module.api.i8_run_payment_scheduling",
                callback: function(r) {
                    frappe.msgprint(__("Payment scheduling started. Check Telegram for results."));
                }
            });
        }, __("Execute Now"));

        frm.add_custom_button(__("View Execution Logs"), function() {
            frappe.set_route("List", "I8 Cost Log", {
                "order_by": "creation desc"
            });
        }, __("View"));

        frm.add_custom_button(__("View Decision Logs"), function() {
            frappe.set_route("List", "I8 Decision Log", {
                "order_by": "creation desc"
            });
        }, __("View"));
    }
});
