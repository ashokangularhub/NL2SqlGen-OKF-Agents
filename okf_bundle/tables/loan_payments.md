---
type: Table
title: Loan Payments
description: One row per EMI installment. Tracks payment due dates, amounts paid, and overdue status for every active loan.
resource: postgresql://core-db.clearbank.internal:5432/retail_bank/public.loan_payments
tags: [loans, emi, payments, delinquency]
timestamp: 2026-07-21T09:00:00Z
---

# Schema

| Column        | Type      | Description                                                           |
|---------------|-----------|-----------------------------------------------------------------------|
| `payment_id`  | UUID      | Globally unique payment identifier.                                   |
| `loan_id`     | UUID      | FK to [loans](./loans.md).                                            |
| `due_date`    | DATE      | Scheduled payment due date.                                           |
| `paid_at`     | TIMESTAMP | Actual timestamp of payment. NULL if not yet paid.                    |
| `amount_due`  | DECIMAL   | EMI amount due for this installment.                                  |
| `amount_paid` | DECIMAL   | Actual amount received. NULL if not yet paid.                         |
| `status`      | ENUM      | One of: `upcoming`, `paid`, `overdue`, `partial`, `waived`.          |

# Business Rules

- Installments become `overdue` if `paid_at IS NULL` and
  `due_date < CURRENT_DATE`.
- A `partial` payment counts as paid for the installment's count but
  the shortfall is added to the next installment's `amount_due`.
- `waived` installments are only set by the loan operations team as part
  of a [loan restructuring](../runbooks/loan_restructuring.md) decision.
- When **2 consecutive** installments transition to `overdue`, the parent
  [loan](./loans.md) `status` is updated to `delinquent` by the nightly
  batch job.
- Installments for `written_off` or `closed` loans retain their historical
  status and are excluded from all active KPI calculations.

# Common Queries

**All overdue installments with days overdue:**
```sql
SELECT lp.payment_id, lp.loan_id, lp.due_date, lp.amount_due,
       CAST(julianday('now') - julianday(lp.due_date) AS INTEGER) AS days_overdue
FROM loan_payments lp
WHERE lp.status = 'overdue'
ORDER BY days_overdue DESC;
```

**Installments due in the next 7 days:**
```sql
SELECT payment_id, loan_id, due_date, amount_due
FROM loan_payments
WHERE status = 'upcoming'
  AND due_date BETWEEN date('now') AND date('now', '+7 days')
ORDER BY due_date ASC;
```

# Related Concepts

- [Loans](./loans.md)
- [Loan Delinquency Rate](../metrics/loan_delinquency_rate.md)
- [Loan Restructuring Runbook](../runbooks/loan_restructuring.md)
