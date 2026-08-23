"""
seed_database.py — Create sample SQLite database for ClearBank Retail Banking.

Creates retail_bank.db with realistic synthetic data matching the OKF bundle
schemas exactly: customers, accounts, transactions, loans, loan_payments, flags.

Usage:
    python src/seed_database.py
"""

import sqlite3
import uuid
import random
from datetime import datetime, timedelta
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "retail_bank.db")

# ── Schema (mirrors OKF bundle tables exactly) ───────────────────────

CREATE_CUSTOMERS = """
CREATE TABLE IF NOT EXISTS customers (
    customer_id   TEXT PRIMARY KEY,
    first_name    TEXT NOT NULL,
    last_name     TEXT NOT NULL,
    date_of_birth DATE NOT NULL,
    email         TEXT NOT NULL UNIQUE,
    phone         TEXT,
    kyc_status    TEXT NOT NULL CHECK(kyc_status IN ('verified','pending','rejected','expired')),
    risk_tier     TEXT NOT NULL CHECK(risk_tier IN ('low','medium','high')),
    onboarded_at  TIMESTAMP NOT NULL,
    status        TEXT NOT NULL CHECK(status IN ('active','inactive','blocked'))
);
"""

CREATE_ACCOUNTS = """
CREATE TABLE IF NOT EXISTS accounts (
    account_id   TEXT PRIMARY KEY,
    customer_id  TEXT NOT NULL REFERENCES customers(customer_id),
    account_type TEXT NOT NULL CHECK(account_type IN ('savings','checking','fixed_deposit')),
    balance      REAL NOT NULL DEFAULT 0,
    currency     TEXT NOT NULL DEFAULT 'USD',
    status       TEXT NOT NULL CHECK(status IN ('active','frozen','closed','dormant')),
    opened_at    TIMESTAMP NOT NULL,
    closed_at    TIMESTAMP
);
"""

CREATE_TRANSACTIONS = """
CREATE TABLE IF NOT EXISTS transactions (
    txn_id               TEXT PRIMARY KEY,
    account_id           TEXT NOT NULL REFERENCES accounts(account_id),
    txn_type             TEXT NOT NULL CHECK(txn_type IN ('credit','debit','transfer')),
    amount               REAL NOT NULL,
    status               TEXT NOT NULL CHECK(status IN ('pending','completed','failed','reversed')),
    channel              TEXT NOT NULL CHECK(channel IN ('atm','mobile','branch','online','pos')),
    txn_at               TIMESTAMP NOT NULL,
    description          TEXT,
    counterparty_account TEXT
);
"""

CREATE_LOANS = """
CREATE TABLE IF NOT EXISTS loans (
    loan_id             TEXT PRIMARY KEY,
    customer_id         TEXT NOT NULL REFERENCES customers(customer_id),
    loan_type           TEXT NOT NULL CHECK(loan_type IN ('personal','home','auto','education')),
    principal           REAL NOT NULL,
    outstanding_balance REAL NOT NULL,
    interest_rate       REAL NOT NULL,
    tenure_months       INTEGER NOT NULL,
    disbursed_at        DATE,
    maturity_date       DATE,
    status              TEXT NOT NULL CHECK(status IN
        ('applied','approved','active','delinquent','closed','written_off'))
);
"""

CREATE_LOAN_PAYMENTS = """
CREATE TABLE IF NOT EXISTS loan_payments (
    payment_id  TEXT PRIMARY KEY,
    loan_id     TEXT NOT NULL REFERENCES loans(loan_id),
    due_date    DATE NOT NULL,
    paid_at     TIMESTAMP,
    amount_due  REAL NOT NULL,
    amount_paid REAL,
    status      TEXT NOT NULL CHECK(status IN ('upcoming','paid','overdue','partial','waived'))
);
"""

CREATE_FLAGS = """
CREATE TABLE IF NOT EXISTS flags (
    flag_id      TEXT PRIMARY KEY,
    entity_type  TEXT NOT NULL CHECK(entity_type IN ('customer','account','transaction','loan')),
    entity_id    TEXT NOT NULL,
    flag_reason  TEXT NOT NULL CHECK(flag_reason IN
        ('fraud','aml','kyc_expired','suspicious_txn','velocity_breach','delinquency')),
    severity     TEXT NOT NULL CHECK(severity IN ('low','medium','high','critical')),
    raised_at    TIMESTAMP NOT NULL,
    resolved_at  TIMESTAMP,
    status       TEXT NOT NULL CHECK(status IN ('open','under_review','resolved','false_positive'))
);
"""

# ── Sample Data Pools ────────────────────────────────────────────────

FIRST_NAMES = ["James", "Maria", "David", "Sarah", "Michael", "Emma", "Robert", "Olivia",
               "William", "Sophia", "Richard", "Isabella", "Joseph", "Mia", "Thomas", "Charlotte"]
LAST_NAMES = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis",
              "Wilson", "Taylor", "Anderson", "Thomas", "Jackson", "White", "Harris", "Martin"]
US_STATES = ["CA", "TX", "NY", "FL", "IL", "PA", "OH",
             "GA", "NC", "MI", "NJ", "VA", "WA", "AZ", "MA"]
CHANNELS = ["mobile", "online", "atm", "branch", "pos"]
LOAN_TYPES = ["personal", "home", "auto", "education"]
TXN_DESCS = ["Grocery store", "Utility bill", "Salary deposit", "ATM withdrawal",
             "Online purchase", "Restaurant", "Fuel", "Insurance premium",
             "Rent payment", "Transfer to savings"]


def uid() -> str:
    return str(uuid.uuid4())


def rand_date(start_days_ago: int, end_days_ago: int = 0) -> datetime:
    delta = random.randint(end_days_ago, start_days_ago)
    return datetime.now() - timedelta(days=delta)


def seed(conn: sqlite3.Connection):
    cur = conn.cursor()
    now = datetime.now()

    # ── Customers ────────────────────────────────────────────────────
    customers = []
    kyc_pool = ["verified"] * 12 + ["expired"] * \
        3 + ["pending"] * 3 + ["rejected"] * 2
    risk_pool = ["low"] * 10 + ["medium"] * 7 + ["high"] * 3
    status_pool = ["active"] * 17 + ["blocked"] * 2 + ["inactive"] * 1

    for i in range(80):
        cid = uid()
        fn = random.choice(FIRST_NAMES)
        ln = random.choice(LAST_NAMES)
        dob = rand_date(60 * 365, 25 * 365).strftime("%Y-%m-%d")
        email = f"{fn.lower()}.{ln.lower()}{i}@example.com"
        phone = f"+1-555-{random.randint(1000, 9999)}"
        kyc = random.choice(kyc_pool)
        risk = random.choice(risk_pool)
        onb = rand_date(1825, 30).strftime("%Y-%m-%d %H:%M:%S")
        stat = "blocked" if kyc == "rejected" else random.choice(status_pool)
        customers.append(
            (cid, fn, ln, dob, email, phone, kyc, risk, onb, stat))

    cur.executemany(
        "INSERT INTO customers VALUES (?,?,?,?,?,?,?,?,?,?)", customers
    )

    # ── Accounts ─────────────────────────────────────────────────────
    accounts = []
    for cid, *_, cstat in customers:
        num_accounts = random.randint(1, 2)
        types_used = set()
        for _ in range(num_accounts):
            atype = random.choice(["savings", "checking", "fixed_deposit"])
            if atype == "savings" and "savings" in types_used:
                atype = "checking"
            types_used.add(atype)
            balance = round(random.uniform(0, 50000), 2)
            astatus = "frozen" if cstat == "blocked" else random.choice(
                ["active"] * 14 + ["dormant"] * 2 + ["closed"] * 1
            )
            opened = rand_date(1825, 10).strftime("%Y-%m-%d %H:%M:%S")
            closed = rand_date(10, 1).strftime(
                "%Y-%m-%d %H:%M:%S") if astatus == "closed" else None
            accounts.append((uid(), cid, atype, balance,
                            "USD", astatus, opened, closed))

    cur.executemany(
        "INSERT INTO accounts VALUES (?,?,?,?,?,?,?,?)", accounts
    )

    # ── Transactions ─────────────────────────────────────────────────
    active_accounts = [a for a in accounts if a[5] == "active"]
    transactions = []
    for _ in range(500):
        acc = random.choice(active_accounts)
        aid = acc[0]
        ttype = random.choice(["credit", "debit", "debit", "transfer"])
        amount = round(random.uniform(10, 15000), 2)
        status = random.choice(
            ["completed"] * 14 + ["failed"] * 2 + ["reversed"] * 1 + ["pending"] * 1)
        channel = random.choice(CHANNELS)
        txn_at = rand_date(365, 0).strftime("%Y-%m-%d %H:%M:%S")
        desc = random.choice(TXN_DESCS)
        counter = random.choice(active_accounts)[
            0] if ttype == "transfer" else None
        transactions.append(
            (uid(), aid, ttype, amount, status, channel, txn_at, desc, counter))

    cur.executemany(
        "INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?,?)", transactions
    )

    # ── Loans ────────────────────────────────────────────────────────
    loans = []
    verified_customers = [c for c in customers if c[6] == "verified"]
    loan_statuses = ["active"] * 8 + ["delinquent"] * 2 + ["closed"] * 3 + \
                    ["written_off"] * 1 + ["applied"] * 1 + ["approved"] * 1

    for i in range(60):
        cust = random.choice(verified_customers)
        cid = cust[0]
        ltype = random.choice(LOAN_TYPES)
        principal = round(random.uniform(1000, 50000), 2)
        rate = round(random.uniform(0.06, 0.18), 4)
        tenure = random.choice([12, 24, 36, 48, 60])
        disbursed = rand_date(720, 30).strftime("%Y-%m-%d")
        maturity = (datetime.strptime(disbursed, "%Y-%m-%d") +
                    timedelta(days=tenure * 30)).strftime("%Y-%m-%d")
        stat = random.choice(loan_statuses)
        paid_frac = random.uniform(0.1, 0.9) if stat in ("active", "delinquent") else \
            (1.0 if stat == "closed" else 0.0)
        outstanding = round(principal * (1 - paid_frac),
                            2) if stat != "written_off" else round(principal * 0.7, 2)
        loans.append((uid(), cid, ltype, principal, outstanding,
                     rate, tenure, disbursed, maturity, stat))

    cur.executemany(
        "INSERT INTO loans VALUES (?,?,?,?,?,?,?,?,?,?)", loans
    )

    # ── Loan Payments ────────────────────────────────────────────────
    loan_payments = []
    for loan_row in loans:
        lid, _, _, principal, _, rate, tenure, disbursed_str, _, lstatus = loan_row
        disbursed_dt = datetime.strptime(disbursed_str, "%Y-%m-%d")
        r = rate / 12
        emi = round(principal * r * (1 + r) **
                    tenure / ((1 + r) ** tenure - 1), 2)

        installments_to_create = min(tenure, 12)
        for i in range(installments_to_create):
            due = (disbursed_dt + timedelta(days=30 * (i + 1))
                   ).strftime("%Y-%m-%d")
            due_dt = datetime.strptime(due, "%Y-%m-%d")
            is_past = due_dt < now

            if lstatus == "closed":
                pstatus = "paid"
                paid_at = (due_dt + timedelta(days=random.randint(0, 3))
                           ).strftime("%Y-%m-%d %H:%M:%S")
                paid_amt = emi
            elif lstatus == "delinquent" and i >= installments_to_create - 2 and is_past:
                pstatus = "overdue"
                paid_at = None
                paid_amt = None
            elif lstatus == "written_off" and is_past:
                pstatus = "overdue"
                paid_at = None
                paid_amt = None
            elif is_past:
                pstatus = random.choice(["paid"] * 9 + ["overdue"] * 1)
                paid_at = (due_dt + timedelta(days=random.randint(0, 5))).strftime("%Y-%m-%d %H:%M:%S") \
                    if pstatus == "paid" else None
                paid_amt = emi if pstatus == "paid" else None
            else:
                pstatus = "upcoming"
                paid_at = None
                paid_amt = None

            loan_payments.append(
                (uid(), lid, due, paid_at, emi, paid_amt, pstatus))

    cur.executemany(
        "INSERT INTO loan_payments VALUES (?,?,?,?,?,?,?)", loan_payments
    )

    # ── Flags ────────────────────────────────────────────────────────
    flags = []
    flag_reasons = ["fraud", "aml", "kyc_expired",
                    "suspicious_txn", "velocity_breach", "delinquency"]
    flag_sevs = ["low", "medium", "high", "critical"]
    flag_statuses = ["open", "under_review", "resolved", "false_positive"]

    # AML / fraud flags on customers
    for cust in random.sample(customers, 12):
        reason = random.choice(flag_reasons)
        severity = random.choice(flag_sevs)
        raised = rand_date(180, 1).strftime("%Y-%m-%d %H:%M:%S")
        fstatus = random.choice(flag_statuses)
        resolved = rand_date(1, 0).strftime("%Y-%m-%d %H:%M:%S") \
            if fstatus in ("resolved", "false_positive") else None
        flags.append(
            (uid(), "customer", cust[0], reason, severity, raised, resolved, fstatus))

    # Suspicious txn flags
    for txn in random.sample(transactions, 8):
        raised = rand_date(90, 1).strftime("%Y-%m-%d %H:%M:%S")
        fstatus = random.choice(flag_statuses)
        resolved = rand_date(1, 0).strftime("%Y-%m-%d %H:%M:%S") \
            if fstatus in ("resolved", "false_positive") else None
        flags.append((uid(), "transaction", txn[0], "suspicious_txn",
                      random.choice(["medium", "high", "critical"]), raised, resolved, fstatus))

    # Delinquency flags on loans
    delinquent_loans = [l for l in loans if l[9] == "delinquent"]
    for loan in delinquent_loans:
        raised = rand_date(60, 1).strftime("%Y-%m-%d %H:%M:%S")
        fstatus = random.choice(["open", "under_review"])
        flags.append(
            (uid(), "loan", loan[0], "delinquency", "high", raised, None, fstatus))

    cur.executemany(
        "INSERT INTO flags VALUES (?,?,?,?,?,?,?,?)", flags
    )

    conn.commit()
    print(f"  ✓ customers:     {len(customers)}")
    print(f"  ✓ accounts:      {len(accounts)}")
    print(f"  ✓ transactions:  {len(transactions)}")
    print(f"  ✓ loans:         {len(loans)}")
    print(f"  ✓ loan_payments: {len(loan_payments)}")
    print(f"  ✓ flags:         {len(flags)}")


def main():
    print(f"\n{'='*50}")
    print("  ClearBank — Seed Database")
    print(f"  Path: {DB_PATH}")
    print(f"{'='*50}\n")

    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
        print("  Removed existing database.\n")

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")

    for ddl in [CREATE_CUSTOMERS, CREATE_ACCOUNTS, CREATE_TRANSACTIONS,
                CREATE_LOANS, CREATE_LOAN_PAYMENTS, CREATE_FLAGS]:
        conn.execute(ddl)

    seed(conn)
    conn.close()

    size_kb = os.path.getsize(DB_PATH) // 1024
    print(f"\n  Database created: {DB_PATH} ({size_kb} KB)")


if __name__ == "__main__":
    main()
