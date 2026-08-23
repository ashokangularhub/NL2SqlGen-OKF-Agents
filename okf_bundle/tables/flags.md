---
type: Table
title: Flags
description: One row per compliance or fraud alert raised against a customer, account, transaction, or loan. Tracks severity, reason, and resolution.
resource: postgresql://core-db.clearbank.internal:5432/retail_bank/public.flags
tags: [flags, fraud, aml, compliance, risk]
timestamp: 2026-07-21T09:00:00Z
---

# Schema

| Column          | Type      | Description                                                           |
|-----------------|-----------|-----------------------------------------------------------------------|
| `flag_id`       | UUID      | Globally unique flag identifier.                                      |
| `entity_type`   | ENUM      | One of: `customer`, `account`, `transaction`, `loan`.                |
| `entity_id`     | UUID      | ID of the flagged entity (customer_id, account_id, etc.).            |
| `flag_reason`   | ENUM      | One of: `fraud`, `aml`, `kyc_expired`, `suspicious_txn`, `velocity_breach`, `delinquency`. |
| `severity`      | ENUM      | One of: `low`, `medium`, `high`, `critical`.                         |
| `raised_at`     | TIMESTAMP | When the flag was created (system or manual).                        |
| `resolved_at`   | TIMESTAMP | When the flag was resolved. NULL if still open.                      |
| `status`        | ENUM      | One of: `open`, `under_review`, `resolved`, `false_positive`.        |

# Business Rules

- `critical` severity flags on a `customer` entity automatically set the
  customer's `status = blocked` and freeze all linked [accounts](./accounts.md).
- `aml` flags must be reviewed within **72 hours** of being raised per
  regulatory SLA. See [AML Alert Investigation](../runbooks/aml_alert_investigation.md).
- `fraud` flags with `critical` severity must be reviewed within **24 hours**.
- A flag cannot be `resolved` without a `resolved_at` timestamp and an
  operations team member ID (stored in the audit log, not this table).
- `velocity_breach` flags are auto-raised by the transaction engine when
  more than 5 failed transactions occur from the same account within 1 hour.
  See [transactions](./transactions.md) for the trigger rule.
- `false_positive` flags do not affect the entity's status and are retained
  for model training purposes.

# Common Queries

**All open critical flags:**
```sql
SELECT flag_id, entity_type, entity_id, flag_reason, raised_at
FROM flags
WHERE severity = 'critical'
  AND status = 'open'
ORDER BY raised_at ASC;
```

**AML flags overdue for review (> 72 hours):**
```sql
SELECT flag_id, entity_id, raised_at,
       ROUND((julianday('now') - julianday(raised_at)) * 24, 1) AS hours_open
FROM flags
WHERE flag_reason = 'aml'
  AND status IN ('open', 'under_review')
  AND raised_at < datetime('now', '-3 hours', '-72 hours')
ORDER BY hours_open DESC;
```

# Related Concepts

- [Customers](./customers.md)
- [Accounts](./accounts.md)
- [Transactions](./transactions.md)
- [AML Alert Investigation](../runbooks/aml_alert_investigation.md)
