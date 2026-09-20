// Keep the colours equal to IPO_STATUS_COLORS in inter_payment_order.js
// (test_inter_payment_order.py compares the two maps).
frappe.listview_settings["Inter Payment Order"] = {
    add_fields: ["status", "bank_status"],

    get_indicator(doc) {
        const colors = {
            "Draft": "grey",
            "Pending Approval": "yellow",
            "Approved": "blue",
            "Processing": "orange",
            "Awaiting Bank": "purple",
            "Needs Verification": "red",
            "Completed": "green",
            "Failed": "red",
            "Cancelled": "grey",
        };
        return [__(doc.status), colors[doc.status] || "grey", "status,=," + doc.status];
    },
};
