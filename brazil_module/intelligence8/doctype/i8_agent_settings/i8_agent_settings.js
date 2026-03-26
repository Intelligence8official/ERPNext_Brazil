frappe.ui.form.on("I8 Agent Settings", {
    refresh: function(frm) {
        if (!frm.doc.enabled) return;

        // ── Execute Now ──
        _add_action(frm, "Run Daily Briefing", "brazil_module.api.i8_run_briefing",
            "Daily Briefing enviado ao Telegram.");

        _add_action(frm, "Run Expense Scheduler", "brazil_module.api.i8_run_expense_scheduler",
            "Expense scheduler executado. Verifique o Telegram.");

        _add_action(frm, "Run Bank Reconciliation", "brazil_module.api.i8_run_reconciliation",
            "Conciliacao bancaria iniciada. Verifique o Telegram.");

        _add_action(frm, "Run Follow-up Check", "brazil_module.api.i8_run_followup_check",
            "Follow-up check executado. Verifique o Telegram.");

        _add_action(frm, "Schedule Weekly Payments", "brazil_module.api.i8_run_payment_scheduling",
            "Agendamento de pagamentos iniciado. Verifique o Telegram.");

        // ── View ──
        frm.add_custom_button(__("Execution Logs (Cost)"), function() {
            frappe.set_route("List", "I8 Cost Log");
        }, __("View"));

        frm.add_custom_button(__("Decision Logs"), function() {
            frappe.set_route("List", "I8 Decision Log");
        }, __("View"));

        frm.add_custom_button(__("Learning Patterns"), function() {
            frappe.set_route("List", "I8 Learning Pattern");
        }, __("View"));

        frm.add_custom_button(__("Conversations"), function() {
            frappe.set_route("List", "I8 Conversation");
        }, __("View"));
    }
});

function _add_action(frm, label, method, success_msg) {
    frm.add_custom_button(__(label), function() {
        frappe.show_alert({message: __("Executando: " + label + "..."), indicator: "blue"});
        frappe.call({
            method: method,
            freeze: true,
            freeze_message: __("Executando " + label + "..."),
            callback: function(r) {
                if (r.message && r.message.status === "queued") {
                    frappe.show_alert({message: __(success_msg), indicator: "green"}, 5);
                }
            },
            error: function(r) {
                frappe.show_alert({message: __("Erro ao executar " + label), indicator: "red"}, 5);
            }
        });
    }, __("Execute Now"));
}
