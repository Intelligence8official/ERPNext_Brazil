frappe.ui.form.on("Inter Payment Order", {
    refresh(frm) {
        if (frm.doc.docstatus === 1) {
            if (frm.doc.status === "Pending Approval") {
                frm.add_custom_button(__("Approve"), function () {
                    frm.call("approve_payment").then(() => frm.reload_doc());
                }, __("Actions"));
            }

            if (frm.doc.status === "Approved") {
                frm.add_custom_button(__("Execute Payment"), function () {
                    frappe.confirm(
                        __("Are you sure you want to execute this payment of R$ {0}?", [
                            format_currency(frm.doc.amount),
                        ]),
                        function () {
                            frm.call("execute_payment").then(() => frm.reload_doc());
                        }
                    );
                }, __("Actions"));
            }
        }

        let indicator = {
            "Draft": "grey",
            "Pending Approval": "yellow",
            "Approved": "blue",
            "Processing": "orange",
            "Completed": "green",
            "Failed": "red",
            "Cancelled": "grey",
        };
        if (indicator[frm.doc.status]) {
            frm.page.set_indicator(frm.doc.status, indicator[frm.doc.status]);
        }
    },

    payment_type(frm) {
        // Clear fields when payment type changes
        if (frm.doc.payment_type !== "PIX") {
            frm.set_value("pix_key", "");
            frm.set_value("pix_key_type", "");
        }
        if (frm.doc.payment_type !== "TED") {
            frm.set_value("recipient_bank_code", "");
            frm.set_value("recipient_agency", "");
            frm.set_value("recipient_account", "");
            frm.set_value("recipient_account_type", "");
        }
        if (frm.doc.payment_type !== "Boleto Payment") {
            frm.set_value("barcode", "");
            frm.set_value("boleto_due_date", "");
        }
    },

    purchase_invoice(frm) {
        if (frm.doc.purchase_invoice) {
            frappe.db.get_value("Purchase Invoice", frm.doc.purchase_invoice, [
                "supplier",
                "grand_total",
                "company",
            ]).then((r) => {
                if (r.message) {
                    frm.set_value("party_type", "Supplier");
                    frm.set_value("party", r.message.supplier);
                    frm.set_value("amount", r.message.grand_total);
                    frm.set_value("company", r.message.company);
                }
            });
        }
    },
});
