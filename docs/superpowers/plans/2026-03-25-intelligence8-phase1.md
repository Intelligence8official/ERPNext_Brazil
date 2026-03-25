# Intelligence8 Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Intelligence8 core agent engine with Telegram/ERP Chat omnichannel, P2P tools (Purchase Order, Invoice, Payment, Reconciliation), recurring expense automation, and supplier communication — enabling the agent to operate the full Procure-to-Pay cycle autonomously.

**Architecture:** A service layer (`brazil_module/services/intelligence/`) wraps the existing fiscal/banking services as Claude API tools. The Agent Brain receives events, reasons via Claude tool use, and routes decisions through a confidence-based Decision Engine. All actions are logged to an immutable Decision Ledger. Communication flows through a Channel Router that unifies Telegram and ERP Chat into a single conversation thread.

**Tech Stack:** Python 3.10+, Frappe 15+, Anthropic Python SDK (anthropic>=0.52.0), python-telegram-bot>=21.0, Redis (idempotency locks), Claude API (Haiku/Sonnet/Opus tiering)

**Spec:** `docs/superpowers/specs/2026-03-25-intelligence8-design.md`

---

## File Structure

### New Files (Services Layer)

| File | Responsibility |
|---|---|
| `brazil_module/services/intelligence/__init__.py` | Package init |
| `brazil_module/services/intelligence/agent.py` | Agent Brain — event processing, Claude API calls, tool dispatch |
| `brazil_module/services/intelligence/context_builder.py` | Builds context for each decision (history, profiles, module rules) |
| `brazil_module/services/intelligence/decision_engine.py` | Confidence check, auto-execute vs human approval routing |
| `brazil_module/services/intelligence/action_executor.py` | Sandboxed ERPNext action execution with allowlist |
| `brazil_module/services/intelligence/cost_tracker.py` | LLM cost logging per call |
| `brazil_module/services/intelligence/circuit_breaker.py` | Circuit breaker for Claude API resilience |
| `brazil_module/services/intelligence/tools/__init__.py` | Tool registry — collects and serves tools to agent |
| `brazil_module/services/intelligence/tools/erp_tools.py` | Generic DocType CRUD (allowlisted) |
| `brazil_module/services/intelligence/tools/purchasing_tools.py` | PO creation, supplier notification, follow-up |
| `brazil_module/services/intelligence/tools/fiscal_tools.py` | NF processing, PO linking wrappers |
| `brazil_module/services/intelligence/tools/banking_tools.py` | Payment, reconciliation, statement wrappers |
| `brazil_module/services/intelligence/tools/email_tools.py` | Email classification, search, reply |
| `brazil_module/services/intelligence/tools/communication_tools.py` | Send email, Frappe notification |
| `brazil_module/services/intelligence/channels/__init__.py` | Package init |
| `brazil_module/services/intelligence/channels/channel_router.py` | Unifies channels, routes to Brain, returns response |
| `brazil_module/services/intelligence/channels/telegram_bot.py` | Telegram Bot webhook handler, inline buttons, approvals |
| `brazil_module/services/intelligence/channels/erp_chat.py` | ERP Chat backend — message handling |
| `brazil_module/services/intelligence/recurring/__init__.py` | Package init |
| `brazil_module/services/intelligence/recurring/expense_scheduler.py` | Daily check for due recurring expenses |
| `brazil_module/services/intelligence/recurring/follow_up_manager.py` | Overdue NF follow-up automation |
| `brazil_module/services/intelligence/prompts/__init__.py` | Package init |
| `brazil_module/services/intelligence/prompts/system_prompt.py` | Main system prompt builder |
| `brazil_module/services/intelligence/prompts/approval_formatter.py` | Formats approval messages for Telegram/ERP |

### New Files (DocTypes — Frappe Module)

Each DocType folder contains: `__init__.py`, `<name>.json`, `<name>.py`

| DocType Folder | Type |
|---|---|
| `brazil_module/intelligence/doctype/i8_agent_settings/` | Single (singleton config) |
| `brazil_module/intelligence/doctype/i8_telegram_user/` | Child Table |
| `brazil_module/intelligence/doctype/i8_decision_log/` | Submittable (immutable audit) |
| `brazil_module/intelligence/doctype/i8_cost_log/` | Regular |
| `brazil_module/intelligence/doctype/i8_conversation/` | Regular |
| `brazil_module/intelligence/doctype/i8_conversation_message/` | Child Table |
| `brazil_module/intelligence/doctype/i8_module_registry/` | Regular |
| `brazil_module/intelligence/doctype/i8_supplier_profile/` | Regular |
| `brazil_module/intelligence/doctype/i8_recurring_expense/` | Regular |
| `brazil_module/intelligence/doctype/i8_recurring_expense_item/` | Child Table |

### New Files (Frontend)

| File | Responsibility |
|---|---|
| `brazil_module/public/js/i8_chat_widget.js` | Floating chat widget + dedicated page |
| `brazil_module/intelligence/workspace/intelligence8.json` | Frappe workspace for Intelligence8 module |

### New Files (Tests)

| File | Tests For |
|---|---|
| `brazil_module/tests/test_circuit_breaker.py` | Circuit breaker state machine |
| `brazil_module/tests/test_decision_engine.py` | Confidence routing, threshold logic |
| `brazil_module/tests/test_action_executor.py` | Allowlist enforcement, sandboxing |
| `brazil_module/tests/test_cost_tracker.py` | Cost calculation, logging |
| `brazil_module/tests/test_context_builder.py` | Context assembly, sliding window |
| `brazil_module/tests/test_agent.py` | Agent Brain event processing, tool dispatch |
| `brazil_module/tests/test_channel_router.py` | Channel routing, conversation threading |
| `brazil_module/tests/test_telegram_bot.py` | Webhook validation, approval flow, auth |
| `brazil_module/tests/test_erp_chat.py` | Chat message handling |
| `brazil_module/tests/test_erp_tools.py` | Allowlist, CRUD operations |
| `brazil_module/tests/test_purchasing_tools.py` | PO creation, supplier notification |
| `brazil_module/tests/test_fiscal_tools.py` | NF processing wrappers |
| `brazil_module/tests/test_email_tools.py` | Email classification, search tools |
| `brazil_module/tests/test_banking_tools.py` | Payment, reconciliation wrappers |
| `brazil_module/tests/test_expense_scheduler.py` | Recurring expense scheduling |
| `brazil_module/tests/test_follow_up_manager.py` | Follow-up automation |
| `brazil_module/tests/test_system_prompt.py` | Prompt assembly |
| `brazil_module/tests/test_approval_formatter.py` | Approval message formatting |

### Modified Files

| File | Change |
|---|---|
| `brazil_module/modules.txt` | Add "Intelligence" line |
| `brazil_module/hooks.py` | Add I8 scheduled tasks, doc_events, JS overrides |
| `brazil_module/setup/install.py` | Add I8 custom fields, roles |
| `brazil_module/api/__init__.py` | Add I8 API endpoints (telegram webhook, chat, dashboard) |
| `pyproject.toml` | Add anthropic, python-telegram-bot dependencies |

---

## Task Dependency Graph

```
Task 1 (dependencies) ─── no deps
Task 2 (DocTypes) ─────── no deps
Task 3 (circuit breaker) ─ no deps
Task 4 (cost tracker) ──── depends on Task 2
Task 5 (decision engine) ─ depends on Task 2
Task 6 (action executor) ─ depends on Task 5
Task 7 (context builder) ─ depends on Task 2
Task 8 (tools) ──────────── depends on Task 6
Task 9 (prompts) ────────── depends on Task 8
Task 10 (agent brain) ───── depends on Tasks 3,4,5,6,7,8,9
Task 11 (channel router) ── depends on Task 2
Task 12 (telegram bot) ──── depends on Tasks 10,11
Task 13 (erp chat) ──────── depends on Tasks 10,11
Task 14 (recurring) ──────── depends on Tasks 8,10
Task 15 (follow-up) ──────── depends on Tasks 8,10,14
Task 16 (hooks + setup) ──── depends on Tasks 10,12,14,15
Task 17 (API endpoints) ──── depends on Tasks 10,12,13
Task 18 (chat widget JS) ── depends on Task 17
Task 19 (workspace) ──────── depends on Task 2
Task 20 (integration test) ── depends on all
```

Tasks 1, 2, 3 can be executed in parallel. Tasks 4, 5, 7 can be parallel after Task 2.

---

## Task 1: Project Setup and Dependencies

**Files:**
- Modify: `pyproject.toml`
- Modify: `brazil_module/modules.txt`
- Create: `brazil_module/services/intelligence/__init__.py`
- Create: `brazil_module/services/intelligence/tools/__init__.py`
- Create: `brazil_module/services/intelligence/channels/__init__.py`
- Create: `brazil_module/services/intelligence/recurring/__init__.py`
- Create: `brazil_module/services/intelligence/prompts/__init__.py`
- Create: `brazil_module/intelligence/__init__.py`

- [ ] **Step 1: Add new dependencies to pyproject.toml**

In `pyproject.toml`, add to the `dependencies` list:

```toml
dependencies = [
    "cryptography>=46.0.0",
    "requests>=2.32.0",
    "pypdf>=4.0.0",
    "qrcode[pil]>=7.4.0",
    "Pillow>=10.0.0",
    "anthropic>=0.52.0",
    "python-telegram-bot>=21.0",
]
```

- [ ] **Step 2: Add Intelligence module to modules.txt**

Append to `brazil_module/modules.txt`:

```
Fiscal
Bancos
Intelligence
```

- [ ] **Step 3: Create package __init__.py files**

Create all `__init__.py` files for the new packages (empty files):

```
brazil_module/services/intelligence/__init__.py
brazil_module/services/intelligence/tools/__init__.py
brazil_module/services/intelligence/channels/__init__.py
brazil_module/services/intelligence/recurring/__init__.py
brazil_module/services/intelligence/prompts/__init__.py
brazil_module/intelligence/__init__.py
```

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml brazil_module/modules.txt brazil_module/services/intelligence/ brazil_module/intelligence/
git commit -m "chore: add Intelligence8 module structure and dependencies"
```

---

## Task 2: DocType Definitions

**Files:**
- Create: All 10 DocType folders under `brazil_module/intelligence/doctype/`

This task creates all the Frappe DocType JSON definitions and Python controllers. Each DocType follows the exact pattern from the existing codebase (see `banco_inter_settings.json` for singleton, `inter_boleto.json` for regular).

### Sub-task 2a: I8 Telegram User (Child Table)

- [ ] **Step 1: Create DocType directory**

```bash
mkdir -p brazil_module/intelligence/doctype/i8_telegram_user
```

- [ ] **Step 2: Create i8_telegram_user.json**

Create `brazil_module/intelligence/doctype/i8_telegram_user/i8_telegram_user.json`:

```json
{
    "actions": [],
    "creation": "2026-03-25 10:00:00.000000",
    "doctype": "DocType",
    "engine": "InnoDB",
    "field_order": [
        "telegram_user_id",
        "user",
        "column_break_auth",
        "approval_limit",
        "active"
    ],
    "fields": [
        {
            "fieldname": "telegram_user_id",
            "fieldtype": "Data",
            "in_list_view": 1,
            "label": "Telegram User ID",
            "reqd": 1
        },
        {
            "fieldname": "user",
            "fieldtype": "Link",
            "in_list_view": 1,
            "label": "Frappe User",
            "options": "User",
            "reqd": 1
        },
        {
            "fieldname": "column_break_auth",
            "fieldtype": "Column Break"
        },
        {
            "default": "0",
            "description": "Max value this user can approve. 0 = unlimited.",
            "fieldname": "approval_limit",
            "fieldtype": "Currency",
            "in_list_view": 1,
            "label": "Approval Limit"
        },
        {
            "default": "1",
            "fieldname": "active",
            "fieldtype": "Check",
            "in_list_view": 1,
            "label": "Active"
        }
    ],
    "index_web_pages_for_search": 0,
    "istable": 1,
    "links": [],
    "modified": "2026-03-25 10:00:00.000000",
    "modified_by": "Administrator",
    "module": "Intelligence",
    "name": "I8 Telegram User",
    "owner": "Administrator",
    "permissions": [],
    "sort_field": "creation",
    "sort_order": "DESC",
    "states": [],
    "track_changes": 0
}
```

- [ ] **Step 3: Create __init__.py and .py controller**

Create `brazil_module/intelligence/doctype/i8_telegram_user/__init__.py` (empty).

Create `brazil_module/intelligence/doctype/i8_telegram_user/i8_telegram_user.py`:

```python
# Copyright (c) 2026, Brazil Module and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class I8TelegramUser(Document):
    pass
```

### Sub-task 2b: I8 Agent Settings (Single)

- [ ] **Step 4: Create DocType directory**

```bash
mkdir -p brazil_module/intelligence/doctype/i8_agent_settings
```

- [ ] **Step 5: Create i8_agent_settings.json**

Create `brazil_module/intelligence/doctype/i8_agent_settings/i8_agent_settings.json`:

```json
{
    "actions": [],
    "creation": "2026-03-25 10:00:00.000000",
    "doctype": "DocType",
    "engine": "InnoDB",
    "field_order": [
        "general_section",
        "enabled",
        "default_confidence_threshold",
        "column_break_general",
        "high_value_confirmation_pin",
        "high_value_threshold",
        "ai_models_section",
        "anthropic_api_key",
        "enable_prompt_caching",
        "max_requests_per_minute",
        "column_break_models",
        "haiku_model",
        "sonnet_model",
        "opus_model",
        "timeout_section",
        "haiku_timeout_seconds",
        "sonnet_timeout_seconds",
        "opus_timeout_seconds",
        "telegram_section",
        "telegram_bot_token",
        "telegram_webhook_secret",
        "column_break_telegram",
        "telegram_chat_id",
        "telegram_users",
        "budget_section",
        "daily_budget_usd",
        "monthly_budget_usd",
        "column_break_budget",
        "pause_on_budget_exceeded",
        "cost_anomaly_threshold",
        "operator_hourly_cost",
        "briefing_section",
        "briefing_enabled",
        "briefing_time",
        "degradation_section",
        "circuit_breaker_threshold",
        "circuit_breaker_recovery_seconds"
    ],
    "fields": [
        {
            "fieldname": "general_section",
            "fieldtype": "Section Break",
            "label": "General"
        },
        {
            "default": "0",
            "fieldname": "enabled",
            "fieldtype": "Check",
            "label": "Enabled"
        },
        {
            "default": "0.85",
            "fieldname": "default_confidence_threshold",
            "fieldtype": "Float",
            "label": "Default Confidence Threshold",
            "description": "Decisions below this threshold require human approval (0.0-1.0)"
        },
        {
            "fieldname": "column_break_general",
            "fieldtype": "Column Break"
        },
        {
            "default": "0",
            "fieldname": "high_value_confirmation_pin",
            "fieldtype": "Check",
            "label": "Require PIN for High Value Approvals"
        },
        {
            "default": "10000",
            "fieldname": "high_value_threshold",
            "fieldtype": "Currency",
            "label": "High Value Threshold",
            "depends_on": "high_value_confirmation_pin"
        },
        {
            "fieldname": "ai_models_section",
            "fieldtype": "Section Break",
            "label": "AI Models"
        },
        {
            "fieldname": "anthropic_api_key",
            "fieldtype": "Password",
            "label": "Anthropic API Key",
            "description": "Fallback. Prefer environment variable ANTHROPIC_API_KEY."
        },
        {
            "default": "1",
            "fieldname": "enable_prompt_caching",
            "fieldtype": "Check",
            "label": "Enable Prompt Caching"
        },
        {
            "default": "60",
            "fieldname": "max_requests_per_minute",
            "fieldtype": "Int",
            "label": "Max Requests per Minute"
        },
        {
            "fieldname": "column_break_models",
            "fieldtype": "Column Break"
        },
        {
            "default": "claude-haiku-4-5-20251001",
            "fieldname": "haiku_model",
            "fieldtype": "Data",
            "label": "Haiku Model ID"
        },
        {
            "default": "claude-sonnet-4-6",
            "fieldname": "sonnet_model",
            "fieldtype": "Data",
            "label": "Sonnet Model ID"
        },
        {
            "default": "claude-opus-4-6",
            "fieldname": "opus_model",
            "fieldtype": "Data",
            "label": "Opus Model ID"
        },
        {
            "fieldname": "timeout_section",
            "fieldtype": "Section Break",
            "label": "Timeouts",
            "collapsible": 1
        },
        {
            "default": "30",
            "fieldname": "haiku_timeout_seconds",
            "fieldtype": "Int",
            "label": "Haiku Timeout (seconds)"
        },
        {
            "default": "60",
            "fieldname": "sonnet_timeout_seconds",
            "fieldtype": "Int",
            "label": "Sonnet Timeout (seconds)"
        },
        {
            "default": "120",
            "fieldname": "opus_timeout_seconds",
            "fieldtype": "Int",
            "label": "Opus Timeout (seconds)"
        },
        {
            "fieldname": "telegram_section",
            "fieldtype": "Section Break",
            "label": "Telegram"
        },
        {
            "fieldname": "telegram_bot_token",
            "fieldtype": "Password",
            "label": "Bot Token",
            "description": "Fallback. Prefer environment variable I8_TELEGRAM_BOT_TOKEN."
        },
        {
            "fieldname": "telegram_webhook_secret",
            "fieldtype": "Password",
            "label": "Webhook Secret",
            "description": "Set when registering webhook. Validates X-Telegram-Bot-Api-Secret-Token header."
        },
        {
            "fieldname": "column_break_telegram",
            "fieldtype": "Column Break"
        },
        {
            "fieldname": "telegram_chat_id",
            "fieldtype": "Data",
            "label": "Primary Chat ID",
            "description": "Telegram chat ID for notifications"
        },
        {
            "fieldname": "telegram_users",
            "fieldtype": "Table",
            "label": "Authorized Telegram Users",
            "options": "I8 Telegram User"
        },
        {
            "fieldname": "budget_section",
            "fieldtype": "Section Break",
            "label": "Budget & Alerts"
        },
        {
            "default": "10",
            "fieldname": "daily_budget_usd",
            "fieldtype": "Currency",
            "label": "Daily Budget (USD)"
        },
        {
            "default": "200",
            "fieldname": "monthly_budget_usd",
            "fieldtype": "Currency",
            "label": "Monthly Budget (USD)"
        },
        {
            "fieldname": "column_break_budget",
            "fieldtype": "Column Break"
        },
        {
            "default": "0",
            "fieldname": "pause_on_budget_exceeded",
            "fieldtype": "Check",
            "label": "Pause Agent on Budget Exceeded"
        },
        {
            "default": "2.0",
            "fieldname": "cost_anomaly_threshold",
            "fieldtype": "Float",
            "label": "Cost Anomaly Threshold",
            "description": "Alert if daily cost exceeds this multiple of the average"
        },
        {
            "default": "50",
            "fieldname": "operator_hourly_cost",
            "fieldtype": "Currency",
            "label": "Operator Hourly Cost (BRL)",
            "description": "Used to calculate estimated savings vs human operator"
        },
        {
            "fieldname": "briefing_section",
            "fieldtype": "Section Break",
            "label": "Daily Briefing",
            "collapsible": 1
        },
        {
            "default": "0",
            "fieldname": "briefing_enabled",
            "fieldtype": "Check",
            "label": "Enable Daily Briefing"
        },
        {
            "default": "08:00",
            "fieldname": "briefing_time",
            "fieldtype": "Time",
            "label": "Briefing Time",
            "depends_on": "briefing_enabled"
        },
        {
            "fieldname": "degradation_section",
            "fieldtype": "Section Break",
            "label": "Graceful Degradation",
            "collapsible": 1
        },
        {
            "default": "5",
            "fieldname": "circuit_breaker_threshold",
            "fieldtype": "Int",
            "label": "Circuit Breaker Threshold",
            "description": "Consecutive API failures before pausing"
        },
        {
            "default": "300",
            "fieldname": "circuit_breaker_recovery_seconds",
            "fieldtype": "Int",
            "label": "Recovery Timeout (seconds)",
            "description": "Seconds to wait before retrying after circuit opens"
        }
    ],
    "index_web_pages_for_search": 0,
    "issingle": 1,
    "links": [],
    "modified": "2026-03-25 10:00:00.000000",
    "modified_by": "Administrator",
    "module": "Intelligence",
    "name": "I8 Agent Settings",
    "owner": "Administrator",
    "permissions": [
        {
            "create": 1,
            "delete": 1,
            "email": 1,
            "print": 1,
            "read": 1,
            "role": "System Manager",
            "share": 1,
            "write": 1
        },
        {
            "create": 1,
            "delete": 1,
            "email": 1,
            "print": 1,
            "read": 1,
            "role": "Intelligence8 Admin",
            "share": 1,
            "write": 1
        },
        {
            "read": 1,
            "role": "Intelligence8 Viewer"
        }
    ],
    "sort_field": "creation",
    "sort_order": "DESC",
    "states": [],
    "track_changes": 1
}
```

- [ ] **Step 6: Create i8_agent_settings.py controller**

Create `brazil_module/intelligence/doctype/i8_agent_settings/__init__.py` (empty).

Create `brazil_module/intelligence/doctype/i8_agent_settings/i8_agent_settings.py`:

```python
import os

import frappe
from frappe.model.document import Document


class I8AgentSettings(Document):
    def validate(self):
        if self.default_confidence_threshold < 0 or self.default_confidence_threshold > 1:
            frappe.throw("Confidence threshold must be between 0.0 and 1.0")
        if self.max_requests_per_minute and self.max_requests_per_minute < 1:
            frappe.throw("Max requests per minute must be at least 1")

    @staticmethod
    def get_settings():
        return frappe.get_single("I8 Agent Settings")

    @staticmethod
    def is_enabled():
        return bool(frappe.db.get_single_value("I8 Agent Settings", "enabled"))

    @staticmethod
    def get_api_key():
        """Get API key from env var first, then from settings."""
        key = os.environ.get("ANTHROPIC_API_KEY")
        if key:
            return key
        return frappe.get_single("I8 Agent Settings").get_password("anthropic_api_key")

    @staticmethod
    def get_telegram_token():
        """Get Telegram token from env var first, then from settings."""
        token = os.environ.get("I8_TELEGRAM_BOT_TOKEN")
        if token:
            return token
        return frappe.get_single("I8 Agent Settings").get_password("telegram_bot_token")
```

### Sub-task 2c: I8 Decision Log (Submittable)

- [ ] **Step 7: Create i8_decision_log DocType**

Create directory and files for `brazil_module/intelligence/doctype/i8_decision_log/`.

`i8_decision_log.json` — key attributes:
- `is_submittable`: 1
- `track_changes`: 1
- Fields: timestamp, event_type, module, action, actor (Select: Agent/Human), channel (Select: telegram/erp_chat/system/email), confidence_score, model_used, input_summary, reasoning, result (Select: Success/Failed/Rejected/Pending), related_doctype, related_docname (Dynamic Link), cost_usd, human_override, human_feedback
- Permissions: System Manager (full), Intelligence8 Admin (full), Intelligence8 Viewer (read)

`i8_decision_log.py`:
```python
import frappe
from frappe.model.document import Document


class I8DecisionLog(Document):
    def before_save(self):
        if not self.is_new() and self.docstatus == 1:
            frappe.throw("Submitted Decision Log entries are immutable and cannot be modified.")

    def on_trash(self):
        frappe.throw("Decision Log entries cannot be deleted.")

    def resolve(self, actor: str, result: str, channel: str = "system",
                human_override: bool = False, human_feedback: str | None = None):
        """Resolve a Pending decision log. Only works on draft (docstatus=0) entries."""
        if self.docstatus != 0:
            frappe.throw("Only draft (Pending) decision logs can be resolved.")
        self.actor = actor
        self.result = result
        self.channel = channel
        self.human_override = human_override
        self.human_feedback = human_feedback
        self.save(ignore_permissions=True)
        self.submit()  # Now immutable
```

### Sub-task 2d: I8 Cost Log

- [ ] **Step 8: Create i8_cost_log DocType**

Create directory and files for `brazil_module/intelligence/doctype/i8_cost_log/`.

`i8_cost_log.json` — Regular DocType with fields: timestamp, module, function_name, model, tokens_in, tokens_out, cost_usd, latency_ms, cache_hit, decision_log (Link), company (Link), department (Link).

`i8_cost_log.py` — Minimal controller (just `pass`).

### Sub-task 2e: I8 Conversation Message (Child Table)

- [ ] **Step 9: Create i8_conversation_message DocType**

`i8_conversation_message.json` — `istable: 1`. Fields: channel (Select), direction (Select), actor (Select), content (Long Text), timestamp (Datetime), related_doctype (Link), related_docname (Dynamic Link), telegram_message_id (Data).

### Sub-task 2f: I8 Conversation

- [ ] **Step 10: Create i8_conversation DocType**

`i8_conversation.json` — Regular. Fields: subject, status (Select: Active/Resolved/Archived), related_doctype, related_docname, messages (Table -> I8 Conversation Message).

### Sub-task 2g: I8 Module Registry

- [ ] **Step 11: Create i8_module_registry DocType**

`i8_module_registry.json` — Regular. Fields: module_name, description, tools_definition (Code/JSON), context_prompt (Code/text), enabled (Check), default_model (Select: haiku/sonnet/opus).

### Sub-task 2h: I8 Supplier Profile

- [ ] **Step 12: Create i8_supplier_profile DocType**

`i8_supplier_profile.json` — Regular. Fields: supplier (Link/Supplier), contact_email, communication_channel (Select: email), email_template (Link/Email Template), expected_nf_days, follow_up_after_days, max_follow_ups, follow_up_interval_days, auto_pay (Check), payment_method (Select: PIX/TED/Boleto), notes (Long Text), reliability_score (Float, read_only).

### Sub-task 2i: I8 Recurring Expense Item (Child Table)

- [ ] **Step 13: Create i8_recurring_expense_item DocType**

`i8_recurring_expense_item.json` — `istable: 1`. Fields: item_code (Link/Item), qty, rate, expense_account (Link/Account), cost_center (Link/Cost Center).

### Sub-task 2j: I8 Recurring Expense

- [ ] **Step 14: Create i8_recurring_expense DocType**

`i8_recurring_expense.json` — Regular. Fields: title, supplier (Link), document_type (Select: Purchase Order/Journal Entry), estimated_amount, currency (Link/Currency), frequency (Select: Monthly/Weekly/Quarterly/Yearly), day_of_month, lead_days, notify_supplier (Check), follow_up_after_days, max_follow_ups, auto_pay (Check), payment_method (Select), amount_tolerance_percent, confidence_threshold, cost_center (Link), department (Link), active (Check), items (Table -> I8 Recurring Expense Item), last_created (Date, read_only), next_due (Date, read_only).

Controller calculates `next_due` on validate.

- [ ] **Step 15: Commit all DocTypes**

```bash
git add brazil_module/intelligence/
git commit -m "feat: add Intelligence8 DocType definitions (10 DocTypes)"
```

---

## Task 3: Circuit Breaker

**Files:**
- Create: `brazil_module/services/intelligence/circuit_breaker.py`
- Test: `brazil_module/tests/test_circuit_breaker.py`

- [ ] **Step 1: Write failing tests**

Create `brazil_module/tests/test_circuit_breaker.py`:

```python
import sys
import time
from unittest.mock import MagicMock

if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    _fm = MagicMock()
    _fm._ = lambda x: x
    sys.modules["frappe"] = _fm
    sys.modules["frappe.utils"] = _fm.utils

import unittest

from brazil_module.services.intelligence.circuit_breaker import CircuitBreaker


class TestCircuitBreaker(unittest.TestCase):
    def test_starts_closed(self):
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=1)
        self.assertEqual(cb.state, "closed")

    def test_opens_after_threshold_failures(self):
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=1)
        cb.record_failure()
        cb.record_failure()
        self.assertEqual(cb.state, "closed")
        cb.record_failure()
        self.assertEqual(cb.state, "open")

    def test_rejects_when_open(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=60)
        cb.record_failure()
        self.assertFalse(cb.allow_request())

    def test_allows_when_closed(self):
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=1)
        self.assertTrue(cb.allow_request())

    def test_transitions_to_half_open_after_timeout(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.1)
        cb.record_failure()
        self.assertEqual(cb.state, "open")
        time.sleep(0.15)
        self.assertTrue(cb.allow_request())
        self.assertEqual(cb.state, "half_open")

    def test_closes_after_success_in_half_open(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.1)
        cb.record_failure()
        time.sleep(0.15)
        cb.allow_request()  # transitions to half_open
        cb.record_success()
        self.assertEqual(cb.state, "closed")

    def test_reopens_on_failure_in_half_open(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.1)
        cb.record_failure()
        time.sleep(0.15)
        cb.allow_request()  # half_open
        cb.record_failure()
        self.assertEqual(cb.state, "open")

    def test_reset_clears_state(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=60)
        cb.record_failure()
        self.assertEqual(cb.state, "open")
        cb.reset()
        self.assertEqual(cb.state, "closed")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest brazil_module/tests/test_circuit_breaker.py -v`
Expected: FAIL (ImportError — module doesn't exist)

- [ ] **Step 3: Implement circuit_breaker.py**

Create `brazil_module/services/intelligence/circuit_breaker.py`:

```python
import time


class CircuitBreaker:
    """Circuit breaker for Claude API resilience.

    States: closed -> open (after N failures) -> half_open (after timeout) -> closed (on success)
    """

    def __init__(self, failure_threshold: int = 5, recovery_timeout: float = 300.0,
                 half_open_max_calls: int = 2):
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._half_open_max_calls = half_open_max_calls
        self._failure_count = 0
        self._half_open_calls = 0
        self._last_failure_time = 0.0
        self._state = "closed"

    @property
    def state(self) -> str:
        return self._state

    def allow_request(self) -> bool:
        if self._state == "closed":
            return True
        if self._state == "open":
            if time.monotonic() - self._last_failure_time >= self._recovery_timeout:
                self._state = "half_open"
                return True
            return False
        # half_open — allow limited test requests
        if self._half_open_calls < self._half_open_max_calls:
            self._half_open_calls += 1
            return True
        return False

    def record_success(self) -> None:
        self._failure_count = 0
        self._half_open_calls = 0
        self._state = "closed"

    def record_failure(self) -> None:
        self._failure_count += 1
        self._last_failure_time = time.monotonic()
        if self._state == "half_open" or self._failure_count >= self._failure_threshold:
            self._state = "open"

    def reset(self) -> None:
        self._failure_count = 0
        self._half_open_calls = 0
        self._last_failure_time = 0.0
        self._state = "closed"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest brazil_module/tests/test_circuit_breaker.py -v`
Expected: All 8 tests PASS

- [ ] **Step 5: Commit**

```bash
git add brazil_module/services/intelligence/circuit_breaker.py brazil_module/tests/test_circuit_breaker.py
git commit -m "feat: add circuit breaker for Claude API resilience"
```

---

## Task 4: Cost Tracker

**Files:**
- Create: `brazil_module/services/intelligence/cost_tracker.py`
- Test: `brazil_module/tests/test_cost_tracker.py`

**Depends on:** Task 2 (I8 Cost Log DocType)

- [ ] **Step 1: Write failing tests**

Create `brazil_module/tests/test_cost_tracker.py`:

```python
import sys
from unittest.mock import MagicMock, patch, call

if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    _fm = MagicMock()
    _fm._ = lambda x: x
    sys.modules["frappe"] = _fm
    sys.modules["frappe.utils"] = _fm.utils

frappe = sys.modules["frappe"]

import unittest

from brazil_module.services.intelligence.cost_tracker import CostTracker, calculate_cost_usd


class TestCalculateCost(unittest.TestCase):
    def test_haiku_cost(self):
        # Haiku: $0.80/MTok input, $4/MTok output
        cost = calculate_cost_usd("claude-haiku-4-5-20251001", tokens_in=1000, tokens_out=500)
        expected = (1000 * 0.80 / 1_000_000) + (500 * 4.0 / 1_000_000)
        self.assertAlmostEqual(cost, expected, places=6)

    def test_sonnet_cost(self):
        # Sonnet: $3/MTok input, $15/MTok output
        cost = calculate_cost_usd("claude-sonnet-4-6", tokens_in=1000, tokens_out=500)
        expected = (1000 * 3.0 / 1_000_000) + (500 * 15.0 / 1_000_000)
        self.assertAlmostEqual(cost, expected, places=6)

    def test_opus_cost(self):
        # Opus: $15/MTok input, $75/MTok output
        cost = calculate_cost_usd("claude-opus-4-6", tokens_in=1000, tokens_out=500)
        expected = (1000 * 15.0 / 1_000_000) + (500 * 75.0 / 1_000_000)
        self.assertAlmostEqual(cost, expected, places=6)

    def test_unknown_model_uses_sonnet_rates(self):
        cost = calculate_cost_usd("unknown-model", tokens_in=1000, tokens_out=500)
        expected = (1000 * 3.0 / 1_000_000) + (500 * 15.0 / 1_000_000)
        self.assertAlmostEqual(cost, expected, places=6)

    def test_cached_input_discount(self):
        # Cached: 90% discount on input
        cost = calculate_cost_usd("claude-sonnet-4-6", tokens_in=1000, tokens_out=500, cache_hit=True)
        expected = (1000 * 0.30 / 1_000_000) + (500 * 15.0 / 1_000_000)
        self.assertAlmostEqual(cost, expected, places=6)


class TestCostTrackerLog(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()
        frappe.get_doc.return_value = MagicMock()
        frappe.new_doc.return_value = MagicMock()

    def test_log_creates_cost_log_entry(self):
        tracker = CostTracker()
        tracker.log(
            model="claude-haiku-4-5-20251001",
            tokens_in=500,
            tokens_out=100,
            latency_ms=230,
            module="p2p",
            function_name="create_po",
            cache_hit=False,
        )
        frappe.new_doc.assert_called_once_with("I8 Cost Log")


class TestBudgetCheck(unittest.TestCase):
    def setUp(self):
        frappe.reset_mock()
        frappe.db.sql.return_value = [[5.0]]

    def test_within_budget(self):
        tracker = CostTracker()
        self.assertTrue(tracker.check_daily_budget(limit_usd=10.0))

    def test_exceeds_budget(self):
        tracker = CostTracker()
        self.assertFalse(tracker.check_daily_budget(limit_usd=3.0))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest brazil_module/tests/test_cost_tracker.py -v`
Expected: FAIL (ImportError)

- [ ] **Step 3: Implement cost_tracker.py**

Create `brazil_module/services/intelligence/cost_tracker.py`:

```python
from datetime import datetime

import frappe

# Pricing per million tokens (as of 2026-03)
MODEL_PRICING = {
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.0},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
    "claude-opus-4-6": {"input": 15.0, "output": 75.0},
}

DEFAULT_PRICING = MODEL_PRICING["claude-sonnet-4-6"]

CACHE_INPUT_DISCOUNT = 0.10  # cached input is 10% of original price


def calculate_cost_usd(
    model: str,
    tokens_in: int,
    tokens_out: int,
    cache_hit: bool = False,
) -> float:
    pricing = MODEL_PRICING.get(model, DEFAULT_PRICING)
    input_rate = pricing["input"]
    if cache_hit:
        input_rate = input_rate * CACHE_INPUT_DISCOUNT
    return (tokens_in * input_rate / 1_000_000) + (tokens_out * pricing["output"] / 1_000_000)


class CostTracker:
    def log(
        self,
        model: str,
        tokens_in: int,
        tokens_out: int,
        latency_ms: int,
        module: str,
        function_name: str,
        cache_hit: bool = False,
        decision_log: str | None = None,
        company: str | None = None,
        department: str | None = None,
    ) -> str:
        cost = calculate_cost_usd(model, tokens_in, tokens_out, cache_hit)
        doc = frappe.new_doc("I8 Cost Log")
        doc.timestamp = datetime.now()
        doc.module = module
        doc.function_name = function_name
        doc.model = model
        doc.tokens_in = tokens_in
        doc.tokens_out = tokens_out
        doc.cost_usd = cost
        doc.latency_ms = latency_ms
        doc.cache_hit = cache_hit
        doc.decision_log = decision_log
        doc.company = company
        doc.department = department
        doc.insert(ignore_permissions=True)
        return doc.name

    def check_daily_budget(self, limit_usd: float) -> bool:
        today = datetime.now().date()
        result = frappe.db.sql(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM `tabI8 Cost Log` WHERE DATE(timestamp) = %s",
            (today,),
        )
        total = float(result[0][0]) if result else 0.0
        return total < limit_usd

    def get_daily_total(self) -> float:
        today = datetime.now().date()
        result = frappe.db.sql(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM `tabI8 Cost Log` WHERE DATE(timestamp) = %s",
            (today,),
        )
        return float(result[0][0]) if result else 0.0
```

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest brazil_module/tests/test_cost_tracker.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add brazil_module/services/intelligence/cost_tracker.py brazil_module/tests/test_cost_tracker.py
git commit -m "feat: add LLM cost tracker with model pricing and budget checks"
```

---

## Task 5: Decision Engine

**Files:**
- Create: `brazil_module/services/intelligence/decision_engine.py`
- Test: `brazil_module/tests/test_decision_engine.py`

**Depends on:** Task 2 (I8 Decision Log, I8 Agent Settings)

- [ ] **Step 1: Write failing tests**

Create `brazil_module/tests/test_decision_engine.py` with tests for:
- `should_auto_approve` returns True when confidence >= threshold
- `should_auto_approve` returns False when confidence < threshold
- `should_auto_approve` always returns False for submit/cancel operations regardless of confidence
- `log_decision` creates immutable Decision Log entry
- `get_threshold_for_action` uses custom threshold from recurring expense when available
- Approval limit check: human can only approve up to their limit

- [ ] **Step 2: Run tests — expect FAIL**

- [ ] **Step 3: Implement decision_engine.py**

Core logic:
```python
class DecisionEngine:
    def __init__(self, settings=None):
        self._settings = settings or frappe.get_single("I8 Agent Settings")

    def evaluate(self, action: str, doctype: str, confidence: float,
                 amount: float = 0, custom_threshold: float | None = None) -> dict:
        threshold = custom_threshold or self._settings.default_confidence_threshold
        requires_human = (
            confidence < threshold
            or action in ("submit", "cancel")
            or (self._settings.high_value_confirmation_pin and amount > self._settings.high_value_threshold)
        )
        return {"auto_approve": not requires_human, "confidence": confidence, "threshold": threshold}

    def log_decision(self, event_type, module, action, actor, channel,
                     confidence, model, input_summary, reasoning,
                     result, related_doctype=None, related_docname=None,
                     cost_usd=0, human_override=False, human_feedback=None) -> str:
        doc = frappe.new_doc("I8 Decision Log")
        # ... set all fields (timestamp, event_type, module, action, actor, etc.)
        doc.insert(ignore_permissions=True)
        # Only submit non-Pending decisions (Pending stays draft for Telegram approval flow)
        if result != "Pending":
            doc.submit()
        return doc.name
```

- [ ] **Step 4: Run tests — expect PASS**
- [ ] **Step 5: Commit**

```bash
git add brazil_module/services/intelligence/decision_engine.py brazil_module/tests/test_decision_engine.py
git commit -m "feat: add confidence-based decision engine with audit logging"
```

---

## Task 6: Action Executor (Sandboxed)

**Files:**
- Create: `brazil_module/services/intelligence/action_executor.py`
- Test: `brazil_module/tests/test_action_executor.py`

**Depends on:** Task 5

- [ ] **Step 1: Write failing tests**

Tests for:
- Allowlisted operation executes successfully
- Non-allowlisted DocType raises PermissionError
- Non-allowlisted operation on allowed DocType raises PermissionError
- `delete` always blocked on any DocType
- Every action logs to Decision Ledger via DecisionEngine
- `create` returns the new document name
- `submit` calls `doc.submit()`

- [ ] **Step 2: Run tests — expect FAIL**

- [ ] **Step 3: Implement action_executor.py**

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
}


class ActionExecutor:
    def execute(self, doctype: str, operation: str, data: dict | None = None) -> dict:
        if operation == "delete":
            raise PermissionError("Agent is never allowed to delete documents")
        allowed_ops = ACTION_ALLOWLIST.get(doctype)
        if allowed_ops is None:
            raise PermissionError(f"Agent has no access to DocType: {doctype}")
        if operation not in allowed_ops:
            raise PermissionError(f"Operation '{operation}' not allowed on {doctype}")
        # dispatch to operation handler
        return getattr(self, f"_do_{operation}")(doctype, data or {})

    def _do_create(self, doctype, data):
        doc = frappe.new_doc(doctype)
        doc.update(data)
        doc.insert(ignore_permissions=True)
        return {"name": doc.name, "doctype": doctype}

    def _do_read(self, doctype, data):
        return frappe.get_doc(doctype, data.get("name")).as_dict()

    def _do_submit(self, doctype, data):
        doc = frappe.get_doc(doctype, data.get("name"))
        doc.submit()
        return {"name": doc.name, "status": "Submitted"}

    def _do_cancel(self, doctype, data):
        doc = frappe.get_doc(doctype, data.get("name"))
        doc.cancel()
        return {"name": doc.name, "status": "Cancelled"}

    def _do_update(self, doctype, data):
        name = data.get("name")
        if not name:
            raise ValueError("'name' is required for update operation")
        fields = {k: v for k, v in data.items() if k != "name"}
        doc = frappe.get_doc(doctype, name)
        doc.update(fields)
        doc.save(ignore_permissions=True)
        return {"name": doc.name}

    # Allowlist of fields the agent can update per DocType
    STATUS_FIELD_ALLOWLIST = {
        "Nota Fiscal": ["processing_status", "supplier_status", "item_creation_status", "po_status", "invoice_status"],
    }

    def _do_update_status(self, doctype, data):
        field = data.get("field")
        allowed_fields = self.STATUS_FIELD_ALLOWLIST.get(doctype, [])
        if field not in allowed_fields:
            raise PermissionError(f"Agent cannot update field '{field}' on {doctype}. Allowed: {allowed_fields}")
        frappe.db.set_value(doctype, data["name"], field, data["value"])
        return {"name": data["name"]}

    def _do_reconcile(self, doctype, data):
        from brazil_module.services.banking.reconciliation import batch_reconcile
        return batch_reconcile(data["bank_account"])
```

- [ ] **Step 4: Run tests — expect PASS**
- [ ] **Step 5: Commit**

```bash
git add brazil_module/services/intelligence/action_executor.py brazil_module/tests/test_action_executor.py
git commit -m "feat: add sandboxed action executor with DocType allowlist"
```

---

## Task 7: Context Builder

**Files:**
- Create: `brazil_module/services/intelligence/context_builder.py`
- Test: `brazil_module/tests/test_context_builder.py`

**Depends on:** Task 2

- [ ] **Step 1: Write failing tests**

Tests for:
- Builds context for recurring expense event (includes supplier profile, expense config, recent POs)
- Builds context for NF arrival event (includes NF data, matching POs, supplier info)
- Conversation context uses sliding window (last 20 messages + summary)
- Returns structured dict with `system_context`, `module_context`, `history`

- [ ] **Step 2: Run tests — expect FAIL**

- [ ] **Step 3: Implement context_builder.py**

```python
class ContextBuilder:
    def build(self, event_type: str, event_data: dict) -> dict:
        context = {
            "system_context": self._get_system_context(),
            "module_context": self._get_module_context(event_type),
            "event_data": event_data,
            "history": [],
        }
        if "conversation_name" in event_data:
            context["history"] = self._get_conversation_context(event_data["conversation_name"])
        if "supplier" in event_data:
            context["supplier_profile"] = self._get_supplier_profile(event_data["supplier"])
        return context

    def _get_system_context(self) -> str:
        # Returns date, company info, active modules
        ...

    def _get_module_context(self, event_type: str) -> str:
        # Loads context_prompt from I8 Module Registry for the relevant module
        ...

    def _get_conversation_context(self, conversation_name: str) -> list:
        # Sliding window: last 20 messages in full
        ...

    def _get_supplier_profile(self, supplier: str) -> dict | None:
        # Load I8 Supplier Profile for this supplier
        ...
```

- [ ] **Step 4: Run tests — expect PASS**
- [ ] **Step 5: Commit**

```bash
git add brazil_module/services/intelligence/context_builder.py brazil_module/tests/test_context_builder.py
git commit -m "feat: add context builder with sliding window conversation history"
```

---

## Task 8: Tools — ERP, Purchasing, Fiscal, Banking, Communication

**Files:**
- Create: `brazil_module/services/intelligence/tools/erp_tools.py`
- Create: `brazil_module/services/intelligence/tools/purchasing_tools.py`
- Create: `brazil_module/services/intelligence/tools/fiscal_tools.py`
- Create: `brazil_module/services/intelligence/tools/banking_tools.py`
- Create: `brazil_module/services/intelligence/tools/communication_tools.py`
- Test: `brazil_module/tests/test_erp_tools.py`
- Test: `brazil_module/tests/test_purchasing_tools.py`
- Test: `brazil_module/tests/test_fiscal_tools.py`
- Test: `brazil_module/tests/test_banking_tools.py`

**Depends on:** Task 6 (ActionExecutor)

Each tool file exports two things:
1. `TOOL_SCHEMAS` — list of Claude API tool definitions (JSON-serializable dicts)
2. Handler functions that the agent calls

- [ ] **Step 1: Write failing tests for erp_tools**

Test that `get_tool_schemas()` returns valid Claude API tool format, and that `execute_tool("erp.read_document", {...})` calls ActionExecutor correctly.

- [ ] **Step 2: Implement erp_tools.py**

```python
TOOL_SCHEMAS = [
    {
        "name": "erp.read_document",
        "description": "Read a document from ERPNext by doctype and name",
        "input_schema": {
            "type": "object",
            "properties": {
                "doctype": {"type": "string", "description": "The DocType name"},
                "name": {"type": "string", "description": "The document name/ID"},
            },
            "required": ["doctype", "name"],
        },
    },
    {
        "name": "erp.list_documents",
        "description": "List documents from ERPNext with filters",
        "input_schema": {
            "type": "object",
            "properties": {
                "doctype": {"type": "string"},
                "filters": {"type": "object"},
                "fields": {"type": "array", "items": {"type": "string"}},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["doctype"],
        },
    },
]

def execute_tool(tool_name: str, args: dict, executor) -> dict:
    if tool_name == "erp.read_document":
        return executor.execute(args["doctype"], "read", {"name": args["name"]})
    elif tool_name == "erp.list_documents":
        return {"data": frappe.get_all(args["doctype"], filters=args.get("filters"), fields=args.get("fields", ["name"]), limit_page_length=args.get("limit", 20))}
    raise ValueError(f"Unknown tool: {tool_name}")
```

- [ ] **Step 3: Write and implement purchasing_tools.py**

Tools: `p2p.create_purchase_order`, `p2p.send_po_to_supplier`, `p2p.check_pending_nf`, `p2p.list_due_invoices`

- [ ] **Step 4: Write and implement fiscal_tools.py**

Tools: `fiscal.process_nota_fiscal`, `fiscal.link_nf_to_po`, `fiscal.create_purchase_invoice`, `fiscal.find_matching_pos`

These wrap existing service functions: `processor.process_new_nf`, `invoice_creator`, `po_matcher`.

- [ ] **Step 5: Write and implement banking_tools.py**

Tools: `banking.create_payment`, `banking.execute_payment`, `banking.check_payment_status`, `banking.reconcile_transactions`, `banking.get_balance`

These wrap existing services: `payment_service`, `reconciliation`, `statement_sync`.

- [ ] **Step 6: Write and implement email_tools.py**

Tools: `email.classify`, `email.search`, `email.get_content`

`email.classify` takes subject, sender, body snippet and returns classification category. `email.search` queries Communication doctype with filters. `email.get_content` reads full email content.

- [ ] **Step 7: Write and implement communication_tools.py**

Tools: `comm.send_email`, `comm.send_notification`

- [ ] **Step 8: Implement tools/__init__.py registry**

```python
from brazil_module.services.intelligence.tools import (
    erp_tools, purchasing_tools, fiscal_tools, banking_tools, email_tools, communication_tools
)

ALL_TOOL_MODULES = [erp_tools, purchasing_tools, fiscal_tools, banking_tools, email_tools, communication_tools]

def get_all_tool_schemas() -> list:
    schemas = []
    for mod in ALL_TOOL_MODULES:
        schemas.extend(mod.TOOL_SCHEMAS)
    return schemas

def execute_tool(tool_name: str, args: dict, executor) -> dict:
    prefix = tool_name.split(".")[0]
    module_map = {"erp": erp_tools, "p2p": purchasing_tools, "fiscal": fiscal_tools, "banking": banking_tools, "email": email_tools, "comm": communication_tools}
    mod = module_map.get(prefix)
    if not mod:
        raise ValueError(f"Unknown tool prefix: {prefix}")
    return mod.execute_tool(tool_name, args, executor)
```

- [ ] **Step 9: Run all tool tests**

Run: `python3 -m pytest brazil_module/tests/test_erp_tools.py brazil_module/tests/test_purchasing_tools.py brazil_module/tests/test_fiscal_tools.py brazil_module/tests/test_banking_tools.py brazil_module/tests/test_email_tools.py -v`

- [ ] **Step 10: Commit**

```bash
git add brazil_module/services/intelligence/tools/ brazil_module/tests/test_*_tools.py
git commit -m "feat: add Intelligence8 tool definitions for P2P, fiscal, banking, and ERP"
```

---

## Task 9: Prompts

**Files:**
- Create: `brazil_module/services/intelligence/prompts/system_prompt.py`
- Create: `brazil_module/services/intelligence/prompts/approval_formatter.py`
- Test: `brazil_module/tests/test_system_prompt.py`
- Test: `brazil_module/tests/test_approval_formatter.py`

**Depends on:** Task 8

- [ ] **Step 1: Write failing tests for system_prompt**

Test that `build_system_prompt(settings, modules)` returns a string containing expected sections: role, available tools, confidence rules, formatting rules.

- [ ] **Step 2: Implement system_prompt.py**

```python
def build_system_prompt(settings, active_modules: list[str]) -> str:
    return f"""You are Intelligence8, an AI agent that operates ERPNext autonomously.

## Your Role
You are the primary operator of this company's ERP system. You receive events (emails, documents, scheduled tasks) and take action using the tools available to you. You make decisions with confidence scores.

## Decision Rules
- Confidence threshold: {settings.default_confidence_threshold}
- If your confidence is >= threshold: execute the action automatically
- If your confidence is < threshold: request human approval with your reasoning
- For submit/cancel operations: ALWAYS request human approval regardless of confidence
- For amounts above {settings.high_value_threshold}: require explicit human approval

## Active Modules
{chr(10).join(f'- {m}' for m in active_modules)}

## Response Format
BEFORE each tool call, include a text block with your reasoning and confidence score in this exact format:
Confidence: 0.XX

Example: "I found the matching PO for this supplier with exact amount match. Confidence: 0.95"

When requesting approval, format a clear summary of what you want to do and why.

## Language
Respond in Brazilian Portuguese for human-facing messages.
"""
```

- [ ] **Step 3: Write failing tests for approval_formatter**

Test formatting of approval messages for Telegram (with inline keyboard markup) and plain text.

- [ ] **Step 4: Implement approval_formatter.py**

```python
def format_approval_message(decision: dict) -> dict:
    """Returns {"text": str, "reply_markup": dict} for Telegram inline buttons."""
    text = (
        f"Aprovacao necessaria\n"
        f"Acao: {decision['action']}\n"
        f"Documento: {decision.get('related_doctype', '')} {decision.get('related_docname', '')}\n"
        f"Valor: {decision.get('amount', 'N/A')}\n"
        f"Confianca: {decision.get('confidence', 0):.0%}\n"
        f"Motivo: {decision.get('reasoning', '')}"
    )
    reply_markup = {
        "inline_keyboard": [[
            {"text": "Aprovar", "callback_data": f"approve:{decision['decision_log_name']}"},
            {"text": "Rejeitar", "callback_data": f"reject:{decision['decision_log_name']}"},
            {"text": "Detalhes", "callback_data": f"details:{decision['decision_log_name']}"},
        ]]
    }
    return {"text": text, "reply_markup": reply_markup}
```

- [ ] **Step 5: Run tests — expect PASS**
- [ ] **Step 6: Commit**

```bash
git add brazil_module/services/intelligence/prompts/ brazil_module/tests/test_system_prompt.py brazil_module/tests/test_approval_formatter.py
git commit -m "feat: add system prompt builder and approval message formatter"
```

---

## Task 10: Agent Brain

**Files:**
- Create: `brazil_module/services/intelligence/agent.py`
- Test: `brazil_module/tests/test_agent.py`

**Depends on:** Tasks 3, 4, 5, 6, 7, 8, 9

This is the central orchestrator. It wires everything together.

- [ ] **Step 1: Write failing tests**

Tests for:
- `process_single_event` calls ContextBuilder, then Claude API, then dispatches tool calls
- `process_single_event` skips when agent is disabled
- `process_single_event` skips when circuit breaker is open
- `process_single_event` logs cost via CostTracker
- `process_single_event` uses correct model tier based on event type
- `on_communication` creates event for email classification
- `on_nota_fiscal` calls existing `process_new_nf` and logs to Decision Ledger
- Idempotency: duplicate event ID is skipped

- [ ] **Step 2: Implement agent.py**

```python
import json
import re
import time

import anthropic
import frappe

from brazil_module.services.intelligence.action_executor import ActionExecutor
from brazil_module.services.intelligence.circuit_breaker import CircuitBreaker
from brazil_module.services.intelligence.context_builder import ContextBuilder
from brazil_module.services.intelligence.cost_tracker import CostTracker
from brazil_module.services.intelligence.decision_engine import DecisionEngine
from brazil_module.services.intelligence.prompts.system_prompt import build_system_prompt
from brazil_module.services.intelligence.tools import execute_tool, get_all_tool_schemas

_circuit_breaker = CircuitBreaker()


HAIKU_EVENTS = ["classify_email", "format_notification", "status_check", "simple_match", "recurring_schedule"]
OPUS_EVENTS = ["anomaly_detected", "high_value_decision", "complex_reconciliation"]


class Intelligence8Agent:
    def __init__(self):
        self.settings = frappe.get_single("I8 Agent Settings")
        self.client = anthropic.Anthropic(api_key=type(self.settings).get_api_key())
        self.context_builder = ContextBuilder()
        self.decision_engine = DecisionEngine(self.settings)
        self.action_executor = ActionExecutor()
        self.cost_tracker = CostTracker()

    def select_model(self, event_type: str) -> str:
        if event_type in HAIKU_EVENTS:
            return self.settings.haiku_model
        if event_type in OPUS_EVENTS:
            return self.settings.opus_model
        return self.settings.sonnet_model

    def get_timeout(self, model: str) -> int:
        if "haiku" in model:
            return self.settings.haiku_timeout_seconds or 30
        if "opus" in model:
            return self.settings.opus_timeout_seconds or 120
        return self.settings.sonnet_timeout_seconds or 60

    def process_event(self, event_type: str, event_data: dict) -> dict:
        if not self.settings.enabled:
            return {"status": "skipped", "reason": "agent_disabled"}

        if not _circuit_breaker.allow_request():
            return {"status": "queued", "reason": "circuit_breaker_open"}

        if not self.cost_tracker.check_daily_budget(float(self.settings.daily_budget_usd or 999)):
            if self.settings.pause_on_budget_exceeded:
                return {"status": "paused", "reason": "budget_exceeded"}

        model = self.select_model(event_type)
        context = self.context_builder.build(event_type, event_data)
        system_prompt = build_system_prompt(self.settings, ["p2p", "fiscal", "banking"])
        tools = get_all_tool_schemas()

        start = time.monotonic()
        try:
            response = self.client.messages.create(
                model=model,
                max_tokens=4096,
                system=system_prompt + "\n\n" + context.get("module_context", ""),
                messages=[{"role": "user", "content": json.dumps(event_data, default=str, ensure_ascii=False)}],
                tools=tools,
            )
            _circuit_breaker.record_success()
        except Exception as e:
            _circuit_breaker.record_failure()
            frappe.log_error(str(e), "Intelligence8 Claude API Error")
            return {"status": "error", "message": str(e)}

        latency_ms = int((time.monotonic() - start) * 1000)
        usage = response.usage
        cache_hit = getattr(usage, "cache_read_input_tokens", 0) > 0

        self.cost_tracker.log(
            model=model,
            tokens_in=usage.input_tokens,
            tokens_out=usage.output_tokens,
            latency_ms=latency_ms,
            module=event_data.get("module", "general"),
            function_name=event_type,
            cache_hit=cache_hit,
        )

        # Extract confidence from text blocks (agent reports confidence in its reasoning)
        confidence = self._extract_confidence(response)

        # Process tool calls
        results = []
        for block in response.content:
            if block.type == "tool_use":
                result = self._handle_tool_call(block, event_type, event_data, confidence)
                results.append(result)

        return {"status": "completed", "results": results}

    @staticmethod
    def _extract_confidence(response) -> float:
        """Extract confidence score from the agent's text response.

        The system prompt instructs the agent to include 'Confidence: 0.XX' in text blocks.
        Falls back to 0.5 (requires human approval) if not found.
        """
        for block in response.content:
            if block.type == "text":
                match = re.search(r"[Cc]onfidence:\s*(0\.\d+|1\.0)", block.text)
                if match:
                    return float(match.group(1))
        return 0.5  # Conservative default — will require human approval

    def _handle_tool_call(self, tool_block, event_type, event_data, confidence: float) -> dict:
        tool_name = tool_block.name
        tool_args = tool_block.input

        amount = tool_args.get("rate", 0) * tool_args.get("qty", 1) if "rate" in tool_args else tool_args.get("amount", 0)
        action = tool_name.split(".")[-1]
        doctype = tool_args.get("doctype", "")

        decision = self.decision_engine.evaluate(
            action=action, doctype=doctype,
            confidence=confidence, amount=amount,
        )

        if decision["auto_approve"]:
            try:
                result = execute_tool(tool_name, tool_args, self.action_executor)
                self.decision_engine.log_decision(
                    event_type=event_type, module=event_data.get("module", ""),
                    action=tool_name, actor="Agent", channel="system",
                    confidence=confidence, model=self.select_model(event_type),
                    input_summary=str(tool_args)[:500], reasoning="Auto-approved",
                    result="Success", related_doctype=doctype,
                    related_docname=result.get("name"),
                )
                return {"tool": tool_name, "status": "executed", "result": result}
            except Exception as e:
                frappe.log_error(str(e), f"I8 Tool Error: {tool_name}")
                return {"tool": tool_name, "status": "error", "message": str(e)}
        else:
            log_name = self.decision_engine.log_decision(
                event_type=event_type, module=event_data.get("module", ""),
                action=tool_name, actor="Agent", channel="system",
                confidence=confidence, model=self.select_model(event_type),
                input_summary=str(tool_args)[:500],
                reasoning="Below confidence threshold",
                result="Pending",
            )
            return {"tool": tool_name, "status": "pending_approval", "decision_log": log_name}


def process_pending_events():
    """Scheduled task: every 2 min. Enqueues pending events to long queue."""
    if not frappe.db.get_single_value("I8 Agent Settings", "enabled"):
        return
    # Future: query an event queue. For now, this is the entry point
    # that other hooks (on_communication, recurring scheduler) can enqueue to.


def process_single_event(event_type: str, event_id: str, event_data: dict):
    """Background job: processes one event."""
    lock_key = f"i8:lock:{event_type}:{event_id}"
    # Use Frappe's Redis cache with setnx for idempotency
    cache = frappe.cache
    if cache.get_value(lock_key):
        return  # Already processing
    cache.set_value(lock_key, 1, expires_in_sec=300)

    try:
        agent = Intelligence8Agent()
        agent.process_event(event_type, event_data)
    finally:
        cache.delete_value(lock_key)


def on_communication(doc, method=None):
    """Doc event hook: Communication after_insert. Enqueues for I8 classification."""
    if not frappe.db.get_single_value("I8 Agent Settings", "enabled"):
        return
    if doc.communication_type != "Communication" or doc.sent_or_received != "Received":
        return
    frappe.enqueue(
        "brazil_module.services.intelligence.agent.process_single_event",
        queue="long",
        timeout=60,
        event_type="classify_email",
        event_id=doc.name,
        event_data={"module": "email", "communication": doc.name, "subject": doc.subject, "content": doc.content, "sender": doc.sender},
        deduplicate=True,
    )


def on_nota_fiscal(doc, method=None):
    """Doc event hook: Nota Fiscal after_insert. Enqueues I8 processing.

    NOTE: process_new_nf runs separately via hooks list — NOT called here.
    """
    if not frappe.db.get_single_value("I8 Agent Settings", "enabled"):
        return
    frappe.enqueue(
        "brazil_module.services.intelligence.agent.process_single_event",
        queue="long",
        timeout=120,
        event_type="nf_received",
        event_id=doc.name,
        event_data={"module": "fiscal", "nota_fiscal": doc.name, "supplier": doc.cnpj_emitente},
        deduplicate=True,
    )
```

- [ ] **Step 3: Run tests — expect PASS**
- [ ] **Step 4: Commit**

```bash
git add brazil_module/services/intelligence/agent.py brazil_module/tests/test_agent.py
git commit -m "feat: add Intelligence8 Agent Brain with Claude API tool use"
```

---

## Task 11: Channel Router

**Files:**
- Create: `brazil_module/services/intelligence/channels/channel_router.py`
- Test: `brazil_module/tests/test_channel_router.py`

**Depends on:** Task 2

- [ ] **Step 1: Write failing tests**

Tests for:
- `route_message` creates I8 Conversation Message in the correct conversation
- `route_message` creates new I8 Conversation if none exists for context
- `get_or_create_conversation` finds existing active conversation by related document
- Messages from different channels appear in same thread

- [ ] **Step 2: Implement channel_router.py**

```python
class ChannelRouter:
    def route_message(self, channel, direction, actor, content,
                      related_doctype=None, related_docname=None,
                      telegram_message_id=None) -> str:
        conversation = self.get_or_create_conversation(related_doctype, related_docname)
        conversation.append("messages", {
            "channel": channel,
            "direction": direction,
            "actor": actor,
            "content": content,
            "timestamp": frappe.utils.now_datetime(),
            "related_doctype": related_doctype,
            "related_docname": related_docname,
            "telegram_message_id": telegram_message_id,
        })
        conversation.save(ignore_permissions=True)
        return conversation.name

    def get_or_create_conversation(self, related_doctype=None, related_docname=None):
        if related_doctype and related_docname:
            existing = frappe.get_all("I8 Conversation", filters={
                "related_doctype": related_doctype,
                "related_docname": related_docname,
                "status": "Active",
            }, limit=1)
            if existing:
                return frappe.get_doc("I8 Conversation", existing[0].name)
        doc = frappe.new_doc("I8 Conversation")
        doc.subject = f"{related_doctype or 'General'}: {related_docname or 'New'}"
        doc.status = "Active"
        doc.related_doctype = related_doctype
        doc.related_docname = related_docname
        doc.insert(ignore_permissions=True)
        return doc
```

- [ ] **Step 3: Run tests — expect PASS**
- [ ] **Step 4: Commit**

```bash
git add brazil_module/services/intelligence/channels/channel_router.py brazil_module/tests/test_channel_router.py
git commit -m "feat: add omnichannel router with unified conversation threading"
```

---

## Task 12: Telegram Bot

**Files:**
- Create: `brazil_module/services/intelligence/channels/telegram_bot.py`
- Test: `brazil_module/tests/test_telegram_bot.py`

**Depends on:** Tasks 10, 11

- [ ] **Step 1: Write failing tests**

Tests for:
- `validate_webhook` rejects requests without valid secret token
- `validate_webhook` accepts requests with correct secret token
- `authorize_user` rejects unknown telegram_user_id
- `authorize_user` returns Frappe user for known telegram_user_id
- `handle_callback_query` processes "approve:LOG-001" callback
- `handle_callback_query` checks approval limit before approving
- `send_message` calls Telegram API with correct chat_id and text
- `send_approval_request` sends message with inline keyboard

- [ ] **Step 2: Implement telegram_bot.py**

```python
import json

import requests
import frappe

from brazil_module.services.intelligence.channels.channel_router import ChannelRouter
from brazil_module.services.intelligence.prompts.approval_formatter import format_approval_message

TELEGRAM_API = "https://api.telegram.org/bot{token}"


class TelegramBot:
    def __init__(self):
        from brazil_module.intelligence.doctype.i8_agent_settings.i8_agent_settings import I8AgentSettings
        self._token = I8AgentSettings.get_telegram_token()
        self._settings = I8AgentSettings.get_settings()
        self._router = ChannelRouter()

    def validate_webhook(self, request_secret: str) -> bool:
        expected = self._settings.get_password("telegram_webhook_secret")
        return request_secret == expected

    def authorize_user(self, telegram_user_id: str) -> dict | None:
        for user_row in self._settings.telegram_users:
            if user_row.telegram_user_id == str(telegram_user_id) and user_row.active:
                return {"user": user_row.user, "approval_limit": float(user_row.approval_limit or 0)}
        return None

    def handle_update(self, update: dict) -> None:
        if "callback_query" in update:
            self._handle_callback(update["callback_query"])
        elif "message" in update:
            self._handle_message(update["message"])

    def _handle_callback(self, callback: dict) -> None:
        user_id = str(callback["from"]["id"])
        auth = self.authorize_user(user_id)
        if not auth:
            return

        data = callback.get("data", "")
        action, _, log_name = data.partition(":")

        if action == "approve":
            self._process_approval(log_name, auth, approved=True)
        elif action == "reject":
            self._process_approval(log_name, auth, approved=False)
        elif action == "details":
            self._send_details(log_name, callback["message"]["chat"]["id"])

    def _process_approval(self, log_name: str, auth: dict, approved: bool) -> None:
        log = frappe.get_doc("I8 Decision Log", log_name)

        # Check approval limit against transaction value (from input_summary), not LLM cost
        transaction_amount = self._extract_transaction_amount(log)
        if auth["approval_limit"] > 0 and transaction_amount > auth["approval_limit"]:
            self.send_message(
                self._settings.telegram_chat_id,
                f"Limite de aprovacao excedido. Valor: R${transaction_amount:,.2f}, Limite: R${auth['approval_limit']:,.2f}",
            )
            return

        # Resolve the pending decision log (draft -> submitted, now immutable)
        log.resolve(
            actor="Human",
            result="Success" if approved else "Rejected",
            channel="telegram",
            human_override=True,
        )

        if approved:
            # Execute the pending action
            frappe.enqueue(
                "brazil_module.services.intelligence.agent.process_single_event",
                queue="long",
                event_type="approved_action",
                event_id=log_name,
                event_data={"decision_log": log_name, "module": log.module},
            )
            self.send_message(self._settings.telegram_chat_id, f"Aprovado: {log.action} - {log.related_docname}")
        else:
            self.send_message(self._settings.telegram_chat_id, f"Rejeitado: {log.action} - {log.related_docname}")

        self._router.route_message(
            channel="telegram", direction="incoming",
            actor="human", content=f"{'Aprovado' if approved else 'Rejeitado'}: {log_name}",
            related_doctype=log.related_doctype, related_docname=log.related_docname,
        )

    def _handle_message(self, message: dict) -> None:
        user_id = str(message["from"]["id"])
        auth = self.authorize_user(user_id)
        if not auth:
            return

        text = message.get("text", "")
        chat_id = message["chat"]["id"]

        self._router.route_message(
            channel="telegram", direction="incoming", actor="human", content=text,
        )

        # Enqueue for agent processing
        frappe.enqueue(
            "brazil_module.services.intelligence.agent.process_single_event",
            queue="long",
            event_type="human_message",
            event_id=str(message["message_id"]),
            event_data={"module": "conversation", "text": text, "chat_id": chat_id, "user": auth["user"]},
        )

    @staticmethod
    def _extract_transaction_amount(log) -> float:
        """Extract the monetary transaction amount from the decision log's input_summary."""
        import json as _json
        try:
            data = _json.loads(log.input_summary or "{}")
            return float(data.get("amount", 0) or data.get("rate", 0) * data.get("qty", 1))
        except (ValueError, TypeError):
            return 0.0

    def send_message(self, chat_id: str, text: str, reply_markup: dict | None = None) -> dict:
        url = f"{TELEGRAM_API.format(token=self._token)}/sendMessage"
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        if reply_markup:
            payload["reply_markup"] = json.dumps(reply_markup)
        resp = requests.post(url, json=payload, timeout=10)
        return resp.json()

    def send_approval_request(self, decision: dict) -> dict:
        formatted = format_approval_message(decision)
        return self.send_message(
            self._settings.telegram_chat_id,
            formatted["text"],
            formatted["reply_markup"],
        )
```

- [ ] **Step 3: Run tests — expect PASS**
- [ ] **Step 4: Commit**

```bash
git add brazil_module/services/intelligence/channels/telegram_bot.py brazil_module/tests/test_telegram_bot.py
git commit -m "feat: add Telegram bot with webhook auth, approvals, and user authorization"
```

---

## Task 13: ERP Chat Backend

**Files:**
- Create: `brazil_module/services/intelligence/channels/erp_chat.py`
- Test: `brazil_module/tests/test_erp_chat.py`

**Depends on:** Tasks 10, 11

- [ ] **Step 1: Write failing tests**
- [ ] **Step 2: Implement erp_chat.py**

Handles messages from the ERP Chat widget:
- `send_message(user, text)` — routes through ChannelRouter, enqueues for agent
- `get_conversation_history(conversation_name)` — returns messages for display

- [ ] **Step 3: Run tests — expect PASS**
- [ ] **Step 4: Commit**

```bash
git add brazil_module/services/intelligence/channels/erp_chat.py brazil_module/tests/test_erp_chat.py
git commit -m "feat: add ERP Chat backend for omnichannel conversation"
```

---

## Task 14: Recurring Expense Scheduler

**Files:**
- Create: `brazil_module/services/intelligence/recurring/expense_scheduler.py`
- Test: `brazil_module/tests/test_expense_scheduler.py`

**Depends on:** Tasks 8, 10

- [ ] **Step 1: Write failing tests**

Tests for:
- `daily_check` finds expenses due today (based on frequency, day_of_month, lead_days)
- `daily_check` skips inactive expenses
- `daily_check` skips already-created-this-period expenses
- `_calculate_next_due` correctly handles Monthly, Weekly, Quarterly, Yearly
- `_create_document_for_expense` enqueues agent event to create PO/JE

- [ ] **Step 2: Implement expense_scheduler.py**

```python
import calendar
from datetime import date, timedelta

import frappe


def daily_check():
    """Scheduled: 0 7 * * *. Check for recurring expenses due today."""
    if not frappe.db.get_single_value("I8 Agent Settings", "enabled"):
        return

    today = date.today()
    expenses = frappe.get_all(
        "I8 Recurring Expense",
        filters={"active": 1},
        fields=["name", "title", "supplier", "document_type", "estimated_amount",
                "currency", "frequency", "day_of_month", "lead_days",
                "notify_supplier", "last_created", "next_due"],
    )

    for expense in expenses:
        if _is_due(expense, today):
            frappe.enqueue(
                "brazil_module.services.intelligence.agent.process_single_event",
                queue="long",
                timeout=120,
                event_type="recurring_schedule",
                event_id=f"recurring:{expense['name']}:{today.isoformat()}",
                event_data={
                    "module": "p2p",
                    "recurring_expense": expense["name"],
                    "supplier": expense["supplier"],
                    "document_type": expense["document_type"],
                    "amount": float(expense["estimated_amount"]),
                    "currency": expense["currency"],
                },
                deduplicate=True,
            )


def _is_due(expense: dict, today: date) -> bool:
    next_due = expense.get("next_due")
    if not next_due:
        return False
    lead_days = expense.get("lead_days") or 0
    trigger_date = next_due - timedelta(days=lead_days)
    return today >= trigger_date and (not expense.get("last_created") or expense["last_created"] < next_due)


def _add_months(d: date, months: int) -> date:
    """Add months to a date, clamping day to valid range."""
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    return d

def calculate_next_due(frequency: str, day_of_month: int, after_date: date) -> date:
    if frequency == "Monthly":
        month = after_date.month % 12 + 1
        year = after_date.year + (1 if month == 1 else 0)
        max_day = calendar.monthrange(year, month)[1]
        return date(year, month, min(day_of_month, max_day))
    elif frequency == "Weekly":
        return after_date + timedelta(weeks=1)
    elif frequency == "Quarterly":
        month = (after_date.month - 1 + 3) % 12 + 1
        year = after_date.year + ((after_date.month - 1 + 3) // 12)
        max_day = calendar.monthrange(year, month)[1]
        return date(year, month, min(day_of_month, max_day))
    elif frequency == "Yearly":
        year = after_date.year + 1
        max_day = calendar.monthrange(year, after_date.month)[1]
        return date(year, after_date.month, min(day_of_month, max_day))
    # Default: monthly
    month = after_date.month % 12 + 1
    year = after_date.year + (1 if month == 1 else 0)
    max_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(day_of_month, max_day))
```

- [ ] **Step 3: Run tests — expect PASS**
- [ ] **Step 4: Commit**

```bash
git add brazil_module/services/intelligence/recurring/expense_scheduler.py brazil_module/tests/test_expense_scheduler.py
git commit -m "feat: add recurring expense scheduler as Auto Repeat replacement"
```

---

## Task 15: Follow-Up Manager

**Files:**
- Create: `brazil_module/services/intelligence/recurring/follow_up_manager.py`
- Test: `brazil_module/tests/test_follow_up_manager.py`

**Depends on:** Tasks 8, 10, 14

- [ ] **Step 1: Write failing tests**

Tests for:
- `check_overdue` finds POs past expected NF delivery date
- `check_overdue` respects max_follow_ups limit
- `check_overdue` enqueues agent event with follow-up context

- [ ] **Step 2: Implement follow_up_manager.py**

```python
def check_overdue():
    """Scheduled: 0 9 * * *. Check for overdue NF deliveries and trigger follow-up."""
    if not frappe.db.get_single_value("I8 Agent Settings", "enabled"):
        return

    profiles = frappe.get_all("I8 Supplier Profile", filters={}, fields=["name", "supplier", "expected_nf_days", "follow_up_after_days", "max_follow_ups", "follow_up_interval_days"])

    for profile in profiles:
        overdue_pos = _find_overdue_pos(profile)
        for po in overdue_pos:
            frappe.enqueue(
                "brazil_module.services.intelligence.agent.process_single_event",
                queue="long",
                event_type="follow_up_supplier",
                event_id=f"followup:{po['name']}",
                event_data={
                    "module": "p2p",
                    "purchase_order": po["name"],
                    "supplier": profile["supplier"],
                    "supplier_profile": profile["name"],
                    "days_overdue": po["days_overdue"],
                },
                deduplicate=True,
            )
```

- [ ] **Step 3: Run tests — expect PASS**
- [ ] **Step 4: Commit**

```bash
git add brazil_module/services/intelligence/recurring/follow_up_manager.py brazil_module/tests/test_follow_up_manager.py
git commit -m "feat: add follow-up manager for overdue NF supplier tracking"
```

---

## Task 16: Hooks and Setup Integration

**Files:**
- Modify: `brazil_module/hooks.py`
- Modify: `brazil_module/setup/install.py`

**Depends on:** Tasks 10, 12, 14, 15

- [ ] **Step 1: Update hooks.py**

Add to `scheduler_events["cron"]` (no agent event loop for now — all events are enqueued directly by hooks):
```python
# NOTE: process_pending_events is NOT scheduled yet — events flow via doc_events and daily schedulers.
# A centralized event queue will be added when needed (Phase 2+).
"0 7 * * *": ["brazil_module.services.intelligence.recurring.expense_scheduler.daily_check"],
"0 9 * * *": ["brazil_module.services.intelligence.recurring.follow_up_manager.check_overdue"],
```

Update `doc_events` (use lists to preserve existing hooks):
```python
"Communication": {
    "after_insert": [
        "brazil_module.services.fiscal.email_monitor.check_nf_attachment",
        "brazil_module.services.intelligence.agent.on_communication",
    ]
},
"Nota Fiscal": {
    "after_insert": [
        "brazil_module.services.fiscal.processor.process_new_nf",
        "brazil_module.services.intelligence.agent.on_nota_fiscal",
    ],
    "validate": "brazil_module.services.fiscal.processor.validate_nf",
},
```

Note: `on_nota_fiscal` does NOT call `process_new_nf` internally — both run independently via the hook list. This ensures the fiscal pipeline works even if Intelligence8 is uninstalled.

- [ ] **Step 2: Update setup/install.py**

Add Intelligence8 custom fields for Communication:
```python
intelligence_fields = {
    "Communication": [
        {"fieldname": "i8_section", "fieldtype": "Section Break", "label": "Intelligence8", "collapsible": 1, "insert_after": "nf_processed"},
        {"fieldname": "i8_processed", "fieldtype": "Check", "label": "I8 Processed", "insert_after": "i8_section", "read_only": 1},
        {"fieldname": "i8_classification", "fieldtype": "Select", "label": "I8 Classification", "options": "\nFISCAL\nCOMMERCIAL\nFINANCIAL\nOPERATIONAL\nSPAM\nUNCERTAIN", "insert_after": "i8_processed", "read_only": 1},
        {"fieldname": "i8_decision_log", "fieldtype": "Link", "label": "I8 Decision Log", "options": "I8 Decision Log", "insert_after": "i8_classification", "read_only": 1},
    ]
}
```

Add roles:
```python
intelligence_roles = [
    {"role_name": "Intelligence8 Admin", "desk_access": 1},
    {"role_name": "Intelligence8 Viewer", "desk_access": 1},
]
```

- [ ] **Step 3: Commit**

```bash
git add brazil_module/hooks.py brazil_module/setup/install.py
git commit -m "feat: integrate Intelligence8 hooks, scheduled tasks, and custom fields"
```

---

## Task 17: API Endpoints

**Files:**
- Modify: `brazil_module/api/__init__.py`

**Depends on:** Tasks 10, 12, 13

- [ ] **Step 1: Add Telegram webhook endpoint**

```python
@frappe.whitelist(allow_guest=True, methods=["POST"])
def telegram_webhook():
    """Receive Telegram Bot webhook updates."""
    from brazil_module.services.intelligence.channels.telegram_bot import TelegramBot
    bot = TelegramBot()
    secret = frappe.request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not bot.validate_webhook(secret):
        frappe.throw("Unauthorized", frappe.AuthenticationError)
    update = frappe.parse_json(frappe.request.data)
    bot.handle_update(update)
    return {"status": "ok"}
```

- [ ] **Step 2: Add ERP Chat endpoints**

```python
@frappe.whitelist()
def i8_chat_send(message, conversation=None):
    """Send a message to Intelligence8 via ERP Chat."""
    from brazil_module.services.intelligence.channels.erp_chat import send_message
    return send_message(frappe.session.user, message, conversation)

@frappe.whitelist()
def i8_chat_history(conversation):
    """Get conversation history for ERP Chat widget."""
    from brazil_module.services.intelligence.channels.erp_chat import get_conversation_history
    return get_conversation_history(conversation)
```

- [ ] **Step 3: Add dashboard data endpoint**

```python
@frappe.whitelist()
def i8_dashboard_data():
    """Get Intelligence8 dashboard data (cost summary, decisions, pending)."""
    from brazil_module.services.intelligence.cost_tracker import CostTracker
    tracker = CostTracker()
    return {
        "daily_cost": tracker.get_daily_total(),
        "pending_approvals": frappe.db.count("I8 Decision Log", {"result": "Pending"}),
        "decisions_today": frappe.db.count("I8 Decision Log", {"timestamp": [">=", frappe.utils.today()]}),
    }
```

- [ ] **Step 4: Commit**

```bash
git add brazil_module/api/__init__.py
git commit -m "feat: add Intelligence8 API endpoints (telegram webhook, chat, dashboard)"
```

---

## Task 18: ERP Chat Widget (JavaScript)

**Files:**
- Create: `brazil_module/public/js/i8_chat_widget.js`

**Depends on:** Task 17

- [ ] **Step 1: Create chat widget**

Create `brazil_module/public/js/i8_chat_widget.js`:

A Frappe page script that:
- Renders a floating chat button in the bottom-right corner
- Opens a chat panel with conversation history
- Sends messages via `frappe.call("brazil_module.api.i8_chat_send")`
- Polls for new messages (or uses Frappe realtime)
- Displays messages with channel indicators (Telegram/ERP/System)

- [ ] **Step 2: Add JS to hooks.py**

Add to `app_include_js` in hooks.py:
```python
app_include_js = "/assets/brazil_module/js/i8_chat_widget.js"
```

- [ ] **Step 3: Commit**

```bash
git add brazil_module/public/js/i8_chat_widget.js brazil_module/hooks.py
git commit -m "feat: add Intelligence8 ERP Chat floating widget"
```

---

## Task 19: Intelligence8 Workspace

**Files:**
- Create: `brazil_module/intelligence/workspace/intelligence8/intelligence8.json`

**Depends on:** Task 2

- [ ] **Step 1: Create workspace JSON**

Follow the pattern from existing workspace files (Fiscal, Bancos). Include:
- Shortcuts to: I8 Agent Settings, I8 Decision Log, I8 Cost Log, I8 Recurring Expense, I8 Supplier Profile, I8 Conversation
- Number cards: Pending Approvals, Today's Decisions, Daily LLM Cost
- Charts: Decisions by Module, Cost Trend

- [ ] **Step 2: Commit**

```bash
git add brazil_module/intelligence/workspace/
git commit -m "feat: add Intelligence8 workspace with dashboard shortcuts"
```

---

## Task 20: Integration Test

**Files:**
- Create: `brazil_module/tests/test_integration_intelligence.py`

**Depends on:** All previous tasks

- [ ] **Step 1: Write integration test**

End-to-end test that simulates:
1. Agent receives a recurring expense event
2. Agent calls Claude API (mocked) which returns a `p2p.create_purchase_order` tool call
3. Decision Engine evaluates confidence
4. Action Executor creates the PO
5. Decision Ledger records the decision
6. Cost Tracker logs the LLM usage
7. Channel Router logs the action to a conversation

```python
class TestIntelligence8Integration(unittest.TestCase):
    def test_recurring_expense_creates_po(self):
        """Full flow: recurring expense -> agent -> PO creation -> audit log."""
        # Mock Claude API to return tool call
        mock_response = MagicMock()
        mock_response.content = [MagicMock(
            type="tool_use",
            name="p2p.create_purchase_order",
            input={"supplier": "DigitalOcean", "required_by": "2026-04-05",
                   "items": [{"item_code": "HOSTING", "qty": 1, "rate": 500}],
                   "confidence": 0.92},
        )]
        mock_response.usage = MagicMock(input_tokens=500, output_tokens=100)

        with patch("anthropic.Anthropic") as MockClient:
            MockClient.return_value.messages.create.return_value = mock_response
            agent = Intelligence8Agent()
            result = agent.process_event("recurring_schedule", {
                "module": "p2p",
                "recurring_expense": "RE-001",
                "supplier": "DigitalOcean",
                "amount": 500,
            })

        self.assertEqual(result["status"], "completed")
        # Verify PO was created
        frappe.new_doc.assert_any_call("Purchase Order")
        # Verify decision was logged
        frappe.new_doc.assert_any_call("I8 Decision Log")
        # Verify cost was logged
        frappe.new_doc.assert_any_call("I8 Cost Log")
```

- [ ] **Step 2: Run full test suite**

Run: `python3 -m pytest brazil_module/tests/ -v --tb=short`
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
git add brazil_module/tests/test_integration_intelligence.py
git commit -m "test: add Intelligence8 integration test for recurring expense flow"
```

---

## Task 21: Final Verification

- [ ] **Step 1: Run full test suite**

```bash
python3 -m pytest brazil_module/tests/ -v --tb=short
```

Verify: All tests pass, no regressions in existing tests.

- [ ] **Step 2: Run linter**

```bash
ruff check brazil_module/
```

Fix any issues.

- [ ] **Step 3: Verify file count and structure**

```bash
find brazil_module/services/intelligence -name "*.py" | wc -l  # expect ~20
find brazil_module/intelligence/doctype -name "*.json" | wc -l  # expect 10
find brazil_module/tests/test_*intelligence* brazil_module/tests/test_*i8* brazil_module/tests/test_circuit* brazil_module/tests/test_decision* brazil_module/tests/test_action_executor* brazil_module/tests/test_cost_tracker* brazil_module/tests/test_channel* brazil_module/tests/test_telegram* brazil_module/tests/test_erp_chat* brazil_module/tests/test_*_tools.py brazil_module/tests/test_expense* brazil_module/tests/test_follow_up* brazil_module/tests/test_system_prompt* brazil_module/tests/test_approval* -name "*.py" 2>/dev/null | wc -l  # expect ~17
```

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "chore: Intelligence8 Phase 1 complete — Core + P2P agent operational"
```
