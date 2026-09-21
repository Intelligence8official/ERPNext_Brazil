// Runs the Inter Payment Order form script in a stubbed desk and reports what it DID.
//
// The form is the operator's console during a payment incident, so "the source text mentions
// reload_doc" is not evidence. This drives the real handlers with a fake frappe/frm and prints one
// JSON object; test_inter_payment_order_form.py asserts on it. No browser, no network, no jQuery.
//
// Usage: node _form_js_harness.js <path to inter_payment_order.js>

const fs = require("fs");
const vm = require("vm");

const SOURCE = fs.readFileSync(process.argv[2], "utf8");
const sleep = (ms) => new Promise((resolve) => globalThis.setTimeout(resolve, ms));

// One sandbox per scenario: the form script declares constants at the top level.
function desk() {
    const events = [];
    const handlers = {};
    const timers = [];
    const sandbox = {
        console: { error() {} },
        setTimeout: (fn, ms) => timers.push({ fn, ms }),
        __: (text, args) => (args ? String(text).replace(/\{(\d+)\}/g, (_, i) => args[i]) : text),
        format_currency: (value, currency) => `${currency} ${value}`,
        frappe: {
            ui: {
                form: { on: (doctype, hooks) => Object.assign(handlers, hooks) },
                Dialog: function (opts) {
                    sandbox.dialog = this;
                    this.opts = opts;
                    this.show = () => events.push(["dialog.show"]);
                    this.hide = () => events.push(["dialog.hide"]);
                    this.disable_primary_action = () => events.push(["dialog.disable"]);
                    this.enable_primary_action = () => events.push(["dialog.enable"]);
                },
            },
            msgprint: (m) => events.push(["msgprint", typeof m === "string" ? m : m.message]),
            show_alert: (m) => events.push(["show_alert", m.message]),
            confirm: (message, on_yes) => {
                sandbox.confirm_yes = on_yes;
            },
            user: { has_role: () => true },
            utils: { escape_html: (t) => String(t).replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`) },
            db: { get_value: async () => ({ message: {} }) },
        },
    };
    sandbox.events = events;
    sandbox.handlers = handlers;
    sandbox.timers = timers;
    vm.createContext(sandbox);
    vm.runInContext(SOURCE, sandbox);
    return sandbox;
}

// A form whose server call is slow enough for a second click to land during it.
function form(sandbox, doc, options = {}) {
    const buttons = {};
    const frm = {
        doc: doc,
        buttons: buttons,
        intro: null,
        page: {
            set_indicator() {},
            clear_secondary_action: () => sandbox.events.push(["clear_secondary_action"]),
        },
        set_intro: (message) => (frm.intro = message),
        add_custom_button: (label, fn) => (buttons[label] = fn),
        has_perm: () => options.may_submit !== false,
        is_dirty: () => Boolean(options.dirty),
        reload_doc: async () => {
            sandbox.events.push(["reload_doc", frm.doc.name]);
            await sleep(10);
            if (options.on_reload) {
                options.on_reload(frm);
            }
        },
        call: async (opts) => {
            sandbox.events.push(["call", opts.method, frm.doc.name, JSON.stringify(opts.args || {})]);
            await sleep(20);
            if (options.server_throws) {
                throw new Error("server refused");
            }
            return { message: options.answer === undefined ? null : options.answer };
        },
    };
    return frm;
}

const calls = (sandbox) => sandbox.events.filter((e) => e[0] === "call");
const kinds = (sandbox) => sandbox.events.map((e) => e[0]);

async function double_click_button(status, label) {
    const sandbox = desk();
    const frm = form(sandbox, { name: "IPO-A", docstatus: 1, status: status });
    sandbox.handlers.refresh(frm);
    frm.buttons[label]();
    frm.buttons[label]();
    await sleep(120);
    return { calls: calls(sandbox).length };
}

async function double_click_dialog() {
    const sandbox = desk();
    const frm = form(sandbox, { name: "IPO-A", docstatus: 1, status: "Needs Verification" });
    sandbox.handlers.refresh(frm);
    frm.buttons["Resolve Verification"]();
    const values = { outcome: "paid", bank_reference: "E1", paid_on: "2026-03-30" };
    sandbox.dialog.opts.primary_action(values);
    sandbox.dialog.opts.primary_action(values);
    await sleep(120);
    return { calls: calls(sandbox).length, hides: sandbox.events.filter((e) => e[0] === "dialog.hide").length };
}

async function resolve_outcome(options, values) {
    const sandbox = desk();
    const frm = form(sandbox, { name: "IPO-A", docstatus: 1, status: "Needs Verification" }, options);
    sandbox.handlers.refresh(frm);
    frm.buttons["Resolve Verification"]();
    await sandbox.dialog.opts.primary_action(values);
    await sleep(60);
    const call = calls(sandbox)[0];
    return {
        hidden: kinds(sandbox).includes("dialog.hide"),
        re_enabled: kinds(sandbox).includes("dialog.enable"),
        args: call ? JSON.parse(call[3]) : null,
    };
}

async function navigation_during_reload() {
    const sandbox = desk();
    const frm = form(
        sandbox,
        { name: "IPO-A", docstatus: 1, status: "Approved", amount: 100 },
        { on_reload: (f) => (f.doc = { name: "IPO-B", docstatus: 1, status: "Approved", amount: 99999 }) }
    );
    sandbox.handlers.refresh(frm);
    frm.buttons["Execute Payment"]();
    await sandbox.confirm_yes();
    await sleep(60);
    // The delayed reload must not land on the document the operator moved to.
    sandbox.timers.forEach((t) => t.fn());
    const reloads_of_b = sandbox.events.filter((e) => e[0] === "reload_doc" && e[1] === "IPO-B");
    return { calls: calls(sandbox).length, warned: kinds(sandbox).includes("msgprint"), reloads_of_b: reloads_of_b.length };
}

async function check_bank_status(answer) {
    const sandbox = desk();
    const frm = form(sandbox, { name: "IPO-A", docstatus: 1, status: "Awaiting Bank" }, { answer: answer });
    sandbox.handlers.refresh(frm);
    await frm.buttons["Check Bank Status"]();
    await sleep(60);
    const told = sandbox.events.filter((e) => ["show_alert", "msgprint"].includes(e[0]));
    return { told: told.length ? told[told.length - 1][0] : null };
}

function intro_for(doc) {
    const sandbox = desk();
    const frm = form(sandbox, Object.assign({ name: "IPO-A", docstatus: 1 }, doc));
    sandbox.handlers.refresh(frm);
    return { intro: frm.intro };
}

function buttons_for(status, options) {
    const sandbox = desk();
    const frm = form(sandbox, { name: "IPO-A", docstatus: 1, status: status }, options || {});
    sandbox.handlers.refresh(frm);
    return {
        labels: Object.keys(frm.buttons),
        cancel_cleared: kinds(sandbox).includes("clear_secondary_action"),
    };
}

(async () => {
    const STATUSES = [
        "Draft", "Pending Approval", "Approved", "Processing", "Awaiting Bank",
        "Needs Verification", "Completed", "Failed", "Cancelled",
    ];
    const not_paid = {
        outcome: "not_paid", note: "nothing in the statement",
        not_in_statement: 1, not_in_approval_queue: 1, not_in_scheduled_payments: 1,
    };
    const result = {
        double_click_approve: await double_click_button("Pending Approval", "Approve"),
        double_click_check: await double_click_button("Awaiting Bank", "Check Bank Status"),
        double_click_dialog: await double_click_dialog(),
        resolve_success: await resolve_outcome({}, { outcome: "paid", bank_reference: "E1", paid_on: "2026-03-30" }),
        resolve_refused: await resolve_outcome({ server_throws: true }, { outcome: "paid", bank_reference: "E1", paid_on: "2026-03-30" }),
        resolve_not_paid: await resolve_outcome({}, not_paid),
        navigation_during_reload: await navigation_during_reload(),
        check_bank_status: {},
        intro_escapes_bank_text: intro_for({ status: "Awaiting Bank", bank_status: "<img src=x onerror=alert(1)>" }),
        buttons: {},
        buttons_without_submit_perm: buttons_for("Approved", { may_submit: false }),
    };
    for (const answer of [
        { status: "blocked", message: "The Banco Inter integration is disabled" },
        { status: "error", message: "403 Forbidden", http_status: 403 },
        { status: "no_bank_id" },
        { status: "skipped" },
        { status: "brand_new_status_from_a_later_version" },
        { status: "unchanged", message: "The bank has no record of this payment yet" },
        { status: "completed" },
        { status: "failed" },
        { status: "awaiting_bank" },
        { status: "needs_verification" },
    ]) {
        result.check_bank_status[answer.status] = await check_bank_status(answer);
    }
    for (const status of STATUSES) {
        result.buttons[status] = buttons_for(status);
    }
    result.buttons["Completed with entry"] = (() => {
        const sandbox = desk();
        const frm = form(sandbox, { name: "IPO-A", docstatus: 1, status: "Completed", payment_entry: "PE-1" });
        sandbox.handlers.refresh(frm);
        return { labels: Object.keys(frm.buttons), cancel_cleared: kinds(sandbox).includes("clear_secondary_action") };
    })();
    process.stdout.write(JSON.stringify(result, null, 1));
})();
