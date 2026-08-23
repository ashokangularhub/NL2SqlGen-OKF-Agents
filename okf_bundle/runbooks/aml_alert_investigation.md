---
type: Runbook
title: AML Alert Investigation
description: Step-by-step workflow for investigating anti-money laundering flags raised on customer accounts or transactions.
tags: [aml, compliance, fraud, flags, workflow]
timestamp: 2026-07-21T09:00:00Z
---

# AML Alert Investigation Workflow

## When Is This Runbook Triggered?

1. A [flag](../tables/flags.md) with `flag_reason = 'aml'` is raised (automated or manual).
2. A [transaction](../tables/transactions.md) debit exceeds **$10,000** in a 24-hour
   window on a single account.
3. A pattern of structured transactions is detected (multiple transactions just
   below $10,000 — "structuring" or "smurfing").
4. A customer is matched against a sanctions or PEP (Politically Exposed Person) list.

## SLA

- `high` severity AML flags must be reviewed within **72 hours**.
- `critical` severity AML flags must be reviewed within **24 hours**.
- Breach of these SLAs must be reported to the compliance officer.

## Steps

### Step 1: Acknowledge the Flag
Assign the flag to an AML analyst in the case management system.
Update [flag](../tables/flags.md) `status` from `open` → `under_review`.
Record the analyst ID and start time in the audit log.

### Step 2: Profile the Customer
Review the [customer](../tables/customers.md) record:
- Confirm `kyc_status = 'verified'`. If not, escalate immediately.
- Note the `risk_tier`. `high` risk customers require senior analyst review.
- Check for other open flags on the same `customer_id`.

### Step 3: Transaction Pattern Analysis
Query [transactions](../tables/transactions.md) for the flagged account:
- Review the last **90 days** of transactions.
- Identify unusual counterparties, amounts, or channel changes.
- Flag structuring patterns: multiple transactions between $9,000–$9,999.

```sql
SELECT txn_id, amount, channel, txn_at, description, counterparty_account
FROM transactions
WHERE account_id = :flagged_account_id
  AND txn_at >= date('now', '-90 days')
ORDER BY txn_at DESC;
```

### Step 4: Decision
- **Clear:** No suspicious activity. Set flag `status = 'false_positive'`.
  Record justification in audit log.
- **Escalate:** Suspicious but inconclusive. Escalate to senior AML team.
  Freeze the [account](../tables/accounts.md) (`status = 'frozen'`).
- **Confirm & Report:** Confirmed suspicious activity. File a Suspicious
  Activity Report (SAR) with FinCEN. Set customer `status = 'blocked'`.
  Raise additional `critical` flags on all linked accounts.

### Step 5: Close and Document
Update flag `status` to `resolved` with `resolved_at` timestamp.
Document the decision and evidence trail in the case management system.
Notify the compliance officer of any SAR filings.

## Related

- [Flags Table](../tables/flags.md)
- [Transactions Table](../tables/transactions.md)
- [Customers Table](../tables/customers.md)
- [Transaction Success Rate](../metrics/transaction_success_rate.md)
