---
type: Table
title: Customers
description: One row per onboarded bank customer. Contains KYC status, risk tier, and identity information.
resource: postgresql://core-db.clearbank.internal:5432/retail_bank/public.customers
tags: [customers, kyc, PII, onboarding, compliance]
timestamp: 2026-07-21T09:00:00Z
---

# Schema

| Column          | Type      | Description                                                         |
|-----------------|-----------|---------------------------------------------------------------------|
| `customer_id`   | UUID      | Globally unique customer identifier.                                |
| `first_name`    | VARCHAR   | Customer's first name. **PII**                                      |
| `last_name`     | VARCHAR   | Customer's last name. **PII**                                       |
| `date_of_birth` | DATE      | Date of birth. **PII**                                              |
| `email`         | VARCHAR   | Contact email address. **PII** Unique constraint.                   |
| `phone`         | VARCHAR   | Mobile number. **PII**                                              |
| `kyc_status`    | ENUM      | One of: `verified`, `pending`, `rejected`, `expired`.              |
| `risk_tier`     | ENUM      | One of: `low`, `medium`, `high`. Set by risk engine on KYC.        |
| `onboarded_at`  | TIMESTAMP | When the customer completed onboarding.                             |
| `status`        | ENUM      | One of: `active`, `inactive`, `blocked`.                            |

# Business Rules

- Customers with `kyc_status = pending` or `rejected` cannot open new accounts
  or apply for loans. See [KYC Renewal runbook](../runbooks/kyc_renewal.md).
- Customers with `kyc_status = expired` have 30 days to re-verify before
  accounts are frozen. See [KYC Renewal runbook](../runbooks/kyc_renewal.md).
- `status = blocked` is set when a customer has an open `critical` severity
  [flag](./flags.md) for `fraud` or `aml`. All transactions are declined.
- PII columns require `role:pii_reader` database role for access.
- `risk_tier = high` customers require additional manual approval for loans
  above $10,000.

# Common Queries

**Customers with expired KYC:**
```sql
SELECT customer_id, first_name, last_name, kyc_status, onboarded_at
FROM customers
WHERE kyc_status = 'expired'
ORDER BY onboarded_at ASC;
```

**Blocked customers count by risk tier:**
```sql
SELECT risk_tier, COUNT(*) AS blocked_count
FROM customers
WHERE status = 'blocked'
GROUP BY risk_tier;
```

# Related Concepts

- [Accounts](./accounts.md)
- [Loans](./loans.md)
- [Flags](./flags.md)
- [KYC Renewal Runbook](../runbooks/kyc_renewal.md)
- [KYC Completion Rate](../metrics/kyc_completion_rate.md)
