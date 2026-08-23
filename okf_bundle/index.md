# Retail Banking Knowledge Bundle

This OKF bundle contains curated knowledge for the **ClearBank**
retail banking platform. It is the canonical source of truth for
AI agents operating on customer accounts, loans, transactions, and
compliance workflows.

## Datasets

* [Retail Bank Database](./datasets/retail_bank.db.md) - Core banking data store

## Tables

* [Customers](./tables/customers.md) - KYC-verified customer profiles
* [Accounts](./tables/accounts.md) - Savings, checking, and fixed-deposit accounts
* [Transactions](./tables/transactions.md) - All debit/credit/transfer events
* [Loans](./tables/loans.md) - Loan applications and lifecycle
* [Loan Payments](./tables/loan_payments.md) - EMI schedule and payment history
* [Flags](./tables/flags.md) - Fraud, AML, and compliance alerts

## Metrics

* [Loan Delinquency Rate](./metrics/loan_delinquency_rate.md) - % of active loans overdue
* [NPA Ratio](./metrics/npa_ratio.md) - Non-performing asset ratio
* [Transaction Success Rate](./metrics/transaction_success_rate.md) - % of transactions completed
* [KYC Completion Rate](./metrics/kyc_completion_rate.md) - % of customers with verified KYC

## Runbooks

* [AML Alert Investigation](./runbooks/aml_alert_investigation.md) - Anti-money laundering review steps
* [Loan Restructuring](./runbooks/loan_restructuring.md) - Workflow for restructuring delinquent loans
* [KYC Renewal](./runbooks/kyc_renewal.md) - Steps for expired KYC re-verification
