---
type: Metric
title: NPA Ratio
description: Non-Performing Asset ratio — outstanding balance of delinquent and written-off loans as a percentage of the total loan book. Regulatory target is <= 3%.
tags: [loans, npa, kpi, credit-risk, regulatory]
timestamp: 2026-07-21T09:00:00Z
---

# Definition

NPA Ratio = (sum of `outstanding_balance` for loans with
`status IN ('delinquent', 'written_off')`) /
(sum of `outstanding_balance` for ALL loans with
`status IN ('active', 'delinquent', 'written_off')`) × 100

A loan is classified as NPA once it has been `delinquent` for 90+ days
without a restructuring agreement per RBI prudential norms.

# SQL

```sql
SELECT
    ROUND(
        100.0 * SUM(CASE WHEN status IN ('delinquent', 'written_off')
                         THEN outstanding_balance ELSE 0 END)
        / NULLIF(SUM(CASE WHEN status IN ('active', 'delinquent', 'written_off')
                          THEN outstanding_balance ELSE 0 END), 0),
        2
    ) AS npa_ratio_pct,
    ROUND(SUM(CASE WHEN status IN ('delinquent','written_off')
                   THEN outstanding_balance ELSE 0 END), 2) AS npa_book_usd,
    ROUND(SUM(CASE WHEN status IN ('active','delinquent','written_off')
                   THEN outstanding_balance ELSE 0 END), 2) AS total_loan_book_usd
FROM loans;
```

# Source Tables

- [loans](../tables/loans.md)

# Thresholds

| Level    | Value  |
|----------|--------|
| Healthy  | <= 3%  |
| Warning  | 3% - 6% |
| Critical | > 6%   |
