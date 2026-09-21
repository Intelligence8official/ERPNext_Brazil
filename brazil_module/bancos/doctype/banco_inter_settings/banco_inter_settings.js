// Banco Inter Settings - form script.
//
// set_intro REPLACES, it does not accumulate: the two notices this form used to set overwrote each
// other, so a disabled integration was announced only when the environment happened to be
// Production. They are collected and shown together now.

frappe.ui.form.on("Banco Inter Settings", {
    refresh(frm) {
        bis_show_notices(frm);
        frm.add_custom_button(__("Verificar conexao"), () => bis_check_connection(frm));
    },
});

function bis_show_notices(frm) {
    const notices = [];

    if (frm.doc.banking_health_status && frm.doc.banking_health_state !== "ok") {
        notices.push(
            __("Comunicacao bancaria: {0}", [frappe.utils.escape_html(frm.doc.banking_health_status)])
        );
    }
    if (!frm.doc.enabled) {
        notices.push(__("Banco Inter integration is disabled. Enable it to start syncing."));
    }
    if (frm.doc.environment === "Sandbox") {
        notices.push(__("Running in Sandbox mode. Switch to Production when ready."));
    }

    if (notices.length) {
        // Red as soon as the channel itself is the problem; otherwise the milder of the two.
        const colour = frm.doc.banking_health_status && frm.doc.banking_health_state !== "ok"
            ? "red"
            : (frm.doc.enabled ? "blue" : "yellow");
        frm.set_intro(notices.join("<br>"), colour);
    }
}

function bis_check_connection(frm) {
    frappe.call({
        method: "brazil_module.api.check_banking_health",
        freeze: true,
        freeze_message: __("Perguntando ao banco..."),
        callback(r) {
            const verdict = r.message || {};
            const problems = verdict.problems || [];
            const lines = problems.length
                ? problems.map((item) => {
                    const fix = item.fixable_by ? ` — ${frappe.utils.escape_html(item.fixable_by)}` : "";
                    return `${frappe.utils.escape_html(item.problem)}${fix}`;
                })
                : [__("Nenhum problema encontrado.")];
            frappe.msgprint({
                title: verdict.healthy ? __("Comunicacao bancaria em ordem") : __("Comunicacao bancaria com problema"),
                indicator: verdict.healthy ? "green" : "red",
                message: lines.join("<br>"),
            });
            frm.reload_doc();
        },
    });
}
