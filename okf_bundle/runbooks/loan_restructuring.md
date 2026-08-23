---
type: Runbook
title: Loan Restructuring
description: Workflow for restructuring delinquent or at-risk loans to prevent write-off and enable customer recovery.
tags: [loans, delinquency, restructuring, credit-risk, workflow]
timestamp: 2026-07-21T09:00:00Z
---

# Loan Restructuring Workflow

## When Is This Runbook Triggered?

1. A [loan](../tables/loans.md) transitions to `status = 'delinquent'`
   (2 or more consecutive overdue installments in [loan_payments](../tables/loan_payments.md)).
2. A customer proactively requests restructuring due to financial hardship.
3. A loan has been `delinquent` for **60 days** — pre-emptive restructuring
   to avoid `written_off` status at 90 days.

## Eligibility

A loan is eligible for restructuring if:
- `loan.status = 'delinquent'` for fewer than **90 days**.
- The borrower has not had a previous restructuring on the same loan.
- The customer's [KYC](../tables/customers.md) is `verified` or `pending`
  (not `rejected` or `blocked`).

## Steps

### Step 1: Case Initiation
Loan operations team creates a restructuring case. Pull the full loan
profile and payment history:

```sql
SELECT l.loan_id, l.loan_type, l.principal, l.outstanding_balance,
       l.interest_rate, l.tenure_months, l.disbursed_at,
       COUNT(lp.payment_id) FILTER (WHERE lp.status = 'overdue') AS overdue_count,
       SUM(lp.amount_due)   FILTER (WHERE lp.status = 'overdue') AS total_overdue_amount
FROM loans l
JOIN loan_payments lp ON l.loan_id = lp.loan_id
WHERE l.loan_id = :loan_id
GROUP BY l.loan_id;
```

### Step 2: Customer Financial Review
Assess the customer's current repayment capacity:
- Review last 6 months of account [transactions](../tables/transactions.md)
  for income patterns.
- Check for any open [flags](../tables/flags.md) on the customer.
- Obtain updated income and expense documentation from the customer.

### Step 3: Restructuring Options
Propose one or more of the following options:

| Option               | Description                                          |
|----------------------|------------------------------------------------------|
| **Tenure Extension** | Extend `tenure_months` to reduce monthly EMI.        |
| **Rate Reduction**   | Temporarily reduce `interest_rate` for 6–12 months. |
| **Installment Waiver** | Waive 1–2 overdue installments (`status = 'waived'`). Only with senior approval. |
| **Moratorium**       | Pause installments for 1–3 months; capitalise interest. |

### Step 4: Approval
- Up to **$5,000 overdue**: Loan operations team can approve directly.
- **$5,000–$25,000 overdue**: Credit committee approval required.
- **> $25,000 overdue**: Chief Credit Officer approval required.

### Step 5: Update Records
Upon approval:
- Update [loan](../tables/loans.md) with revised `interest_rate`,
  `tenure_months`, and reset `status` → `active`.
- Create new [loan_payment](../tables/loan_payments.md) rows for the
  revised schedule.
- Waive outstanding overdue installments (`status = 'waived'`).
- Close any `delinquency` [flags](../tables/flags.md) on this loan.

## SLA

Restructuring cases must be reviewed and decided within **15 business days**
of initiation. Emergency hardship cases within **5 business days**.

## Related

- [Loans Table](../tables/loans.md)
- [Loan Payments Table](../tables/loan_payments.md)
- [Loan Delinquency Rate](../metrics/loan_delinquency_rate.md)
- [NPA Ratio](../metrics/npa_ratio.md)
