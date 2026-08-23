---
type: Runbook
title: KYC Renewal
description: Process for re-verifying customers whose KYC has expired or been rejected, to restore full account access.
tags: [kyc, customers, compliance, onboarding, workflow]
timestamp: 2026-07-21T09:00:00Z
---

# KYC Renewal Workflow

## When Is This Runbook Triggered?

1. A [customer](../tables/customers.md) has `kyc_status = 'expired'`
   (KYC documents older than 2 years).
2. A customer has `kyc_status = 'rejected'` after initial or previous
   verification attempt.
3. A customer is flagged with `flag_reason = 'kyc_expired'` by the
   nightly compliance batch job.

## Impact of Expired KYC

| Customer Action             | Allowed?                                 |
|-----------------------------|------------------------------------------|
| View account balance        | Yes                                      |
| Receive credits (inbound)   | Yes (30-day grace period)                |
| Make debits or transfers    | No — blocked after 30-day grace period   |
| Apply for new loan          | No                                       |
| Open new account            | No                                       |

After **30 days** of `kyc_status = 'expired'` without renewal,
all linked [accounts](../tables/accounts.md) are set to `frozen`.

## Steps

### Step 1: Customer Notification
System sends automated notification via email and SMS at:
- **30 days before** expiry: first reminder.
- **7 days before** expiry: urgent reminder.
- **Day 0** (expiry): account restriction notice.
- **Day 15**: final warning before account freeze.

### Step 2: Document Collection
Customer submits updated KYC documents via the mobile app or branch:
- Government-issued photo ID (passport, driver's licence, national ID).
- Proof of address (utility bill or bank statement, less than 3 months old).
- For `risk_tier = high` customers: additional source-of-funds declaration.

### Step 3: Document Verification
- **Automated check**: OCR + document authenticity validation (< 5 minutes).
- **Manual review**: triggered if automated check fails or `risk_tier = high`.
  Manual review SLA: **48 hours**.

### Step 4: Decision
- **Verified**: Update [customer](../tables/customers.md) `kyc_status = 'verified'`.
  Unfreeze linked accounts (`status = 'active'`). Close the `kyc_expired` flag.
- **Rejected**: Update `kyc_status = 'rejected'`. Customer may appeal within
  30 days with alternative documents. After 2 rejections, escalate to the
  compliance team for potential account closure.

### Step 5: Record Update
- Set [customer](../tables/customers.md) `kyc_status` appropriately.
- Resolve the [flag](../tables/flags.md) (`status = 'resolved'` or `'false_positive'`).
- If accounts were frozen, restore to `status = 'active'`.
- Log the verification outcome and document references in the audit system.

## SLA

- Automated KYC check: **< 5 minutes**.
- Manual review: **48 hours**.
- Branch-assisted renewal: **same day** (within branch hours).

## Related

- [Customers Table](../tables/customers.md)
- [Accounts Table](../tables/accounts.md)
- [Flags Table](../tables/flags.md)
- [KYC Completion Rate](../metrics/kyc_completion_rate.md)
