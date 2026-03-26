# Payment Automation + Learning Loop

**Date:** 2026-03-26
**Status:** Approved Design

---

## 1. Payment Automation

### 1.1 Supplier Custom Fields

Move I8 Supplier Profile data to custom fields on Supplier DocType. Remove I8 Supplier Profile.

New Section Break "Intelligence8" on Supplier:

| Field | Type | Description |
|---|---|---|
| `i8_section` | Section Break | "Intelligence8" (collapsible) |
| `pix_key` | Data | PIX key |
| `pix_key_type` | Select | CPF/CNPJ/Email/Telefone/Aleatoria |
| `i8_expected_nf_days` | Int | Days to expect NF (default 5) |
| `i8_nf_due_day` | Int | Day of month supplier issues NF |
| `i8_follow_up_after_days` | Int | Days before follow-up (default 7) |
| `i8_max_follow_ups` | Int | Max follow-ups (default 3) |
| `i8_auto_pay` | Check | Auto-pay when NF arrives |
| `i8_agent_notes` | Long Text | Context notes for the agent |

New custom field on Purchase Invoice:

| Field | Type | Description |
|---|---|---|
| `boleto_barcode` | Data | Linha digitavel for boleto payment |

### 1.2 Payment Method Detection

The agent determines payment method from Purchase Invoice Payment Terms:

```
Purchase Invoice → Payment Schedule → Mode of Payment
  → "Pix" → schedule PIX via Inter API
  → "Boleto" → schedule boleto payment (requires boleto_barcode)
  → "Credit Card" → auto-create Payment Entry and submit (already paid)
  → "Wire Transfer" / "TED" → schedule TED via Inter API
```

### 1.3 Weekly Payment Scheduling (Monday)

New function `schedule_weekly_payments()` in `planning_loop.py`:

1. Query Purchase Invoices: docstatus=1, outstanding_amount > 0, due_date between Monday and Sunday of current week
2. For each PI:
   - Get mode_of_payment from payment schedule
   - PIX: get supplier.pix_key → call Inter API `send_pix` with `dataAgendamento` = due_date → if success, create Payment Entry draft
   - Boleto: check PI.boleto_barcode → if exists, call Inter API `pay_barcode` with scheduled date → if success, create Payment Entry draft. If no barcode, alert Telegram "falta linha digitavel"
   - Credit Card: create Payment Entry draft → submit immediately (already paid by card) → notify Telegram
   - TED: similar to PIX but via `send_ted`
3. Send summary to Telegram: "X pagamentos agendados esta semana: [list]"

### 1.4 Daily Urgent Payment Check

New function `check_urgent_payments()` in `planning_loop.py` (runs daily):

1. Query PIs: outstanding > 0, due_date in (today, tomorrow), no linked Payment Entry
2. Alert via Telegram: "URGENTE: X pagamentos vencem hoje/amanha sem agendamento"
3. Include inline buttons to schedule each one

### 1.5 Agent Settings — New Fields

| Field | Type | Description |
|---|---|---|
| `auto_schedule_payments` | Check | Enable automatic payment scheduling on Mondays |
| `payment_schedule_day` | Select | Day of week to schedule (default Monday) |

---

## 2. Learning Loop

### 2.1 I8 Learning Pattern DocType

| Field | Type | Description |
|---|---|---|
| `action` | Data | Tool name (e.g., p2p-create_purchase_order) |
| `pattern_key` | Data | Context key (e.g., supplier:EDGARD MAKOTO...) |
| `consecutive_approvals` | Int | Current streak of approvals |
| `last_approval_date` | Date | Last approval date |
| `auto_approved_count` | Int | Times auto-approved by learning |
| `active` | Check | Enable/disable this pattern |

### 2.2 Integration in agent.py

In `_handle_tool_call`, before Decision Engine:
1. Build pattern_key from tool args (supplier name, action type)
2. Query I8 Learning Pattern for matching action + pattern_key
3. If consecutive_approvals >= settings.learning_approval_count → auto-approve
4. Always notify Telegram: "Auto-aprovado (aprendizado): [action description] (X aprovacoes anteriores)"

### 2.3 When human approves/rejects via Telegram

Approval:
1. Find or create I8 Learning Pattern for action + pattern_key
2. Increment consecutive_approvals
3. Update last_approval_date

Rejection:
1. Reset consecutive_approvals to 0

### 2.4 Agent Settings — New Fields

| Field | Type | Description |
|---|---|---|
| `learning_enabled` | Check | Enable learning loop |
| `learning_approval_count` | Int | Approvals before auto-approve (default 3) |

### 2.5 Telegram Notification

Every auto-approved action sends:
```
Auto-aprovado (aprendizado):
  Acao: Criar PO R$ 16.800,00
  Fornecedor: EDGARD MAKOTO AKAMINE
  Aprovacoes anteriores: 3
  [Ver no ERP](link)
```

---

## 3. Cleanup

- Delete I8 Supplier Profile DocType and related files
- Remove from workspace sidebar, tools, and context_builder
- Update follow_up_manager to read from Supplier custom fields instead
