---
type: Metric
title: KYC Completion Rate
description: Percentage of active customers with verified KYC status. Regulatory minimum is 95%.
tags: [customers, kyc, kpi, compliance, regulatory]
timestamp: 2026-07-21T09:00:00Z
---

# Definition

KYC Completion Rate = (count of `active` customers with `kyc_status = 'verified'`) /
(count of all `active` customers) × 100

Customers with `status = 'inactive'` or `status = 'blocked'` are excluded
from both numerator and denominator — only the active customer base is measured.

# SQL

```sql
SELECT
    ROUND(
        100.0 * SUM(CASE WHEN kyc_status = 'verified' THEN 1 ELSE 0 END)
        / NULLIF(COUNT(*), 0),
        2
    ) AS kyc_completion_rate_pct,
    COUNT(*) AS total_active_customers,
    SUM(CASE WHEN kyc_status = 'verified' THEN 1 ELSE 0 END) AS verified_count,
    SUM(CASE WHEN kyc_status IN ('pending','expired','rejected') THEN 1 ELSE 0 END) AS unverified_count
FROM customers
WHERE status = 'active';
```

# Source Tables

- [customers](../tables/customers.md)

# Thresholds

| Level    | Value       |
|----------|-------------|
| Healthy  | >= 95%      |
| Warning  | 90% - 95%   |
| Critical | < 90%       |
