---
type: Metric
title: Loan Delinquency Rate
description: Percentage of active loans that have at least one overdue installment. Target is <= 5%.
tags: [loans, delinquency, kpi, credit-risk]
timestamp: 2026-07-21T09:00:00Z
---

# Definition

Loan Delinquency Rate = (count of `active` loans with one or more `overdue`
installments in [loan_payments](../tables/loan_payments.md)) /
(total count of `active` loans) × 100

Loans with `status IN ('closed', 'written_off', 'applied', 'approved')`
are excluded as they have no active repayment obligation.

# SQL

```sql
SELECT
    strftime('%Y-%m', lp.due_date) AS month,
    ROUND(
        100.0 * COUNT(DISTINCT CASE WHEN lp.status = 'overdue' THEN l.loan_id END)
        / NULLIF(COUNT(DISTINCT l.loan_id), 0),
        2
    ) AS delinquency_rate_pct
FROM loans l
JOIN loan_payments lp ON l.loan_id = lp.loan_id
WHERE l.status IN ('active', 'delinquent')
GROUP BY 1
ORDER BY 1 DESC;
```

# Source Tables

- [loans](../tables/loans.md)
- [loan_payments](../tables/loan_payments.md)

# Thresholds

| Level    | Value    |
|----------|----------|
| Healthy  | <= 5%    |
| Warning  | 5% - 10% |
| Critical | > 10%    |
