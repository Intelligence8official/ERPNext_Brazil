frappe.ui.form.on("Inter Boleto", {
    refresh(frm) {
        // Status-based actions
        if (frm.doc.status === "Registered" || frm.doc.status === "Pending") {
            frm.add_custom_button(__("Check Status"), function () {
                frm.call("check_status").then(() => frm.reload_doc());
            }, __("Actions"));

            frm.add_custom_button(__("Cancel Boleto"), function () {
                frappe.confirm(
                    __("Are you sure you want to cancel this boleto?"),
                    function () {
                        frm.call("cancel_boleto").then(() => frm.reload_doc());
                    }
                );
            }, __("Actions"));
        }

        if (frm.doc.nosso_numero && !frm.doc.boleto_pdf) {
            frm.add_custom_button(__("Download PDF"), function () {
                frm.call("download_pdf").then(() => frm.reload_doc());
            }, __("Actions"));
        }

        // Status indicator
        let indicator = {
            "Pending": "yellow",
            "Registered": "blue",
            "Paid": "green",
            "Overdue": "orange",
            "Cancelled": "grey",
            "Error": "red",
        };
        if (indicator[frm.doc.status]) {
            frm.page.set_indicator(frm.doc.status, indicator[frm.doc.status]);
        }
    },
});
