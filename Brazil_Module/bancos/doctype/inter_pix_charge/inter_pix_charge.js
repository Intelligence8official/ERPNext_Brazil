frappe.ui.form.on("Inter PIX Charge", {
    refresh(frm) {
        if (frm.doc.status === "Pending" || frm.doc.status === "Active") {
            frm.add_custom_button(__("Check Status"), function () {
                frm.call("check_status").then(() => frm.reload_doc());
            });
        }

        let indicator = {
            "Pending": "yellow",
            "Active": "blue",
            "Paid": "green",
            "Expired": "grey",
            "Cancelled": "grey",
        };
        if (indicator[frm.doc.status]) {
            frm.page.set_indicator(frm.doc.status, indicator[frm.doc.status]);
        }
    },
});
