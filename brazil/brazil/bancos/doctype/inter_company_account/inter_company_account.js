frappe.ui.form.on("Inter Company Account", {
    refresh(frm) {
        if (!frm.is_new()) {
            frm.add_custom_button(__("Test Connection"), function () {
                frm.call("test_connection").then((r) => {
                    if (r.message && r.message.status === "success") {
                        frm.reload_doc();
                    }
                });
            }, __("Actions"));

            frm.add_custom_button(__("Sync Statements"), function () {
                frm.call("sync_now");
            }, __("Actions"));

            frm.add_custom_button(__("Fetch Balance"), function () {
                frm.call("fetch_balance").then((r) => {
                    if (r.message && r.message.status === "success") {
                        frm.reload_doc();
                    }
                });
            }, __("Actions"));

            frm.add_custom_button(__("Register Webhook"), function () {
                frm.call("register_webhook");
            }, __("Actions"));
        }

        // Show certificate status
        if (frm.doc.certificate_valid) {
            frm.dashboard.set_headline(
                __("Certificate valid until {0}", [frm.doc.certificate_expiry]),
                "green"
            );
        } else if (frm.doc.certificate_file) {
            frm.dashboard.set_headline(
                __("Certificate invalid or expired"),
                "red"
            );
        }

        // Show balance
        if (frm.doc.current_balance !== undefined && frm.doc.current_balance !== null) {
            frm.set_intro(
                __("Balance: R$ {0} (as of {1})", [
                    format_currency(frm.doc.current_balance),
                    frm.doc.balance_date || "N/A",
                ]),
                "blue"
            );
        }
    },
});
