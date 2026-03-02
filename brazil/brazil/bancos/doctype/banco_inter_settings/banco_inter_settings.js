frappe.ui.form.on("Banco Inter Settings", {
    refresh(frm) {
        if (!frm.doc.enabled) {
            frm.set_intro(__("Banco Inter integration is disabled. Enable it to start syncing."), "yellow");
        }

        if (frm.doc.environment === "Sandbox") {
            frm.set_intro(__("Running in Sandbox mode. Switch to Production when ready."), "blue");
        }
    },
});
