---
type: Table
title: Accounts
description: One row per bank account. Covers savings, checking, and fixed-deposit account types with balance and status tracking.
resource: postgresql://core-db.clearbank.internal:5432/retail_bank/public.accounts
tags: [accounts, balance, banking, ledger]
timestamp: 2026-07-21T09:00:00Z
---

# Schema

| Column         | Type      | Description                                                          |
|----------------|-----------|----------------------------------------------------------------------|
| `account_id`   | UUID      | Globally unique account identifier.                                  |
| `customer_id`  | UUID      | FK to [customers](./customers.md).                                   |
| `account_type` | ENUM      | One of: `savings`, `checking`, `fixed_deposit`.                      |
| `balance`      | DECIMAL   | Current available balance in USD.                                    |
| `currency`     | VARCHAR   | ISO 4217 currency code. Default `USD`.                               |
| `status`       | ENUM      | One of: `active`, `frozen`, `closed`, `dormant`.                     |
| `opened_at`    | TIMESTAMP | When the account was opened.                                         |
| `closed_at`    | TIMESTAMP | When the account was closed. NULL if still open.                     |

# Business Rules

- A customer may hold at most **one** `savings` account at a time.
- A customer may hold at most **three** `checking` accounts simultaneously.
- `fixed_deposit` accounts have a locked balance; withdrawals trigger early
  exit penalties defined in the product master (not stored here).
- Accounts are set to `frozen` when the owning customer receives a `high` or
  `critical` severity [flag](./flags.md) of type `aml` or `fraud`.
- Accounts become `dormant` after **24 months** of zero transaction activity.
  Dormant accounts require a branch re-activation request.
- Negative balance is not permitted. Debit transactions that would result in
  a negative balance are rejected by the ledger engine.

# Common Queries

**All active accounts with zero balance:**
```sql
SELECT account_id, customer_id, account_type, opened_at
FROM accounts
WHERE status = 'active' AND balance = 0
ORDER BY opened_at ASC;
```

**Dormant accounts eligible for closure:**
```sql
SELECT a.account_id, a.customer_id, a.balance, a.status
FROM accounts a
WHERE a.status = 'dormant'
  AND a.balance = 0;
```

# Related Concepts

- [Customers](./customers.md)
- [Transactions](./transactions.md)
- [Flags](./flags.md)
- [Transaction Success Rate](../metrics/transaction_success_rate.md)
