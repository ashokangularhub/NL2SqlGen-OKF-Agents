---
type: Metric
title: Transaction Success Rate
description: Percentage of transactions that completed successfully, excluding reversals. Target is >= 98%.
tags: [transactions, kpi, payments, uptime]
timestamp: 2026-07-21T09:00:00Z
---

# Definition

Transaction Success Rate = (count of transactions with `status = 'completed'`) /
(count of transactions with `status IN ('completed', 'failed')`) × 100

`pending` and `reversed` transactions are excluded from the denominator —
`pending` because they have not yet settled, and `reversed` because they
represent intentional corrections rather than failures.

# SQL

```sql
SELECT
    strftime('%Y-%m', txn_at) AS month,
    ROUND(
        100.0 * SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END)
        / NULLIF(SUM(CASE WHEN status IN ('completed', 'failed') THEN 1 ELSE 0 END), 0),
        2
    ) AS success_rate_pct,
    COUNT(*) AS total_transactions
FROM transactions
WHERE status IN ('completed', 'failed')
GROUP BY 1
ORDER BY 1 DESC;
```

# Source Tables

- [transactions](../tables/transactions.md)

# Thresholds

| Level    | Value      |
|----------|------------|
| Healthy  | >= 98%     |
| Warning  | 95% - 98%  |
| Critical | < 95%      |
