// Inter Payment Order - form script.
//
// The form never decides anything about money: every button reloads the document, re-checks the
// status and calls a controller method, which re-reads the database row. See
// docs/superpowers/specs/2026-09-20-inter-payment-safety-design.md (section 4.3).
//
// It also never tells the operator something it does not know. After an incident that duplicated
// payments, the person reading this form is deciding whether money left the account: a hopeful
// message here is worse than no message at all.

const IPO_MANAGER_ROLES = ["Banco Inter Manager", "System Manager"];
const IPO_RELOAD_AFTER_EXECUTE_MS = 4000;
const IPO_NOT_PAID_CHECKS = ["not_in_statement", "not_in_approval_queue", "not_in_scheduled_payments"];
// What the operator says they looked at, recorded with the note so the timeline keeps the attestation.
const IPO_NOT_PAID_CHECK_LABELS = ["statement", "approval queue", "scheduled payments"];

// Keep equal to CANCELLABLE_STATUSES in payment_guards.py: the server refuses anything else.
const IPO_CANCELLABLE_STATUSES = ["Draft", "Pending Approval", "Approved", "Failed"];

// Answers of poll_bank_status in which the bank really spoke about this order. Anything else
// (blocked, error, no_bank_id, skipped, or a status added later) must not claim a fresh answer.
const IPO_BANK_ANSWERED = ["awaiting_bank", "completed", "failed", "needs_verification", "unchanged"];

// Keep equal to the map in inter_payment_order_list.js.
const IPO_STATUS_COLORS = {
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

frappe.ui.form.on("Inter Payment Order", {
    refresh(frm) {
        if (IPO_STATUS_COLORS[frm.doc.status]) {
            frm.page.set_indicator(__(frm.doc.status), IPO_STATUS_COLORS[frm.doc.status]);
        }
        if (frm.doc.docstatus === 1) {
            ipo_hide_cancel_when_the_server_would_refuse(frm);
            ipo_show_status_intro(frm);
            ipo_add_status_buttons(frm);
        }
    },

    payment_type(frm) {
        if (frm.doc.payment_type === "TED") {
            frappe.msgprint(
                __("TED is not available: Banco Inter's Banking API has no TED endpoint. Use PIX instead.")
            );
            frm.set_value("payment_type", "");
            return;
        }
        // Clear fields when payment type changes
        if (frm.doc.payment_type !== "PIX") {
            frm.set_value("pix_key", "");
            frm.set_value("pix_key_type", "");
        }
        frm.set_value("recipient_bank_code", "");
        frm.set_value("recipient_agency", "");
        frm.set_value("recipient_account", "");
        frm.set_value("recipient_account_type", "");
        if (frm.doc.payment_type !== "Boleto Payment") {
            frm.set_value("barcode", "");
            frm.set_value("boleto_due_date", "");
        }
    },

    purchase_invoice(frm) {
        if (!frm.doc.purchase_invoice) {
            return;
        }
        frappe.db.get_value("Purchase Invoice", frm.doc.purchase_invoice, [
            "supplier",
            "outstanding_amount",
            "company",
        ]).then((r) => {
            if (r.message) {
                frm.set_value("party_type", "Supplier");
                frm.set_value("party", r.message.supplier);
                // What is still owed, not the invoice total: partial payments and returns count.
                frm.set_value("amount", r.message.outstanding_amount);
                frm.set_value("company", r.message.company);
            }
        });
    },
});

// Every button goes through here. The form may be stale (the job, the cron or another user moved
// the order) and frm.call sends the whole document: reload first, re-check, only then call.
// Resolves to undefined when nothing was called, otherwise to {message}.
async function call_on_fresh_doc(frm, expected_statuses, method, args, reload_delay_ms) {
    if (frm.__ipo_busy) {
        return undefined;  // a second click while the first call is still in flight
    }
    frm.__ipo_busy = true;
    // The desk keeps ONE Form object per doctype, so frm.doc after an await is whatever document
    // is open by then - not necessarily the one the operator acted on.
    const acted_on = frm.doc.name;
    try {
        await frm.reload_doc();
        if (frm.doc.name !== acted_on) {
            frappe.msgprint(__("You moved to another payment order. Nothing was done."));
            return undefined;
        }
        if (frm.doc.docstatus !== 1 || !expected_statuses.includes(frm.doc.status)) {
            frappe.msgprint(__("This payment order is now '{0}'. Nothing was done.", [__(frm.doc.status)]));
            return undefined;
        }
        const r = await frm.call({
            method: method,
            doc: frm.doc,
            args: args || {},
            freeze: true,
            freeze_message: __("Talking to Banco Inter..."),
        });
        return { message: r ? r.message : null };
    } catch (error) {
        // frappe.call already showed the server's message; keep the trace for support.
        console.error(`Inter Payment Order: ${method} failed`, error);
        return undefined;
    } finally {
        frm.__ipo_busy = false;
        ipo_reload_when_still_here(frm, acted_on, reload_delay_ms);
    }
}

// Never reload over another document or over unsaved edits: by the time this runs the operator may
// have navigated away.
function ipo_reload_when_still_here(frm, acted_on, delay_ms) {
    const reload = () => {
        if (frm.doc.name === acted_on && !frm.is_dirty()) {
            frm.reload_doc();
        }
    };
    if (delay_ms) {
        setTimeout(reload, delay_ms);
    } else {
        reload();
    }
}

function ipo_is_manager() {
    return IPO_MANAGER_ROLES.some((role) => frappe.user.has_role(role));
}

// Every action here is a document method on a submitted document, and Frappe checks the submit
// permission for those - so offering them to a reader only produces a refusal.
function ipo_may_act(frm) {
    return frm.has_perm("submit");
}

function ipo_hide_cancel_when_the_server_would_refuse(frm) {
    if (!IPO_CANCELLABLE_STATUSES.includes(frm.doc.status)) {
        frm.page.clear_secondary_action();
    }
}

function ipo_show_status_intro(frm) {
    if (frm.doc.status === "Needs Verification") {
        frm.set_intro(
            __(
                "The outcome of this payment at the bank is unknown. Do NOT pay it again by other means "
                + "before checking the bank: the account statement, the approval queue and the scheduled "
                + "payments. Then use Actions > Resolve Verification."
            ),
            "red"
        );
    } else if (frm.doc.status === "Awaiting Bank") {
        frm.set_intro(
            __("The bank accepted this request (bank status: {0}). It is settled only when the bank reports it as paid.", [
                // Straight from the bank's JSON, and set_intro renders HTML.
                frappe.utils.escape_html(frm.doc.bank_status || "-"),
            ]),
            "blue"
        );
    } else if (frm.doc.status === "Completed" && !frm.doc.payment_entry) {
        frm.set_intro(
            __("Paid at the bank, but the Payment Entry was not created. See the timeline for the reason, fix it and use Actions > Create Payment Entry."),
            "orange"
        );
    }
}

function ipo_add_status_buttons(frm) {
    if (!ipo_may_act(frm)) {
        return;
    }
    const status = frm.doc.status;
    const group = __("Actions");

    if (status === "Pending Approval" && ipo_is_manager()) {
        frm.add_custom_button(__("Approve"), () => {
            call_on_fresh_doc(frm, ["Pending Approval"], "approve_payment");
        }, group);
    }
    if (status === "Approved") {
        frm.add_custom_button(__("Execute Payment"), () => ipo_confirm_execution(frm), group);
    }
    if (status === "Awaiting Bank") {
        frm.add_custom_button(__("Check Bank Status"), () => ipo_check_bank_status(frm), group);
    }
    if (status === "Needs Verification" && ipo_is_manager()) {
        frm.add_custom_button(__("Resolve Verification"), () => ipo_resolve_dialog(frm), group);
    }
    if (status === "Completed" && !frm.doc.payment_entry && ipo_is_manager()) {
        frm.add_custom_button(__("Create Payment Entry"), () => ipo_create_payment_entry(frm), group);
    }
}

function ipo_confirm_execution(frm) {
    frappe.confirm(
        __("Are you sure you want to send this payment of {0} to Banco Inter?", [
            format_currency(frm.doc.amount, "BRL"),
        ]),
        async () => {
            const result = await call_on_fresh_doc(
                frm, ["Approved"], "execute_payment", {}, IPO_RELOAD_AFTER_EXECUTE_MS
            );
            if (result && result.message && result.message.status === "queued") {
                frappe.show_alert({
                    message: __("The form reloads in a few seconds with the bank's answer."),
                    indicator: "blue",
                });
            }
        }
    );
}

async function ipo_check_bank_status(frm) {
    const result = await call_on_fresh_doc(frm, ["Awaiting Bank"], "check_bank_status");
    if (!result) {
        return;
    }
    const outcome = result.message || {};
    if (!IPO_BANK_ANSWERED.includes(outcome.status)) {
        // Disabled integration, a refused or failed request, no bank id: the status on screen is
        // the OLD one and saying otherwise would send the operator to the wrong conclusion.
        frappe.msgprint({
            title: __("The bank status was NOT updated"),
            indicator: "orange",
            message: frappe.utils.escape_html(outcome.message || outcome.status || "-"),
        });
        return;
    }
    frappe.show_alert({
        message: outcome.message
            ? frappe.utils.escape_html(outcome.message)
            : __("The bank was asked. The form shows the current status."),
        indicator: "blue",
    });
}

async function ipo_create_payment_entry(frm) {
    const result = await call_on_fresh_doc(frm, ["Completed"], "create_payment_entry");
    if (!result) {
        return;
    }
    if (result.message) {
        frappe.show_alert({ message: __("Payment Entry {0} created", [result.message]), indicator: "green" });
    } else {
        frappe.msgprint(__("No Payment Entry was created. See the timeline of this order for the reason."));
    }
}

// All three, always. Whether the bank still has the last word depends on its id, on its last
// answer and on how old the request is - a rule this form would only copy badly. The server
// refuses with its reason, and the dialog stays open with what the operator typed.
function ipo_resolve_outcomes() {
    return [
        { value: "at_bank", label: __("The bank holds it - I have the bank's request id") },
        { value: "paid", label: __("It was paid - I have the date and the reference") },
        { value: "not_paid", label: __("It was not paid and the bank does not hold it") },
    ];
}

function ipo_resolve_fields(frm) {
    const needs_reference = "eval:['at_bank', 'paid'].includes(doc.outcome)";
    const is_paid = "eval:doc.outcome=='paid'";
    const is_not_paid = "eval:doc.outcome=='not_paid'";
    return [
        {
            fieldname: "outcome", fieldtype: "Select", label: __("What did you find at the bank?"),
            options: ipo_resolve_outcomes(), reqd: 1,
        },
        {
            fieldname: "bank_reference", fieldtype: "Data", label: __("Bank Reference"),
            description: __("codigoSolicitacao (Pix) or codigoTransacao (boleto); for a paid Pix, the end-to-end id of the statement"),
            default: frm.doc.approval_code || "",
            depends_on: needs_reference, mandatory_depends_on: needs_reference,
        },
        {
            fieldname: "paid_on", fieldtype: "Date", label: __("Paid On"),
            depends_on: is_paid, mandatory_depends_on: is_paid,
        },
        {
            fieldname: "not_in_statement", fieldtype: "Check", depends_on: is_not_paid,
            label: __("I checked the account statement: this payment is not there"),
        },
        {
            fieldname: "not_in_approval_queue", fieldtype: "Check", depends_on: is_not_paid,
            label: __("I checked the bank's approval queue: this payment is not waiting there"),
        },
        {
            fieldname: "not_in_scheduled_payments", fieldtype: "Check", depends_on: is_not_paid,
            label: __("I checked the bank's scheduled payments: this payment is not there"),
        },
        {
            fieldname: "note", fieldtype: "Small Text", label: __("Note"),
            mandatory_depends_on: is_not_paid,
        },
    ];
}

// What is missing for the chosen outcome, or null. The server checks again; a Check field that is
// "mandatory" passes Frappe's own validation with 0, so the three confirmations are checked here.
function ipo_resolve_problem(values) {
    if (["at_bank", "paid"].includes(values.outcome) && !(values.bank_reference || "").trim()) {
        return __("Bank Reference is required for this outcome.");
    }
    if (values.outcome === "paid" && !values.paid_on) {
        return __("Paid On is required when the payment was paid.");
    }
    if (values.outcome === "not_paid") {
        if (!IPO_NOT_PAID_CHECKS.every((fieldname) => values[fieldname])) {
            return __("Confirm the three checks at the bank before declaring this payment as not paid.");
        }
        if (!(values.note || "").trim()) {
            return __("A note is required when the payment was not paid.");
        }
    }
    return null;
}

// not_paid is the only outcome that frees the invoice, and it rests on what a person swears they
// looked at. The checkboxes live in the browser, so carry them into the note: the timeline is
// where this attestation has to survive.
function ipo_resolve_note(values) {
    const note = (values.note || "").trim();
    if (values.outcome !== "not_paid") {
        return note;
    }
    return `Checked at the bank, not there: ${IPO_NOT_PAID_CHECK_LABELS.join(", ")}. ${note}`;
}

function ipo_resolve_dialog(frm) {
    let running = false;
    const dialog = new frappe.ui.Dialog({
        title: __("Resolve Verification"),
        fields: ipo_resolve_fields(frm),
        primary_action_label: __("Resolve"),
        primary_action: async (values) => {
            if (running) {
                return;
            }
            const problem = ipo_resolve_problem(values);
            if (problem) {
                frappe.msgprint(problem);
                return;
            }
            running = true;
            dialog.disable_primary_action();
            try {
                const result = await call_on_fresh_doc(frm, ["Needs Verification"], "resolve_verification", {
                    outcome: values.outcome,
                    bank_reference: (values.bank_reference || "").trim(),
                    paid_on: values.paid_on || null,
                    note: ipo_resolve_note(values),
                });
                // Only on success: a refusal (future date, reference already used) must keep the
                // dialog open with what the operator typed.
                if (result) {
                    dialog.hide();
                }
            } finally {
                running = false;
                dialog.enable_primary_action();
            }
        },
    });
    dialog.show();
}
