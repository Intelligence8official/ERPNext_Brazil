frappe.ui.form.on("Sales Invoice", {
    refresh(frm) {
        if (frm.doc.docstatus !== 1 || frm.doc.outstanding_amount <= 0) return;

        frm.add_custom_button(__("Generate BoletoPIX"), function () {
            frappe.call({
                method: "brazil_module.api.create_boleto",
                args: { sales_invoice: frm.doc.name },
                freeze: true,
                freeze_message: __("Generating BoletoPIX..."),
                callback(r) {
                    if (r.message && r.message.status === "success") {
                        frappe.show_alert({
                            message: __("BoletoPIX created: {0}", [r.message.boleto]),
                            indicator: "green",
                        });
                        frm.reload_doc();
                    }
                },
            });
        }, __("Banco Inter"));

        frm.add_custom_button(__("Generate PIX"), function () {
            frappe.call({
                method: "brazil_module.api.create_pix_charge",
                args: { sales_invoice: frm.doc.name },
                freeze: true,
                freeze_message: __("Generating PIX charge..."),
                callback(r) {
                    if (r.message && r.message.status === "success") {
                        frappe.show_alert({
                            message: __("PIX charge created: {0}", [r.message.pix_charge]),
                            indicator: "green",
                        });
                        frm.reload_doc();
                    }
                },
            });
        }, __("Banco Inter"));

        // Show linked boleto/PIX status
        if (frm.doc.inter_boleto) {
            frappe.db.get_value("Inter Boleto", frm.doc.inter_boleto, "status").then((r) => {
                if (r.message) {
                    let colors = {
                        Pending: "yellow",
                        Registered: "blue",
                        Paid: "green",
                        Overdue: "orange",
                        Cancelled: "grey",
                        Error: "red",
                    };
                    frm.dashboard.add_indicator(
                        __("Boleto: {0}", [r.message.status]),
                        colors[r.message.status] || "grey"
                    );
                }
            });
        }
    },
});
