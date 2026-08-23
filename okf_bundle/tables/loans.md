---
type: Table
title: Loans
description: One row per loan application. Tracks loan type, principal, outstanding balance, interest rate, and lifecycle status.
resource: postgresql://core-db.clearbank.internal:5432/retail_bank/public.loans
tags: [loans, credit, npa, delinquency, banking]
timestamp: 2026-07-21T09:00:00Z
---

# Schema

| Column               | Type      | Description                                                          |
|----------------------|-----------|----------------------------------------------------------------------|
| `loan_id`            | UUID      | Globally unique loan identifier.                                     |
| `customer_id`        | UUID      | FK to [customers](./customers.md).                                   |
| `loan_type`          | ENUM      | One of: `personal`, `home`, `auto`, `education`.                    |
| `principal`          | DECIMAL   | Original sanctioned loan amount in USD.                             |
| `outstanding_balance`| DECIMAL   | Remaining unpaid principal + accrued interest.                      |
| `interest_rate`      | DECIMAL   | Annual interest rate as a decimal (e.g., 0.085 = 8.5% p.a.).       |
| `tenure_months`      | INTEGER   | Total repayment period in months.                                   |
| `disbursed_at`       | DATE      | Date funds were released to the customer. NULL if not yet approved. |
| `maturity_date`      | DATE      | Date the loan is fully due. NULL until disbursement.                |
| `status`             | ENUM      | One of: `applied`, `approved`, `active`, `delinquent`, `closed`, `written_off`. |

# Business Rules

- A customer with `kyc_status != 'verified'` cannot have a loan approved.
  See [customers](./customers.md) and [KYC Renewal](../runbooks/kyc_renewal.md).
- Loans transition to `delinquent` when **2 or more consecutive** EMI
  installments in [loan_payments](./loan_payments.md) are `overdue`.
- Loans transition to `written_off` when they remain `delinquent` for
  **90 days** without a restructuring agreement.
- Customers with `risk_tier = high` are capped at a maximum principal of
  **$10,000** per personal loan.
- `home` loans require a property valuation document (stored externally).
- The EMI is computed as:
  `EMI = P × r × (1+r)^n / ((1+r)^n - 1)` where `r = interest_rate / 12`
  and `n = tenure_months`.

# Common Queries

**Delinquent loans ordered by outstanding balance:**
```sql
SELECT loan_id, customer_id, loan_type, outstanding_balance,
       interest_rate, disbursed_at
FROM loans
WHERE status = 'delinquent'
ORDER BY outstanding_balance DESC;
```

**Loans approaching maturity in 30 days:**
```sql
SELECT loan_id, customer_id, loan_type, outstanding_balance, maturity_date
FROM loans
WHERE status = 'active'
  AND maturity_date <= date('now', '+30 days')
ORDER BY maturity_date ASC;
```

# Related Concepts

- [Customers](./customers.md)
- [Loan Payments](./loan_payments.md)
- [Flags](./flags.md)
- [Loan Delinquency Rate](../metrics/loan_delinquency_rate.md)
- [NPA Ratio](../metrics/npa_ratio.md)
- [Loan Restructuring Runbook](../runbooks/loan_restructuring.md)
