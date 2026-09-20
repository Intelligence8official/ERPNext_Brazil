frappe.ui.form.on("Purchase Invoice", {
    refresh(frm) {
        if (frm.doc.docstatus !== 1 || frm.doc.outstanding_amount <= 0) return;

        frm.add_custom_button(__("Pay via Inter"), function () {
            let d = new frappe.ui.Dialog({
                title: __("Pay via Banco Inter"),
                fields: [
                    {
                        fieldname: "payment_type",
                        fieldtype: "Select",
                        label: __("Payment Type"),
                        options: "PIX\nBoleto Payment",
                        reqd: 1,
                        default: "PIX",
                    },
                    {
                        fieldname: "pix_key",
                        fieldtype: "Data",
                        label: __("PIX Key"),
                        depends_on: "eval:doc.payment_type=='PIX'",
                        mandatory_depends_on: "eval:doc.payment_type=='PIX'",
                    },
                    {
                        fieldname: "barcode",
                        fieldtype: "Data",
                        label: __("Boleto Barcode"),
                        depends_on: "eval:doc.payment_type=='Boleto Payment'",
                        mandatory_depends_on: "eval:doc.payment_type=='Boleto Payment'",
                    },
                ],
                primary_action_label: __("Create Payment Order"),
                primary_action(values) {
                    frappe.call({
                        method: "brazil_module.api.create_payment_order",
                        args: {
                            payment_type: values.payment_type,
                            amount: frm.doc.outstanding_amount,
                            company: frm.doc.company,
                            purchase_invoice: frm.doc.name,
                            party_type: "Supplier",
                            party: frm.doc.supplier,
                            pix_key: values.pix_key || "",
                            barcode: values.barcode || "",
                        },
                        freeze: true,
                        callback(r) {
                            const result = r.message || {};
                            if (result.status === "success") {
                                d.hide();
                                frappe.set_route("Form", "Inter Payment Order", result.payment_order);
                                return;
                            }
                            // The payment guards answer with a reason instead of raising.
                            frappe.msgprint({
                                title: __("Payment order not created"),
                                indicator: "red",
                                message: result.message || __("The payment order could not be created."),
                            });
                        },
                    });
                },
            });
            d.show();
        }, __("Banco Inter"));
    },
});
