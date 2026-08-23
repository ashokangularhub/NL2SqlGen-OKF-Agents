"""
okf_agent_with_tool.py — OKF + Database Tool Agent for ClearBank Retail Banking

Full production pattern:

  ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
  │  OKF Bundle  │     │   Agent/LLM  │     │  DB Tool     │
  │  (Knowledge) │────▶│  (Reasoning) │────▶│  (Execution) │
  │              │     │              │     │              │
  │  - schemas   │     │  - reads OKF │     │  - runs SQL  │
  │  - rules     │     │  - builds SQL│     │  - returns   │
  │  - metrics   │     │  - calls tool│     │    results   │
  │  - runbooks  │     │  - answers   │     │              │
  └──────────────┘     └──────────────┘     └──────────────┘

Usage:
    python src/seed_database.py          # seed DB first
    python src/okf_agent_with_tool.py    # run agent (mock mode)
    ANTHROPIC_API_KEY=sk-... python src/okf_agent_with_tool.py
"""

from db_tool import DatabaseTool, QueryResult
from okf_parser import BundleNavigator, Concept
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))

BUNDLE_PATH = os.path.join(os.path.dirname(__file__), "..", "okf_bundle")
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "retail_bank.db")

# ── Section Router ────────────────────────────────────────────────────

SECTION_KEYWORDS = {
    "Datasets": ["database", "db", "data store", "retention", "storage"],
    "Tables": [
        "table", "schema", "column", "query", "sql", "customers", "accounts",
        "transactions", "loans", "payments", "flags", "balance", "emi",
        "delinquent", "overdue", "frozen", "blocked", "kyc", "risk",
        "show me", "list", "find", "get me", "denied", "approved", "pending",
        "aml", "fraud", "npa", "outstanding",
    ],
    "Metrics": [
        "metric", "kpi", "rate", "delinquency", "npa", "ratio",
        "transaction success", "kyc completion", "threshold",
        "healthy", "calculate", "average", "percentage",
    ],
    "Runbooks": [
        "runbook", "workflow", "steps", "process", "aml", "investigation",
        "restructuring", "kyc renewal", "how to", "procedure",
        "freeze", "unfreeze", "resolve", "escalate",
    ],
}


def route_query(query: str) -> list[str]:
    """Route query to the most relevant OKF section."""
    query_lower = query.lower()
    scores = {}
    for section, keywords in SECTION_KEYWORDS.items():
        score = sum(1 + (2 if " " in kw else 0)
                    for kw in keywords if kw in query_lower)
        if score > 0:
            scores[section] = score
    if not scores:
        return ["Tables"]
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [ranked[0][0]]


def needs_data(query: str) -> bool:
    """Return True if the query requires live database results."""
    signals = [
        "show me", "list", "find", "how many", "count", "what are",
        "get me", "fetch", "display", "which", "pending", "overdue",
        "delinquent", "blocked", "frozen", "denied", "approved",
        "current", "today", "this month", "last month", "total",
        "top", "highest", "lowest", "latest", "real", "live", "actual",
    ]
    return any(s in query.lower() for s in signals)


def classify_query(query: str) -> str:
    """
    Classify query intent:
      'knowledge' → answer from OKF bundle alone
      'metric'    → predefined metric computation + OKF context
      'data'      → needs live data from database
    """
    query_lower = query.lower()

    knowledge_signals = [
        "steps for", "what are the steps", "how does", "how do i",
        "what is the schema", "what columns", "what fields",
        "business rules", "define", "definition", "explain",
        "workflow", "runbook", "process for", "when is", "what triggers",
    ]
    if any(s in query_lower for s in knowledge_signals):
        return "knowledge"

    metric_signals = [
        "delinquency rate", "npa ratio", "transaction success rate",
        "kyc completion", "kpi", "metric",
    ]
    if any(s in query_lower for s in metric_signals) and needs_data(query):
        return "metric"

    if needs_data(query):
        return "data"

    return "knowledge"


# ── Context Assembly ──────────────────────────────────────────────────

def concept_to_context(concept: Concept) -> str:
    lines = [
        f"## {concept.title} (type: {concept.concept_type})",
        f"**Description:** {concept.description}",
    ]
    if concept.tags:
        lines.append(f"**Tags:** {', '.join(concept.tags)}")
    lines.append("")
    lines.append(concept.body)
    return "\n".join(lines)


def build_context(concepts: list[Concept],
                  query_results: list[QueryResult] = None) -> str:
    parts = [
        "# Organizational Knowledge (OKF Bundle — ClearBank)\n",
        "Use this as the authoritative source for schema, business rules,",
        "and metric definitions.\n",
    ]
    for concept in concepts:
        parts.append(concept_to_context(concept))
        parts.append("\n---\n")

    if query_results:
        parts.append("\n# Live Data Results\n")
        parts.append(
            "The following data was retrieved from the actual database.\n")
        for result in query_results:
            parts.append(f"**Query:** `{result.sql.strip()}`\n")
            parts.append(result.to_markdown_table())
            parts.append(f"\n*({result.row_count} rows returned)*\n")
            parts.append("\n---\n")

    return "\n".join(parts)


# ── LLM Call ──────────────────────────────────────────────────────────

def call_llm(context: str, query: str) -> str | None:
    """Call LLM with combined OKF + data context. Returns None in mock mode."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    import httpx
    system = (
        "You are a banking AI assistant for ClearBank. "
        "You have two sources of information:\n"
        "1. OKF Knowledge Bundle — schema, business rules, metric definitions\n"
        "2. Live Data Results — actual query results from the database\n\n"
        "Use OKF knowledge to interpret the data correctly. "
        "Cite specific concepts and reference actual numbers from the data."
    )
    response = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-sonnet-4-6",
            "max_tokens": 1024,
            "system": system,
            "messages": [{"role": "user", "content": f"{context}\n\n---\n\n# Question\n\n{query}"}],
        },
        timeout=30,
    )
    data = response.json()
    return data["content"][0]["text"]


# ── SQL Generation from OKF Knowledge ────────────────────────────────

def generate_sql_from_okf(query: str) -> str:
    """
    Pattern-match the query to a relevant SQL statement.
    In production, the LLM reads OKF schema concepts and generates this.
    OKF ensures the LLM uses correct column names, ENUM values, and join paths.
    """
    q = query.lower()

    if "delinquent" in q or "overdue loan" in q:
        return """
            SELECT l.loan_id, l.customer_id, l.loan_type,
                   l.outstanding_balance, l.interest_rate, l.disbursed_at,
                   COUNT(lp.payment_id) AS overdue_installments
            FROM loans l
            JOIN loan_payments lp ON l.loan_id = lp.loan_id
            WHERE l.status = 'delinquent' AND lp.status = 'overdue'
            GROUP BY l.loan_id
            ORDER BY l.outstanding_balance DESC
            LIMIT 15
        """
    elif "overdue" in q and "installment" in q:
        return """
            SELECT lp.payment_id, lp.loan_id, lp.due_date, lp.amount_due,
                   CAST(julianday('now') - julianday(lp.due_date) AS INTEGER) AS days_overdue
            FROM loan_payments lp
            WHERE lp.status = 'overdue'
            ORDER BY days_overdue DESC
            LIMIT 20
        """
    elif "blocked" in q or ("frozen" in q and "account" in q):
        return """
            SELECT c.customer_id, c.kyc_status, c.risk_tier, c.status AS customer_status,
                   a.account_id, a.account_type, a.balance, a.status AS account_status
            FROM customers c
            JOIN accounts a ON c.customer_id = a.customer_id
            WHERE c.status = 'blocked' OR a.status = 'frozen'
            ORDER BY a.balance DESC
            LIMIT 20
        """
    elif "aml" in q or "flag" in q:
        return """
            SELECT flag_id, entity_type, entity_id, flag_reason, severity,
                   raised_at, status,
                   ROUND((julianday('now') - julianday(raised_at)) * 24, 1) AS hours_open
            FROM flags
            WHERE status IN ('open', 'under_review')
            ORDER BY
                CASE severity WHEN 'critical' THEN 1 WHEN 'high' THEN 2
                              WHEN 'medium' THEN 3 ELSE 4 END,
                raised_at ASC
            LIMIT 20
        """
    elif "kyc" in q and ("expired" in q or "pending" in q or "not verified" in q):
        return """
            SELECT customer_id, kyc_status, risk_tier, onboarded_at, status
            FROM customers
            WHERE kyc_status IN ('expired', 'pending', 'rejected')
              AND status = 'active'
            ORDER BY kyc_status, onboarded_at ASC
            LIMIT 20
        """
    elif ("transaction" in q or "txn" in q) and ("failed" in q or "large" in q or "high" in q):
        return """
            SELECT txn_id, account_id, txn_type, amount, status, channel, txn_at, description
            FROM transactions
            WHERE (status = 'failed' OR amount > 10000)
              AND txn_at >= datetime('now', '-30 days')
            ORDER BY amount DESC
            LIMIT 20
        """
    elif "customer" in q and ("count" in q or "how many" in q or "active" in q):
        return """
            SELECT kyc_status, risk_tier, status,
                   COUNT(*) AS customer_count
            FROM customers
            GROUP BY kyc_status, risk_tier, status
            ORDER BY customer_count DESC
        """
    elif "loan" in q and ("plan" in q or "type" in q or "summary" in q):
        return """
            SELECT loan_type, status,
                   COUNT(*) AS loan_count,
                   ROUND(SUM(principal), 2) AS total_principal,
                   ROUND(SUM(outstanding_balance), 2) AS total_outstanding
            FROM loans
            GROUP BY loan_type, status
            ORDER BY total_outstanding DESC
        """
    else:
        # Default: accounts summary
        return """
            SELECT account_type, status,
                   COUNT(*) AS account_count,
                   ROUND(AVG(balance), 2) AS avg_balance,
                   ROUND(SUM(balance), 2) AS total_balance
            FROM accounts
            GROUP BY account_type, status
            ORDER BY total_balance DESC
        """


# ═════════════════════════════════════════════════════════════════════
# Main Agent Flow
# ═════════════════════════════════════════════════════════════════════

SAMPLE_QUERIES = [
    "Show me all delinquent loans and their outstanding balances",
    "What is the current NPA ratio? Is it healthy?",
    "Find all overdue loan installments and how many days overdue",
    "Show me blocked customers and their frozen accounts",
    "List all open AML and fraud flags ordered by severity",
    "How many customers have expired or pending KYC?",
    "What are the steps for AML alert investigation?",        # knowledge only
    "What are the business rules for loan restructuring?",    # knowledge only
    "Show me failed and large transactions in the last 30 days",
    "What is the current KYC completion rate?",
]


def run_agent(bundle_path: str, db_path: str, query: str):
    print("=" * 64)
    print("  OKF + Data Agent — ClearBank Retail Banking")
    print("=" * 64)
    print(f"\n  Query: \"{query}\"")

    # Phase 1: Classify intent
    intent = classify_query(query)
    print(f"\n{'─'*64}")
    print(f"  Phase 1 │ Classify Query Intent → {intent.upper()}")
    if intent == "knowledge":
        print(f"          │ OKF knowledge only — no DB query needed")
    elif intent == "metric":
        print(f"          │ Predefined metric SQL + OKF context")
    else:
        print(f"          │ Needs live data from database")

    # Phase 2: OKF Progressive Disclosure
    t0 = time.perf_counter()
    nav = BundleNavigator(bundle_path)
    sections = route_query(query)
    section_concepts = []
    for section in sections:
        section_concepts.extend(nav.load_section(section))
    linked = nav.follow_links(section_concepts, max_hops=1, max_links=3)
    all_concepts = section_concepts + linked
    t1 = time.perf_counter()

    stats = nav.get_stats()
    print(f"\n{'─'*64}")
    print(f"  Phase 2 │ OKF Progressive Disclosure")
    print(f"          │ Routed to: {sections}")
    print(f"          │ Concepts loaded: {len(all_concepts)} "
          f"({stats['files_read']}/{stats['total_files']} files, {stats['pct_loaded']}%)")
    for c in all_concepts:
        marker = "✓" if c in section_concepts else "→"
        print(f"          │   {marker} {c.concept_id} ({c.concept_type})")
    print(f"          │ Time: {(t1-t0)*1000:.1f}ms")

    # Phase 3: Database query (if needed)
    query_results = []
    if intent in ("data", "metric"):
        tool = DatabaseTool(db_path)
        t2 = time.perf_counter()

        print(f"\n{'─'*64}")
        print(f"  Phase 3 │ Database Tool Execution")

        if intent == "metric":
            metric_map = {
                "delinquency rate": "loan_delinquency_rate",
                "npa ratio":        "npa_ratio",
                "npa":              "npa_ratio",
                "transaction success": "transaction_success_rate",
                "kyc completion":   "kyc_completion_rate",
                "kyc":              "kyc_completion_rate",
            }
            metric_name = "npa_ratio"
            for key, val in metric_map.items():
                if key in query.lower():
                    metric_name = val
                    break

            result = tool.get_metric(metric_name)
            query_results.append(result)
            print(f"          │ Tool: get_metric('{metric_name}')")
        else:
            sql = generate_sql_from_okf(query)
            result = tool.execute_query(sql)
            query_results.append(result)
            print(f"          │ Tool: execute_query()")
            for line in sql.strip().split("\n"):
                if line.strip():
                    print(f"          │   {line.strip()}")

        t3 = time.perf_counter()
        print(f"          │ Rows returned: {result.row_count}")
        print(f"          │ Time: {(t3-t2)*1000:.1f}ms")
        if result.error:
            print(f"          │ ERROR: {result.error}")

    # Phase 4: Build combined context
    context = build_context(
        all_concepts, query_results if query_results else None)
    print(f"\n{'─'*64}")
    print(f"  Phase 4 │ Combined Context")
    print(f"          │ OKF concepts: {len(all_concepts)}")
    print(f"          │ Data results: {len(query_results)}")
    print(
        f"          │ Context size: {len(context)} chars ({len(context.split())} words)")

    # Phase 5: LLM response
    print(f"\n{'─'*64}")
    print(f"  Phase 5 │ Agent Response")
    print(f"{'─'*64}\n")

    llm_response = call_llm(context, query)

    if llm_response:
        print(llm_response)
    else:
        print("[Mock Response — set ANTHROPIC_API_KEY for live LLM]\n")
        print(f"Query intent:  {intent}")
        print(f"OKF concepts loaded: {[c.title for c in all_concepts]}")
        if query_results:
            print(f"\nData preview:\n")
            for r in query_results:
                print(r.to_markdown_table()[:800])


def main():
    import argparse
    parser = argparse.ArgumentParser(description="ClearBank OKF Data Agent")
    parser.add_argument("--query", "-q", type=str, default=None,
                        help="Query to run (omit to cycle through sample queries)")
    parser.add_argument("--bundle", type=str, default=BUNDLE_PATH)
    parser.add_argument("--db",     type=str, default=DB_PATH)
    args = parser.parse_args()

    queries = [args.query] if args.query else SAMPLE_QUERIES

    for i, q in enumerate(queries[:3]):  # run first 3 by default
        if i > 0:
            print(f"\n\n{'#'*64}\n")
        run_agent(args.bundle, args.db, q)


if __name__ == "__main__":
    main()
