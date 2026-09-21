frappe.ui.form.on("I8 Agent Settings", {
    refresh: function(frm) {
        if (!frm.doc.enabled) return;

        // ── Execute Now ──
        _add_action(frm, "Run Daily Briefing", "brazil_module.api.i8_run_briefing",
            "Briefing solicitado.");

        _add_action(frm, "Run Expense Scheduler", "brazil_module.api.i8_run_expense_scheduler",
            "Expense scheduler executado. Verifique o Telegram.");

        _add_action(frm, "Run Bank Reconciliation", "brazil_module.api.i8_run_reconciliation",
            "Conciliacao bancaria iniciada. Verifique o Telegram.");

        _add_action(frm, "Run Follow-up Check", "brazil_module.api.i8_run_followup_check",
            "Follow-up check executado. Verifique o Telegram.");

        _add_action(frm, "Schedule Weekly Payments", "brazil_module.api.i8_run_payment_scheduling",
            "Agendamento de pagamentos iniciado. Verifique o Telegram.");

        frm.add_custom_button(__("Test Telegram Connection"), function() {
            frappe.call({
                method: "brazil_module.api.i8_check_telegram_webhook",
                freeze: true,
                freeze_message: __("Perguntando ao Telegram..."),
                callback: function(r) {
                    const s = r.message || {};
                    const lines = [
                        __("Bot: {0}", [s.bot ? "@" + s.bot : __("desconhecido")]),
                        __("Webhook: {0}", [s.url || __("nenhum registrado")]),
                        __("Fila: {0}", [s.pending]),
                    ];
                    if (!s.healthy) {
                        lines.push("");
                        lines.push(__("Problema: {0}", [s.problem]));
                        if (s.fixable) {
                            lines.push(__("O botao Register Telegram Webhook resolve isto."));
                        }
                    }
                    frappe.msgprint({
                        title: s.healthy ? __("Telegram entregando") : __("Telegram com problema"),
                        indicator: s.healthy ? "green" : "red",
                        message: lines.join("<br>"),
                    });
                    frm.reload_doc();
                },
            });
        }, __("Execute Now"));

        // Repointing delivery and minting a new secret: worth a confirmation.
        frm.add_custom_button(__("Register Telegram Webhook"), function() {
            frappe.confirm(
                __("Isto aponta o Telegram para este site e troca o segredo do webhook. Continuar?"),
                function() {
                    frappe.call({
                        method: "brazil_module.api.i8_register_telegram_webhook",
                        freeze: true,
                        freeze_message: __("Registrando no Telegram..."),
                        callback: function(r) {
                            const res = r.message || {};
                            frappe.msgprint({
                                title: res.ok ? __("Webhook registrado") : __("Nao foi possivel registrar"),
                                indicator: res.ok ? "green" : "red",
                                message: res.ok ? (res.url || "") : (res.message || __("Sem detalhes")),
                            });
                            frm.reload_doc();
                        },
                    });
                }
            );
        }, __("Execute Now"));

        // A wrong credential is invisible until a scheduled job fails at
        // dawn; this asks the provider for one word, now.
        frm.add_custom_button(__("Test LLM Connection"), function() {
            frappe.call({
                method: "brazil_module.api.i8_test_llm_connection",
                freeze: true,
                freeze_message: __("Perguntando ao provedor..."),
                callback: function(r) {
                    const res = r.message || {};
                    if (res.status === "success") {
                        frappe.msgprint({
                            title: __("Provedor respondeu"),
                            indicator: "green",
                            message: __("{0} respondeu com {1}: {2}", [res.provider, res.model, res.answer]),
                        });
                    } else {
                        frappe.msgprint({
                            title: __("Provedor nao respondeu"),
                            indicator: "red",
                            message: res.message || __("Sem detalhes"),
                        });
                    }
                },
            });
        }, __("Execute Now"));

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
                // Say what came back, not what we hoped for: these endpoints answer
                // {status, message}, and one of them used to report "sent" for a briefing that
                // had only been queued - and then dropped, outside its configured window.
                const out = r.message || {};
                const ok = out.status === "queued" || out.status === "sent";
                frappe.show_alert({
                    message: out.message ? frappe.utils.escape_html(out.message) : __(success_msg),
                    indicator: ok ? "green" : "orange",
                }, 7);
            },
            error: function(r) {
                frappe.show_alert({message: __("Erro ao executar " + label), indicator: "red"}, 5);
            }
        });
    }, __("Execute Now"));
}
