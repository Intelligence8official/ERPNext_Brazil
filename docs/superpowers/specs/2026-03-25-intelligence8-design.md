# Intelligence8 — AI-Native ERP Agent for ERPNext

**Date:** 2026-03-25
**Status:** Approved Design
**Module:** brazil_module/services/intelligence/

---

## 1. Vision

Intelligence8 is an autonomous AI agent that operates ERPNext as a human employee would — receiving inputs, understanding context, making decisions, and executing actions. Instead of hiring operators, the AI handles the entire ERP operation with human supervision only for high-stakes decisions.

### Core Principles

- **LLM as central brain** — not rules with LLM in the gaps
- **Confidence-based autonomy** — executes alone when confident, escalates to human when not
- **Full auditability** — every decision logged: who decided (agent vs human), why, data used, outcome
- **Omnichannel** — Telegram-first + ERP Chat, unified thread regardless of channel
- **Module-agnostic** — starts with Finance, expands progressively; architecture supports any ERPNext module
- **Model tiering** — Haiku 4.5 (~80%), Sonnet 4.6 (~15%), Opus 4.6 (~5%) based on decision complexity
- **LLM cost dashboard** — granular tracking per function, module, department
- **Prompt caching** — Claude API prompt caching for system prompts and module context to reduce costs 50-90%
- **Circuit breaker** — graceful degradation when Claude API is unavailable
- **Idempotency** — every action has a deduplication key to prevent duplicate documents

---

## 2. Architecture

### 2.1 Layer Model

```
+--------------------------------------------------+
|            Intelligence8 (NEW)                    |
|  Agent Brain + Context Builder + Channels         |
|                  | uses as tools                  |
+------------------+-------------------------------+
|  Existing Services (KEPT AS-IS)                   |
|  Fiscal Services | Banking Services | Email Mon.  |
+--------------------------------------------------+
|  Frappe / ERPNext (UNCHANGED)                     |
+--------------------------------------------------+
```

Intelligence8 is a layer above existing services. Existing code is preserved and called as tools by the agent.

### 2.2 Core Components

| Component | Responsibility | State |
|---|---|---|
| **Agent Brain** (`agent.py`) | Receives events, reasons via Claude API + tool use, decides actions | Stateless per request |
| **Context Builder** (`context_builder.py`) | Assembles relevant context for each decision (history, profiles, module rules) | Reads from ERPNext + Agent Memory |
| **Decision Engine** (`decision_engine.py`) | Confidence check + routing (auto-execute vs human approval) | Configurable thresholds |
| **Action Executor** (`action_executor.py`) | Executes actions in ERPNext via allowlisted operations only (sandboxed) | Via Frappe internal API |
| **Cost Tracker** (`cost_tracker.py`) | Logs tokens consumed per call with tags (module, function, model) | I8 Cost Log DocType |
| **Channel Router** (`channels/channel_router.py`) | Receives from any channel, normalizes, routes to Brain, returns response | Unified thread in DocType |

### 2.3 Decision Flow

```
Event (email arrives, cron fires, user sends Telegram msg)
  -> Channel Router identifies input
    -> Context Builder assembles relevant context
      -> Agent Brain (Claude API + tools) reasons and decides
        -> Confidence check:
           >= threshold -> Action Executor runs + Decision Ledger records
           < threshold  -> Channel Router requests human approval (Telegram/ERP)
             -> Human approves/rejects -> Decision Ledger records (flag: human_override)
               -> Action Executor runs (or not)
```

### 2.4 Security: Action Executor Sandboxing

The Action Executor operates with an explicit allowlist of permitted operations per module. The agent CANNOT perform arbitrary DocType CRUD.

```python
ACTION_ALLOWLIST = {
    "Purchase Order": ["create", "submit", "cancel"],
    "Purchase Invoice": ["create", "submit"],
    "Payment Entry": ["create", "submit"],
    "Journal Entry": ["create", "submit"],
    "Inter Payment Order": ["create"],
    "Nota Fiscal": ["read", "update_status"],
    "Bank Transaction": ["read", "reconcile"],
    "Supplier": ["read", "create", "update"],
    "Item": ["read", "create"],
    "Communication": ["read", "create"],
    # Explicitly NOT allowed: Company, User, Role, DocType, System Settings, etc.
}
```

Rules:
- All `submit` and `cancel` operations MUST pass through Decision Engine (no bypass)
- `delete` is NEVER permitted for the agent on any DocType
- `erp_tools.py` enforces the allowlist before executing; violations are logged and blocked

### 2.5 Security: Telegram Authentication

```python
# Webhook validation
- secret_token set when registering webhook with Telegram API
- Every incoming webhook validated via X-Telegram-Bot-Api-Secret-Token header
- Rejected requests logged to I8 Decision Log with actor="unauthorized"

# User authorization
- I8 Telegram User child table maps telegram_user_id -> Frappe User
- Each Telegram user has approval_limit (currency) — max value they can approve
- Unrecognized user_id messages are ignored + logged
- For approvals above a configurable threshold: require confirmation PIN
```

### 2.6 Idempotency

Every agent action includes a deduplication key to prevent duplicate operations when the scheduler runs overlapping cycles:

- Redis lock per event ID (`i8:lock:{event_type}:{event_id}`, TTL 5 min)
- Before creating any document, check for existing by natural key (e.g., PO for same supplier+month for recurring, NF by chave_de_acesso)
- Decision Log entries are unique per `event_type + event_id + action`

### 2.7 Circuit Breaker

```python
CIRCUIT_BREAKER = {
    "failure_threshold": 5,        # consecutive failures to open circuit
    "recovery_timeout_seconds": 300,  # 5 min before retry
    "half_open_max_calls": 2       # test calls before closing circuit
}
```

States: CLOSED (normal) -> OPEN (after N failures, stops calling) -> HALF-OPEN (test calls after timeout) -> CLOSED

When circuit is OPEN:
- Pending events are queued, not dropped
- Telegram notification: "Agent paused: Claude API unavailable. Retrying in 5 min."
- Critical approvals fall back to Telegram message with manual action instructions

### 2.8 Model Selection

```python
HAIKU_EVENTS = [
    "classify_email", "format_notification", "status_check",
    "simple_match", "recurring_schedule"
]
OPUS_EVENTS = [
    "anomaly_detected", "high_value_decision",
    "complex_reconciliation", "multi_document_analysis"
]
# Everything else -> Sonnet
```

---

## 3. New DocTypes

### 3.1 I8 Agent Settings (Single)

Global configuration for Intelligence8.

**Section: General**

| Field | Type | Description |
|---|---|---|
| `enabled` | Check | Master switch |
| `default_confidence_threshold` | Float | Default: 0.85 |
| `high_value_confirmation_pin` | Check | Require PIN for high-value approvals |
| `high_value_threshold` | Currency | Amount above which PIN is required |

**Section: AI Models**

| Field | Type | Description |
|---|---|---|
| `anthropic_api_key` | Password | Claude API key (fallback; prefer env var ANTHROPIC_API_KEY) |
| `haiku_model` | Data | Model ID for simple decisions |
| `sonnet_model` | Data | Model ID for medium decisions |
| `opus_model` | Data | Model ID for complex decisions |
| `max_requests_per_minute` | Int | Client-side rate limit before calling Claude |
| `haiku_timeout_seconds` | Int | Default: 30 |
| `sonnet_timeout_seconds` | Int | Default: 60 |
| `opus_timeout_seconds` | Int | Default: 120 |
| `enable_prompt_caching` | Check | Use Claude prompt caching for system/context prompts |

**Section: Telegram**

| Field | Type | Description |
|---|---|---|
| `telegram_bot_token` | Password | Telegram Bot API token (fallback; prefer env var) |
| `telegram_webhook_secret` | Password | Secret token for webhook validation |
| `telegram_chat_id` | Data | Primary chat ID for notifications |
| `telegram_users` | Table(I8 Telegram User) | Authorized users with approval limits |

**Section: Budget & Alerts**

| Field | Type | Description |
|---|---|---|
| `daily_budget_usd` | Currency | Daily cost limit |
| `monthly_budget_usd` | Currency | Monthly cost limit |
| `pause_on_budget_exceeded` | Check | Stop agent if budget exceeded |
| `cost_anomaly_threshold` | Float | Alert if daily cost > X times average |
| `briefing_time` | Time | Daily briefing hour (default: 08:00) |
| `briefing_enabled` | Check | Enable daily briefing |

**Section: Graceful Degradation**

| Field | Type | Description |
|---|---|---|
| `circuit_breaker_threshold` | Int | Consecutive failures to open circuit (default: 5) |
| `circuit_breaker_recovery_seconds` | Int | Seconds before retry (default: 300) |

### 3.1.1 I8 Telegram User (Child Table)

| Field | Type | Description |
|---|---|---|
| `telegram_user_id` | Data | Telegram user ID (numeric) |
| `user` | Link(User) | Mapped Frappe user |
| `approval_limit` | Currency | Max value this user can approve (0 = unlimited) |
| `active` | Check | Enable/disable |

### 3.2 I8 Decision Log (Submittable, immutable)

Immutable audit trail for every agent decision. Uses `is_submittable = 1` so entries are auto-submitted on creation and cannot be edited or deleted. `track_changes = 1` for Frappe's built-in audit.

| Field | Type | Description |
|---|---|---|
| `timestamp` | Datetime | When decision was made |
| `event_type` | Data | What triggered the decision |
| `module` | Data | fiscal, banking, p2p, email, conversation |
| `action` | Data | What was done (create_po, approve_invoice, etc.) |
| `actor` | Select | "agent" or "human" |
| `channel` | Select | telegram, erp_chat, system, email |
| `confidence_score` | Float | Agent's confidence (0.0-1.0) |
| `model_used` | Data | Which Claude model |
| `input_summary` | Long Text | Summary of data the agent analyzed |
| `reasoning` | Long Text | Agent's reasoning for the decision |
| `result` | Select | success, failed, rejected, pending |
| `related_doctype` | Link(DocType) | Document affected |
| `related_docname` | Dynamic Link | Specific document name |
| `cost_usd` | Currency | LLM cost for this decision |
| `human_override` | Check | Was original decision overridden? |
| `human_feedback` | Small Text | Feedback when human corrects |

### 3.3 I8 Conversation (Regular)

Unified omnichannel conversation thread.

| Field | Type | Description |
|---|---|---|
| `subject` | Data | Auto-generated summary |
| `status` | Select | active, resolved, archived |
| `related_doctype` | Link(DocType) | Primary related document |
| `related_docname` | Dynamic Link | Specific document |
| `messages` | Table(I8 Conversation Message) | All messages |

### 3.4 I8 Conversation Message (Child Table)

| Field | Type | Description |
|---|---|---|
| `channel` | Select | telegram, erp_chat, email, system |
| `direction` | Select | incoming, outgoing, internal |
| `actor` | Select | agent, human |
| `content` | Long Text | Message content |
| `timestamp` | Datetime | When sent/received |
| `related_doctype` | Link(DocType) | Related document |
| `related_docname` | Dynamic Link | Specific document |
| `telegram_message_id` | Data | For Telegram callback tracking |

### 3.5 I8 Module Registry (Regular)

Pluggable module registration. Tool names are namespaced by module to avoid conflicts (e.g., `fiscal.create_nf`, `banking.pay_pix`).

| Field | Type | Description |
|---|---|---|
| `module_name` | Data | e.g., "fiscal", "banking", "p2p" |
| `description` | Small Text | What this module does |
| `tools_definition` | Code (JSON) | Tool schemas for Claude API (namespaced: `{module}.{tool}`) |
| `context_prompt` | Code (text) | Module-specific system prompt fragment |
| `enabled` | Check | Active/inactive |
| `default_model` | Select | haiku, sonnet, opus |

Example tool schema entry:
```json
{
  "name": "p2p.create_purchase_order",
  "description": "Creates a Purchase Order in ERPNext",
  "input_schema": {
    "type": "object",
    "properties": {
      "supplier": {"type": "string"},
      "required_by": {"type": "string", "format": "date"},
      "items": {"type": "array", "items": {"type": "object", "properties": {"item_code": {"type": "string"}, "qty": {"type": "number"}, "rate": {"type": "number"}}}}
    },
    "required": ["supplier", "required_by", "items"]
  }
}
```

### 3.6 I8 Supplier Profile (Regular)

Per-supplier communication and behavior configuration.

| Field | Type | Description |
|---|---|---|
| `supplier` | Link(Supplier) | Which supplier |
| `contact_email` | Data | Email for PO/follow-up |
| `communication_channel` | Select | email (future: whatsapp, telegram) |
| `email_template` | Link(Email Template) | Template for PO notification |
| `expected_nf_days` | Int | Days to expect NF after PO |
| `follow_up_after_days` | Int | Days before first follow-up |
| `max_follow_ups` | Int | Max follow-up attempts |
| `follow_up_interval_days` | Int | Days between follow-ups |
| `auto_pay` | Check | Auto-pay when NF arrives |
| `payment_method` | Select | PIX, TED, Boleto |
| `notes` | Long Text | Agent context about this supplier |
| `reliability_score` | Float | Calculated from history |

### 3.7 I8 Recurring Expense (Regular)

Intelligent replacement for Auto Repeat.

| Field | Type | Description |
|---|---|---|
| `title` | Data | e.g., "Hosting DigitalOcean" |
| `supplier` | Link(Supplier) | Supplier |
| `document_type` | Select | Purchase Order, Journal Entry |
| `estimated_amount` | Currency | Expected amount |
| `currency` | Link(Currency) | Transaction currency |
| `frequency` | Select | Monthly, Weekly, Quarterly, Yearly |
| `day_of_month` | Int | Day to create document |
| `lead_days` | Int | Create X days before due date |
| `notify_supplier` | Check | Send email to supplier |
| `follow_up_after_days` | Int | Follow-up if NF not received |
| `max_follow_ups` | Int | Max follow-up attempts |
| `auto_pay` | Check | Auto-pay when NF arrives |
| `payment_method` | Select | PIX, TED, Boleto |
| `amount_tolerance_percent` | Float | Acceptable variation % (e.g., 10 for variable-cost services) |
| `confidence_threshold` | Float | Override per expense |
| `cost_center` | Link(Cost Center) | For accounting |
| `department` | Link(Department) | For cost allocation |
| `active` | Check | Enable/disable |
| `items` | Table(I8 Recurring Expense Item) | Line items |
| `last_created` | Date | Last document created |
| `next_due` | Date | Next scheduled creation |

### 3.8 I8 Recurring Expense Item (Child Table)

| Field | Type | Description |
|---|---|---|
| `item_code` | Link(Item) | Item |
| `qty` | Float | Quantity |
| `rate` | Currency | Unit rate |
| `expense_account` | Link(Account) | Expense account |
| `cost_center` | Link(Cost Center) | Cost center |

### 3.9 I8 Cost Log (Regular)

LLM cost tracking per call.

| Field | Type | Description |
|---|---|---|
| `timestamp` | Datetime | When the call was made |
| `module` | Data | fiscal, banking, p2p, email, conversation |
| `function_name` | Data | classify_email, create_po, match_nf, etc. |
| `model` | Data | haiku-4.5, sonnet-4.6, opus-4.6 |
| `tokens_in` | Int | Input tokens |
| `tokens_out` | Int | Output tokens |
| `cost_usd` | Currency | Calculated cost |
| `latency_ms` | Int | Response time in milliseconds |
| `cache_hit` | Check | Whether prompt cache was used |
| `decision_log` | Link(I8 Decision Log) | Linked decision |
| `company` | Link(Company) | Company |
| `department` | Link(Department) | Department |

---

## 4. Procure-to-Pay (P2P) Flow — Phase 1 Core

### 4.1 Complete Flow

```
1. PURCHASE ORDER
   Agent creates PO (recurring or on-demand)
   -> Notifies supplier via email with PO number
   -> Schedules follow-up if NF doesn't arrive on time

2. NF RECEIPT
   NF arrives (SEFAZ, email, or manual)
   -> Agent interprets and links to PO automatically
   -> Creates Purchase Invoice (draft or submitted)
   -> Validates: amounts match? Items correct? CNPJ ok?

3. PAYMENT
   Agent identifies invoices due
   -> Creates Payment Entry or Inter Payment Order
   -> Confidence check: value < threshold -> executes
   -> High value -> requests approval via Telegram
   -> Executes payment (PIX/TED/Boleto via Banco Inter)

4. RECONCILIATION
   Bank statement synced automatically
   -> Agent matches: Bank Transaction <-> Payment Entry
   -> Ambiguous cases: LLM analyzes reference + context
   -> Records reconciliation in Decision Ledger

5. FOLLOW-UP & EXCEPTIONS
   NF didn't arrive on time? -> Follow-up to supplier
   NF amount diverges from PO? -> Alert via Telegram
   Payment failed? -> Retry + notification
   Duplicate detected? -> Block + request human decision
```

### 4.2 Recurring Expenses — Replacing Auto Repeat

Monthly agent workflow for each active I8 Recurring Expense:

1. Calculate `required_by` = due date based on frequency + day_of_month
2. Create PO with items, values, and correct dynamic date (lead_days before due)
3. Send email to supplier (if notify_supplier enabled, using I8 Supplier Profile template)
4. Monitor NF arrival (SEFAZ + email pipeline)
5. When NF arrives -> link to PO -> create Invoice
6. If auto_pay -> schedule payment -> request approval if above threshold
7. Auto-reconcile when debit appears in bank statement

### 4.3 Supplier Communication

Per-supplier configuration via I8 Supplier Profile:
- Preferred channel (email, future: WhatsApp/Telegram)
- Email template for PO notification
- Expected NF delivery time
- Follow-up schedule (after X days, max Y attempts, Z days interval)
- Escalation: if max follow-ups exceeded, notify human via Telegram

---

## 5. Email Intelligence

### 5.1 Classification

Every incoming email is classified by the agent (Haiku):

| Category | Action |
|---|---|
| FISCAL | NF, boleto, charge -> Fiscal pipeline |
| COMMERCIAL | proposal, quote, order -> Purchase/Sales pipeline |
| FINANCIAL | statement, receipt, notice -> Banking pipeline |
| OPERATIONAL | supplier responding about PO, follow-up -> Link to existing conversation |
| SPAM/IRRELEVANT | marketing, newsletter, promo -> Discard + log |
| UNCERTAIN | can't classify -> Ask via Telegram |

### 5.2 Differences from Current System

| Aspect | Current (email_monitor.py) | Intelligence8 |
|---|---|---|
| What it analyzes | Attachments only (XML/PDF/ZIP) | Subject + body + attachments + sender |
| Classification | Pattern matching on subject | LLM understands semantic context |
| Emails without attachments | Ignored | Classified and processed |
| Spam/irrelevant | No filtering | Discarded with Decision Ledger log |
| Supplier response | Not recognized | Linked to thread + PO/NF |
| International email | 14 hardcoded vendors | LLM identifies any vendor |

### 5.3 Refactoring email_monitor.py

The existing email_monitor.py attachment extraction logic is preserved. Changes:
- Classification moves from pattern matching to Agent Brain (Haiku)
- Body/subject analysis added (currently ignored)
- Results feed into unified Decision Ledger
- Orchestration moves from direct cron to agent event loop

**Email field transition:** New custom fields added to Communication:
- `i8_processed` (Check) — Intelligence8 processing flag
- `i8_classification` (Select) — FISCAL, COMMERCIAL, FINANCIAL, OPERATIONAL, SPAM, UNCERTAIN
- `i8_decision_log` (Link to I8 Decision Log) — traceability

The existing `nf_processed` field is maintained for backward compatibility during Phase 1. The email_monitor.py continues to set `nf_processed` for attachment extraction; Intelligence8 sets `i8_processed` after classification. Both can coexist.

---

## 6. Omnichannel Communication

### 6.1 Channels

| Channel | Direction | Use |
|---|---|---|
| **Telegram Bot** | Bidirectional | Primary interaction: approvals, commands, questions, briefings |
| **ERP Chat Widget** | Bidirectional | In-ERP interaction: same thread, detailed views |
| **Email** | Outgoing | Supplier communication (PO, follow-up) |
| **System** | Internal | Agent internal actions logged to thread |

### 6.2 Unified Thread

All channels write to the same I8 Conversation. A conversation started in Telegram can be continued in ERP Chat and vice versa. The agent always has full context.

**Context window strategy:** When building context for the agent, the Context Builder uses a sliding window: last 20 messages in full + a Haiku-generated summary of older messages. This prevents context window overflow and reduces cost for long-running conversations.

### 6.3 Telegram Bot

**Webhook-based** (not polling) for Frappe efficiency.

Proactive notifications:
- Daily briefing (configurable time)
- Pending approvals with inline buttons (Approve / Reject / Details)
- Exception alerts (NF value mismatch, payment failure, supplier delay)

Human commands:
- Direct response: "approve", "reject", "details"
- Questions: "how much did we spend on AWS this month?"
- Actions: "create PO of R$3k for supplier X"
- Status: "pending", "summary", "payments today"

### 6.4 Approval Flow

```
AGENT -> Telegram:
  "Approval needed
   Purchase Invoice: ACC-PINV-2026-00142
   Supplier: DigitalOcean
   Amount: USD 500.00 (R$2,450.00)
   Ref: PO-2026-00089
   Confidence: 0.78
   Reason: Amount 2% below PO

   [Approve]  [Reject]  [Details]"

HUMAN taps "Approve"

AGENT -> Decision Ledger:
  {decision: "approve_invoice", actor: "human",
   channel: "telegram", confidence: 0.78,
   document: "ACC-PINV-2026-00142"}

AGENT -> Telegram:
  "Invoice approved. Payment scheduled for 05/04."
```

### 6.5 ERP Chat Widget

JavaScript component in ERPNext that:
- Shows the unified conversation thread
- Allows text input (natural language commands)
- Displays agent actions with links to related documents
- Available on a dedicated page and as a floating widget

---

## 7. Cost Dashboard

### 7.1 I8 Cost Log

Every LLM call is logged with: timestamp, module, function, model, tokens_in, tokens_out, cost_usd, company, department.

### 7.2 Dashboard Views

| View | Shows | Purpose |
|---|---|---|
| Daily/monthly cost | Line chart of total cost per day | Trend and predictability |
| Cost by module | Bars: fiscal, banking, p2p, email, conversation | Identify where agent spends most |
| Cost by function | Treemap: classify_email, create_po, etc. | Optimize expensive functions |
| Cost by model | Pie: Haiku vs Sonnet vs Opus | Validate tiering |
| Cost by department | Horizontal bars | Internal cost allocation |
| Decisions/day | Counter + chart: total, auto, human | Agent operational volume |
| Confidence distribution | Histogram of scores | Calibrate thresholds |
| Estimated savings | Agent cost vs estimated human operator cost | Intelligence8 ROI |

### 7.3 Cost Alerts

Configurable in I8 Agent Settings:
- Daily/monthly budget limits
- Alert channel (Telegram)
- Pause on budget exceeded (optional)
- Anomaly detection (cost > X times average)

---

## 8. Additional Intelligence Features

### 8.1 Cash Flow Intelligence (Phase 3)

Agent sees POs, invoices, recurrences, bank balance and provides:
- 30-day cash flow projection
- Liquidity alerts with suggestions
- Early payment discount optimization

### 8.2 Supplier Intelligence (Phase 3)

Historical analysis per supplier:
- Reliability score (NF delivery time, accuracy)
- Price anomaly detection
- Renegotiation suggestions based on volume
- Duplicate supplier detection (same CNPJ root)

### 8.3 Anomaly Detection (Phase 3)

Pattern deviation detection:
- Duplicate NF by variation (same supplier, same value, different keys)
- Out-of-pattern payments
- Undue charges (no PO or contract)
- Tax divergences

### 8.4 Receivables Management (Phase 2)

Accounts receivable automation:
- Auto-generate boleto/PIX on Sales Invoice submit
- Progressive collection follow-up (3, 7, 15 days overdue)
- Daily receivables summary
- Legal action alert at configurable thresholds

### 8.5 Smart Reporting via Conversation (Phase 3)

Natural language queries via Telegram or ERP Chat:
- "How much did we spend on infrastructure this month?"
- "Generate expense report by supplier for last 6 months"
- Agent queries ERPNext and returns formatted answers

### 8.6 Proactive Fiscal Compliance (Phase 4)

- NF-e manifestation reminders
- Cancellation detection and response
- Tax rate validation
- XML storage with legal retention compliance

### 8.7 Daily Briefing (Phase 2)

Daily executive summary via Telegram at configured time:
- Bank balance
- Receivables and payables for the day
- Pending actions (approvals, NFs, follow-ups)
- Alerts (overdue, anomalies)
- Previous day I8 cost

### 8.8 Learning Loop (Phase 4)

Agent improves over time:
- Human corrections lower confidence for similar decisions
- Human approvals without hesitation raise confidence
- Human rejections trigger more cautious behavior
- All recorded in Decision Ledger for analysis

---

## 9. Directory Structure

```
brazil_module/
  services/
    fiscal/              # Existing, unchanged
    banking/             # Existing, unchanged
    intelligence/        # NEW - Intelligence8
      __init__.py
      agent.py                   # Agent Brain - central orchestrator
      context_builder.py         # Builds context for each decision
      decision_engine.py         # Confidence check + routing
      action_executor.py         # Executes actions in ERPNext
      cost_tracker.py            # LLM cost logging
      tools/
        __init__.py
        fiscal_tools.py          # Wrappers: create NF, process XML
        banking_tools.py         # Wrappers: pay, reconcile
        purchasing_tools.py      # Create PO, follow-up supplier
        email_tools.py           # Classify, reply, search emails
        erp_tools.py             # Generic DocType CRUD
        communication_tools.py   # Send email, notification
      channels/
        __init__.py
        telegram_bot.py          # Telegram Bot (webhook-based)
        erp_chat.py              # Chat widget backend
        channel_router.py        # Unifies channels -> Brain
      recurring/
        __init__.py
        expense_scheduler.py     # Recurring expense cron
        follow_up_manager.py     # Automatic follow-up
      prompts/
        system_prompt.py         # Agent system prompt
        email_classifier.py      # Email classification prompt
        document_creator.py      # Document creation prompt
        approval_formatter.py    # Formats approval messages
  intelligence/          # NEW Frappe module
    doctype/
      i8_agent_settings/
      i8_decision_log/
      i8_conversation/
      i8_conversation_message/
      i8_module_registry/
      i8_supplier_profile/
      i8_recurring_expense/
      i8_recurring_expense_item/
      i8_cost_log/
    workspace/
      intelligence8.json
  public/js/
    sales_invoice.js       # Existing
    purchase_invoice.js    # Existing
    bank_account.js        # Existing
    i8_chat_widget.js      # NEW - omnichannel chat widget
```

---

## 10. Integration with Existing Code

### 10.1 What Changes

| Existing Component | Change |
|---|---|
| `hooks.py` | New scheduled tasks for agent loop, recurring expenses, follow-ups. Doc events route to agent. |
| `email_monitor.py` | Classification moves to Agent Brain. Attachment extraction preserved. |
| `api/__init__.py` | New endpoints: telegram webhook, chat, dashboard data. |
| `setup/install.py` | New DocTypes, roles (Intelligence8 Admin, Intelligence8 Viewer), new custom fields on Communication. |
| `modules.txt` | Add "Intelligence" module. |

### 10.2 What Stays Unchanged

| Component | Reason |
|---|---|
| `services/fiscal/*` (except email_monitor) | Called as tools by the agent |
| `services/banking/*` | Called as tools by the agent |
| All fiscal DocTypes | Agent reads/writes them via Action Executor |
| All banking DocTypes | Agent reads/writes them via Action Executor |
| `public/js/sales_invoice.js` | Existing buttons preserved |
| `public/js/purchase_invoice.js` | Existing buttons preserved |
| `public/js/bank_account.js` | Existing buttons preserved |

### 10.3 Hooks Changes

```python
scheduler_events = {
    "cron": {
        # Intelligence8 - agent event loop
        "*/2 * * * *": [
            "brazil_module.services.intelligence.agent.process_pending_events"
        ],
        # Recurring expenses
        "0 7 * * *": [
            "brazil_module.services.intelligence.recurring.expense_scheduler.daily_check"
        ],
        # Follow-ups
        "0 9 * * *": [
            "brazil_module.services.intelligence.recurring.follow_up_manager.check_overdue"
        ],
        # Existing SEFAZ/banking tasks preserved
    }
}

doc_events = {
    "Communication": {
        "after_insert": [
            "brazil_module.services.fiscal.email_monitor.check_nf_attachment",  # preserved
            "brazil_module.services.intelligence.agent.on_communication"         # new: I8 classification
        ]
    },
    "Nota Fiscal": {
        "after_insert": "brazil_module.services.intelligence.agent.on_nota_fiscal",
        # on_nota_fiscal internally calls processor.process_new_nf for backward compat
        "validate": "brazil_module.services.fiscal.processor.validate_nf"
    },
    "Sales Invoice": {
        "on_submit": "brazil_module.services.banking.boleto_service.on_invoice_submit"  # preserved
    },
    "Payment Entry": {
        "on_submit": "brazil_module.services.banking.reconciliation.on_payment_entry_submit"  # preserved
    }
}

# Note: on_nota_fiscal wraps the existing process_new_nf pipeline, adding:
# - Decision Ledger logging
# - Confidence-based routing for ambiguous matches
# - Telegram notification for exceptions
```

### 10.4 Scheduler Strategy

Agent event processing uses `frappe.enqueue()` with `queue="long"` and explicit timeouts to avoid blocking other scheduled tasks (SEFAZ fetch, boleto status, etc.):

```python
def process_pending_events():
    """Scheduled every 2 min. Enqueues actual processing to long queue."""
    events = get_pending_events()
    for event in events:
        frappe.enqueue(
            "brazil_module.services.intelligence.agent.process_single_event",
            queue="long",
            timeout=max_timeout_for_event(event),
            event_type=event.type,
            event_id=event.id,
            deduplicate=True  # Frappe's built-in job deduplication
        )
```

### 10.5 Graceful Degradation

When `enabled = false` in I8 Agent Settings:
- Scheduled tasks skip processing (check flag at start)
- Pending events remain in queue, not dropped
- Telegram bot responds with "Agent is paused. Contact admin."
- Existing services (SEFAZ fetch, boleto status, etc.) continue normally — they are independent
- Re-enabling processes the backlog in chronological order

### 10.6 Estimated Savings Calculation

```
monthly_savings = sum(
    decision_count_by_type * avg_human_minutes_by_type * hourly_operator_cost / 60
) - monthly_llm_cost
```

Configurable in I8 Agent Settings: `operator_hourly_cost` (Currency). Default human time estimates per decision type are stored in I8 Module Registry.

---

## 11. New Dependencies

```toml
anthropic = ">=0.52.0"           # Claude API client
python-telegram-bot = ">=21.0"   # Telegram Bot API
```

Only two new dependencies. Everything else uses native Frappe.

---

## 12. Phased Roadmap

| Phase | Focus | Features |
|---|---|---|
| **Phase 1** | Core + P2P | Agent Brain, Decision Engine, Action Executor, Cost Tracker, Omnichannel (Telegram + ERP Chat), Decision Ledger, I8 Recurring Expense, full P2P flow (PO -> NF -> Pay -> Reconcile), Supplier Profile with communication |
| **Phase 2** | Email + Receivables | Email Intelligence (replaces pattern matching), Receivables Management (auto-collect), Daily Briefing |
| **Phase 3** | Analytics | Cash Flow Intelligence, Supplier Intelligence, Anomaly Detection, Smart Reporting via conversation |
| **Phase 4** | Compliance + Learning | Proactive Fiscal Compliance, Learning Loop (confidence adjustment), expansion to new ERPNext modules |

---

## 13. Success Criteria

- Agent handles 80%+ of daily P2P operations without human intervention
- Average decision confidence > 0.85 after 30 days of operation
- LLM cost < USD 10/day for standard operation
- Zero unaudited decisions (100% Decision Ledger coverage)
- Telegram response time < 30 seconds for approvals
- NF-to-payment cycle reduced from days to hours
- Follow-up automation eliminates missed NF deadlines
