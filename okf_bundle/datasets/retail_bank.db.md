---
type: Dataset
title: Retail Bank Database
description: Core PostgreSQL database for ClearBank retail banking operations — customer accounts, loan lifecycle, transactions, and compliance flags.
resource: postgresql://core-db.clearbank.internal:5432/retail_bank
tags: [banking, bfsi, retail, production, compliance]
timestamp: 2026-07-21T09:00:00Z
---

# Retail Bank Database

The ClearBank retail database is the system of record for all banking
operations. It stores customer KYC profiles, account balances, transaction
events, loan disbursements, EMI schedules, and compliance flag alerts.

## Tables

- [customers](../tables/customers.md) — one row per onboarded customer
- [accounts](../tables/accounts.md) — one row per bank account
- [transactions](../tables/transactions.md) — one row per financial event
- [loans](../tables/loans.md) — one row per loan application/disbursement
- [loan_payments](../tables/loan_payments.md) — one row per EMI installment
- [flags](../tables/flags.md) — one row per compliance or fraud alert

## Access

Production read replicas are available for analytics and reporting workloads.
Write access is restricted to the core banking microservice and the
loan-origination service. Direct writes to `transactions` are forbidden;
all entries are created by the ledger engine.

## Retention

- Transaction data retained for 10 years per RBI/BSA regulations.
- Customer PII data retained for 7 years post-account closure.
- Compliance flags retained for 5 years post-resolution.
