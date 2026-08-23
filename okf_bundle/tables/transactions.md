---
type: Table
title: Transactions
description: One row per financial event (debit, credit, or transfer). Immutable ledger entries created by the banking engine.
resource: postgresql://core-db.clearbank.internal:5432/retail_bank/public.transactions
tags: [transactions, ledger, payments, banking]
timestamp: 2026-07-21T09:00:00Z
---

# Schema

| Column                | Type      | Description                                                        |
|-----------------------|-----------|--------------------------------------------------------------------|
| `txn_id`              | UUID      | Globally unique transaction identifier.                            |
| `account_id`          | UUID      | FK to [accounts](./accounts.md).                                   |
| `txn_type`            | ENUM      | One of: `credit`, `debit`, `transfer`.                             |
| `amount`              | DECIMAL   | Transaction amount in USD. Always positive.                        |
| `status`              | ENUM      | One of: `pending`, `completed`, `failed`, `reversed`.             |
| `channel`             | ENUM      | One of: `atm`, `mobile`, `branch`, `online`, `pos`.               |
| `txn_at`              | TIMESTAMP | When the transaction was initiated.                                |
| `description`         | VARCHAR   | Free-text note or merchant name.                                   |
| `counterparty_account`| VARCHAR   | Target account ID for transfers. NULL for debit/credit.            |

# Business Rules

- Transactions are **immutable** — rows are never updated after insert. Failed
  or erroneous transactions are corrected via a new reversal row with
  `txn_type = credit` and reference to the original `txn_id` in `description`.
- A single account debit of more than **$10,000** in a 24-hour window
  automatically raises a [flag](./flags.md) with `flag_reason = suspicious_txn`.
- More than **5 failed** transactions from the same `account_id` within
  1 hour triggers a `velocity_breach` [flag](./flags.md).
- `transfer` transactions create **two** rows: a debit on the source account
  and a credit on the `counterparty_account`.
- `status = reversed` entries restore the balance but remain in the ledger.

# Common Queries

**Failed transactions in the last 24 hours:**
```sql
SELECT txn_id, account_id, amount, channel, txn_at
FROM transactions
WHERE status = 'failed'
  AND txn_at >= datetime('now', '-1 day')
ORDER BY txn_at DESC;
```

**Large debit transactions above $10,000:**
```sql
SELECT txn_id, account_id, amount, channel, txn_at, description
FROM transactions
WHERE txn_type = 'debit'
  AND amount > 10000
  AND status = 'completed'
ORDER BY amount DESC
LIMIT 20;
```

# Related Concepts

- [Accounts](./accounts.md)
- [Flags](./flags.md)
- [AML Alert Investigation](../runbooks/aml_alert_investigation.md)
- [Transaction Success Rate](../metrics/transaction_success_rate.md)
