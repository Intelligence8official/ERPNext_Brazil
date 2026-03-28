# Multi-Agent Architecture with Orchestrator

**Date:** 2026-03-28
**Status:** Approved Design

---

## 1. Overview

Evolve Intelligence8 from a single-agent architecture to a multi-agent system where:

- An **Orchestrator** classifies events and dispatches to the right specialized agent (supports multi-module pipeline for complex requests)
- Each **Module Agent** has its own prompt, read/write tools, and model stored in the database (I8 Module Registry)
- A **base system prompt** in Agent Settings defines global rules (language, confidence format, etc.)
- Adding a new agent = creating a new I8 Module Registry record in ERPNext (no deploy needed)
- Full **trace_id** propagation from event → orchestrator → module → tool calls for observability
- **Prompt versioning** via Frappe native `track_changes` (automatic history on every save)
- **Escalation model** per module for uncertain/complex scenarios

---

## 2. Orchestrator

### Event Routing

Two-tier routing:

1. **Configurable mapping** — child table `I8 Event Routing` on Agent Settings maps `event_type → module_name`. For known system events (recurring_schedule, nf_received, classify_email).
2. **LLM fallback** — for `human_message` and unmapped events, a Haiku call classifies which module should handle it based on module descriptions.

### I8 Event Routing (new child table)

| Field | Type | Description |
|---|---|---|
| `event_type` | Data | Event type string (e.g., "recurring_schedule") |
| `module_name` | Link(I8 Module Registry) | Target module |

### Orchestrator logic

```
route_event(event_type, event_data):
    1. If event_type == "approved_action":
       → Read Decision Log → get original module from log.module field
       → Re-dispatch to that module
    2. Check I8 Event Routing for event_type → module_name
       → If found and module is enabled → return [module_name]
    3. If not found → call Haiku with list of enabled modules + descriptions
       → Haiku returns ordered list of modules (e.g., ["fiscal", "p2p"])
       → For ambiguous requests like "mostra NFs e agenda pagamento"
    4. If Haiku fails → fallback to ["conversational"]
```

### Multi-Module Pipeline

When the orchestrator returns multiple modules (e.g., ["fiscal", "p2p"]):
1. Execute first module, collect results
2. Pass results as additional context to second module
3. Aggregate all results and text responses
4. Send unified response to user

This handles requests like "mostra as NFs pendentes e agenda o pagamento" that span fiscal + p2p.

### Trace ID

A `trace_id` (UUID) is generated at the start of every event and propagated to:
- I8 Decision Log (new field `trace_id`)
- I8 Cost Log (new field `trace_id`)
- I8 Conversation Message (new field `trace_id`)

This connects: event arrival → orchestrator decision → module execution → tool calls → response. Essential for debugging in production.

---

## 3. Agent Settings — New Fields

| Field | Type | Description |
|---|---|---|
| `base_system_prompt` | Code (text) | Global system prompt with rules, language, confidence format. Concatenated before every module's context_prompt. |
| `event_routing` | Table(I8 Event Routing) | Configurable event_type → module_name mapping |

### New field on I8 Decision Log and I8 Cost Log

| Field | Type | Description |
|---|---|---|
| `trace_id` | Data | UUID propagated from event start through the entire processing chain |
| `module` | Data | Module that processed this event (already exists on Decision Log) |

---

## 4. I8 Module Registry — Updated Fields

| Campo | Tipo | Uso |
|---|---|---|
| `module_name` | Data | Identifier: "p2p", "fiscal", "email", "conversational" |
| `description` | Small Text | Used by orchestrator LLM to choose module |
| `context_prompt` | Code (text) | Module-specific system prompt (editable in ERPNext) |
| `read_tools` | Code (JSON) | JSON array of read-only tool patterns (auto-approved) |
| `write_tools` | Code (JSON) | JSON array of write tool patterns (go through Decision Engine) |
| `default_model` | Select | Primary model: haiku, sonnet, opus |
| `escalation_model` | Select | Retry model when UNCERTAIN/low confidence. Blank = no escalation. |
| `enabled` | Check | Enable/disable module |

**Security:** Read tools are always auto-approved (no Decision Engine). Write tools go through the existing Decision Engine (confidence threshold, learning loop, approval flow). The conversational module should have NO write_tools.

**Prompt versioning:** Module Registry has `track_changes = 1`. Every save creates a Frappe Version record. History viewable via "View > Version" in the document.

**Escalation:** When the module's first LLM call returns no tool calls or all confidence < 0.3, the orchestrator retries with `escalation_model`. Example: email classification Haiku returns UNCERTAIN → retry with Sonnet.

---

## 5. Module Agent Execution

Each module agent:

1. Reads `base_system_prompt` from Agent Settings (global rules)
2. Reads `context_prompt` from I8 Module Registry (module-specific instructions)
3. Concatenates: `base_prompt + "\n\n" + module_prompt`
4. Combines `read_tools + write_tools` and filters available tools
5. Calls Claude API with the module's `default_model`
6. Runs agentic loop (existing, up to 5 turns)
7. If no tool calls and escalation_model is set → retry with escalation model

### Tool Filtering

`read_tools` and `write_tools` store JSON arrays of tool name patterns:

```json
// read_tools
["erp-read_document", "erp-list_documents", "erp-get_report_data"]

// write_tools
["p2p-create_purchase_order", "p2p-send_po_to_supplier"]
```

Pattern matching: exact name or prefix with `*` (e.g., `"p2p-*"` matches all p2p tools).

In `_handle_tool_call`: if the tool is in `read_tools` → auto-approve (ALWAYS_APPROVE). If in `write_tools` → Decision Engine evaluates.

---

## 6. Initial Module Seed Data

### 5a: P2P Module

- **module_name:** p2p
- **description:** Manages procurement: creates Purchase Orders, schedules payments, sends POs to suppliers, tracks due invoices.
- **default_model:** sonnet
- **read_tools:** `["erp-read_document", "erp-list_documents", "erp-get_report_data", "p2p-list_due_invoices"]`
- **write_tools:** `["p2p-create_purchase_order", "p2p-send_po_to_supplier"]`
- **escalation_model:** opus
- **context_prompt:**
```
You are the P2P (Procure-to-Pay) agent for Intelligence8.

## Your Responsibilities
- Create Purchase Orders for recurring expenses
- Send POs to suppliers when notify_supplier is enabled
- List and track due invoices
- Schedule payments

## Rules
- ALWAYS call tools to execute actions. Never just describe what you would do.
- For recurring_schedule events: call p2p-create_purchase_order with the supplier, required_by date, and items from the expense data.
- If notify_supplier is Yes, the system will auto-send the PO after creation.

## Response Format
Include confidence score before each tool call:
Confidence: 0.XX
```

### 5b: Fiscal Module

- **module_name:** fiscal
- **description:** Processes Notas Fiscais: matches to POs, creates Purchase Invoices, manages NF status.
- **default_model:** sonnet
- **read_tools:** `["fiscal-get_nf_details", "fiscal-find_matching_pos", "fiscal-find_recurring_expense", "erp-read_document", "erp-list_documents"]`
- **write_tools:** `["fiscal-create_purchase_invoice", "fiscal-link_nf_to_po", "fiscal-update_nf_status", "p2p-create_purchase_order"]`
- **escalation_model:** opus
- **context_prompt:**
```
You are the Fiscal agent for Intelligence8.

## Your Responsibilities
- Process incoming Notas Fiscais (NF-e, NFS-e, CT-e)
- Match NFs to existing Purchase Orders
- Create Purchase Invoices from NFs
- Handle 4 scenarios:
  A. NF has matching PO → link + create invoice
  B. No PO but recurring expense exists → create PO first, then link + invoice
  C. Known supplier, no PO → create invoice directly
  D. Unknown supplier → mark as Needs Review

## Steps for nf_received events:
1. Call fiscal-get_nf_details to read the NF
2. Call fiscal-find_matching_pos to find POs for this supplier
3. Follow the appropriate scenario (A/B/C/D)

## Response Format
Include confidence score before each tool call:
Confidence: 0.XX
```

### 5c: Email Module

- **module_name:** email
- **description:** Classifies incoming emails into categories: FISCAL, COMMERCIAL, FINANCIAL, OPERATIONAL, SPAM, UNCERTAIN.
- **default_model:** haiku
- **read_tools:** `["email-get_content", "email-search"]`
- **write_tools:** `["email-classify"]`
- **escalation_model:** sonnet
- **context_prompt:**
```
You are the Email Classification agent for Intelligence8.

## Your Only Task
Classify the email by calling email-classify with:
- communication: the Communication document name
- classification: FISCAL, COMMERCIAL, FINANCIAL, OPERATIONAL, SPAM, or UNCERTAIN
- reasoning: brief explanation

## Classification Rules
- FISCAL: invoices, NF, tax documents, boletos
- COMMERCIAL: proposals, quotes, orders, contracts
- FINANCIAL: bank statements, receipts, payment confirmations
- OPERATIONAL: supplier responses about POs, delivery confirmations
- SPAM: marketing, newsletters, promotions
- UNCERTAIN: cannot determine

DO NOT call any other tool. Just classify.

Confidence: 0.95
```

### 5d: Banking Module

- **module_name:** banking
- **description:** Manages banking operations: reconciles transactions, checks balances, monitors payment status.
- **default_model:** sonnet
- **read_tools:** `["banking-get_balance", "banking-reconcile_transactions", "erp-read_document", "erp-list_documents", "erp-get_account_balance"]`
- **write_tools:** `["banking-create_payment"]`
- **escalation_model:** (blank)
- **context_prompt:**
```
You are the Banking agent for Intelligence8.

## Your Responsibilities
- Check bank account balances
- Reconcile bank transactions with payments and invoices
- Monitor payment execution status

## Response Format
Include confidence score before each tool call:
Confidence: 0.XX
```

### 5e: Conversational Module

- **module_name:** conversational
- **description:** Answers user questions about the ERP: financial reports, expense summaries, supplier info, pending actions.
- **default_model:** sonnet
- **read_tools:** `["erp-read_document", "erp-list_documents", "erp-get_report_data", "erp-get_account_balance", "banking-get_balance", "p2p-list_due_invoices", "fiscal-get_nf_details"]`
- **write_tools:** `[]` (read-only — no write access)
- **escalation_model:** opus
- **context_prompt:**
```
You are the Conversational agent for Intelligence8.

## Your Role
Answer the user's questions about the ERP system. Use tools to fetch data and provide accurate, formatted responses.

## Guidelines
- Respond in Brazilian Portuguese
- Use tables and formatting for readability
- Always query actual data — never guess or estimate
- If you don't have a tool for what the user asks, say so clearly

## Available Actions
- Query financial reports (erp-get_report_data)
- Check account balances (erp-get_account_balance, banking-get_balance)
- List documents (erp-list_documents)
- Read specific documents (erp-read_document)
- List due invoices (p2p-list_due_invoices)
```

---

## 7. Event Routing Seed Data

| event_type | module_name |
|---|---|
| recurring_schedule | p2p |
| nf_received | fiscal |
| classify_email | email |
| follow_up_supplier | p2p |
| human_message | (LLM fallback) |
| approved_action | (uses original module from Decision Log) |

---

## 8. Code Changes

### 7a: agent.py — Refactor to use Orchestrator

Replace the monolithic `process_event` with:

```python
def process_event(self, event_type, event_data):
    # 1. Route to module
    module_name = self._route_event(event_type, event_data)

    # 2. Load module config
    registry = self._get_module_registry(module_name)

    # 3. Build prompt: base + module
    system_prompt = self._build_prompt(registry)

    # 4. Filter tools for this module
    tools = self._filter_tools(registry)

    # 5. Select model
    model = self._resolve_model(registry)

    # 6. Run agentic loop (existing logic)
    return self._run_agentic_loop(model, system_prompt, tools, event_type, event_data)
```

### 7b: prompts/system_prompt.py — Simplify

`build_system_prompt` becomes a thin reader of `Agent Settings.base_system_prompt`. The module-specific prompt comes from the registry.

### 7c: tools/__init__.py — Add filter_tools

```python
def filter_tools_for_module(all_schemas, module_tools_json):
    """Filter tool schemas based on module's tools_definition patterns."""
    import json
    patterns = json.loads(module_tools_json or "[]")

    filtered = []
    for schema in all_schemas:
        tool_name = schema["name"]
        for pattern in patterns:
            if pattern.endswith("*"):
                if tool_name.startswith(pattern[:-1]):
                    filtered.append(schema)
                    break
            elif tool_name == pattern:
                filtered.append(schema)
                break
    return filtered
```

### 7d: Backward compatibility

If no I8 Module Registry records exist or module is not found:
- Use all tools (no filtering)
- Use hardcoded base prompt
- Use sonnet model
- Log warning

---

## 9. What Stays Unchanged

- `context_builder.py` — already works, enriches event context
- `decision_engine.py` — unchanged, evaluates confidence per tool call
- `action_executor.py` — unchanged, executes sandboxed actions
- `cost_tracker.py` — unchanged, logs per-call costs
- `circuit_breaker.py` — unchanged, shared across all agents
- `learning_engine.py` — unchanged, works per action pattern
- `telegram_bot.py` — unchanged, routes messages to agent
- `channels/` — unchanged
- `recurring/` — unchanged
- All existing tools — unchanged
