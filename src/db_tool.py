"""
db_tool.py — Database Tool Layer for ClearBank Retail Banking

Simulates an MCP (Model Context Protocol) server backed by SQLite.
Provides two tools:
  - execute_query(sql)       — read-only SELECT against retail_bank.db
  - get_metric(metric_name)  — predefined KPI SQL from the OKF bundle

Usage:
    from db_tool import DatabaseTool
    tool = DatabaseTool("retail_bank.db")
    result = tool.execute_query("SELECT * FROM loans WHERE status = 'delinquent'")
    print(result.to_markdown_table())
"""

import sqlite3
import os
from dataclasses import dataclass


@dataclass
class QueryResult:
    """Structured result from a database query."""
    columns: list
    rows: list
    row_count: int
    sql: str
    error: str | None = None

    def to_markdown_table(self) -> str:
        """Format results as a markdown table for LLM context."""
        if self.error:
            return f"**Query Error:** {self.error}"
        if not self.rows:
            return "*No rows returned.*"

        lines = []
        lines.append("| " + " | ".join(self.columns) + " |")
        lines.append("| " + " | ".join(["---"] * len(self.columns)) + " |")
        for row in self.rows[:20]:
            formatted = [str(v) if v is not None else "NULL" for v in row]
            lines.append("| " + " | ".join(formatted) + " |")
        if len(self.rows) > 20:
            lines.append(f"\n*... and {len(self.rows) - 20} more rows "
                         f"(showing 20 of {self.row_count})*")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "columns": self.columns,
            "rows": self.rows[:50],
            "row_count": self.row_count,
            "sql": self.sql,
            "error": self.error,
        }


class DatabaseTool:
    """
    Banking database query tool — the ACTION layer that complements OKF knowledge.

    In a production MCP setup this would be registered as:

        @mcp.tool()
        def execute_query(sql: str) -> str:
            '''Execute a read-only SQL query against the ClearBank database.'''
            return tool.execute_query(sql).to_markdown_table()

    OKF provides the schema and business rules; this tool executes the query
    and returns real data. Without OKF the agent would guess column names;
    without this tool the agent could only describe data, not return it.
    """

    # ── MCP-style tool definitions ───────────────────────────────────
    TOOLS = [
        {
            "name": "execute_query",
            "description": (
                "Execute a read-only SQL query against the ClearBank retail "
                "banking database. Tables: customers, accounts, transactions, "
                "loans, loan_payments, flags. Consult the OKF bundle for "
                "schema details and business rules before constructing queries."
            ),
            "parameters": {
                "sql": "A SELECT SQL query to execute (read-only)."
            },
        },
        {
            "name": "get_metric",
            "description": (
                "Compute a predefined KPI metric using the exact SQL from "
                "the OKF metrics bundle. Available: loan_delinquency_rate, "
                "npa_ratio, transaction_success_rate, kyc_completion_rate."
            ),
            "parameters": {
                "metric_name": "Name of the metric to compute."
            },
        },
    ]

    # ── Metric SQL (mirrors OKF metric concept files, SQLite dialect) ─
    METRIC_SQL = {
        "loan_delinquency_rate": """
            SELECT
                strftime('%Y-%m', lp.due_date) AS month,
                ROUND(
                    100.0 * COUNT(DISTINCT CASE WHEN lp.status = 'overdue'
                                               THEN l.loan_id END)
                    / NULLIF(COUNT(DISTINCT l.loan_id), 0),
                    2
                ) AS delinquency_rate_pct
            FROM loans l
            JOIN loan_payments lp ON l.loan_id = lp.loan_id
            WHERE l.status IN ('active', 'delinquent')
            GROUP BY 1
            ORDER BY 1 DESC
            LIMIT 6;
        """,
        "npa_ratio": """
            SELECT
                ROUND(
                    100.0 * SUM(CASE WHEN status IN ('delinquent','written_off')
                                     THEN outstanding_balance ELSE 0 END)
                    / NULLIF(SUM(CASE WHEN status IN ('active','delinquent','written_off')
                                     THEN outstanding_balance ELSE 0 END), 0),
                    2
                ) AS npa_ratio_pct,
                ROUND(SUM(CASE WHEN status IN ('delinquent','written_off')
                               THEN outstanding_balance ELSE 0 END), 2) AS npa_book_usd,
                ROUND(SUM(CASE WHEN status IN ('active','delinquent','written_off')
                               THEN outstanding_balance ELSE 0 END), 2) AS total_loan_book_usd
            FROM loans;
        """,
        "transaction_success_rate": """
            SELECT
                strftime('%Y-%m', txn_at) AS month,
                ROUND(
                    100.0 * SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END)
                    / NULLIF(SUM(CASE WHEN status IN ('completed','failed') THEN 1 ELSE 0 END), 0),
                    2
                ) AS success_rate_pct,
                COUNT(*) AS total_transactions
            FROM transactions
            WHERE status IN ('completed', 'failed')
            GROUP BY 1
            ORDER BY 1 DESC
            LIMIT 6;
        """,
        "kyc_completion_rate": """
            SELECT
                ROUND(
                    100.0 * SUM(CASE WHEN kyc_status = 'verified' THEN 1 ELSE 0 END)
                    / NULLIF(COUNT(*), 0),
                    2
                ) AS kyc_completion_rate_pct,
                COUNT(*) AS total_active_customers,
                SUM(CASE WHEN kyc_status = 'verified' THEN 1 ELSE 0 END) AS verified_count,
                SUM(CASE WHEN kyc_status IN ('pending','expired','rejected') THEN 1 ELSE 0 END)
                    AS unverified_count
            FROM customers
            WHERE status = 'active';
        """,
    }

    def __init__(self, db_path: str):
        self.db_path = db_path
        if not os.path.exists(db_path):
            raise FileNotFoundError(
                f"Database not found: {db_path}\n"
                f"Run: python src/seed_database.py"
            )

    def execute_query(self, sql: str) -> QueryResult:
        """Execute a read-only SQL query. Only SELECT statements allowed."""
        sql = sql.strip().rstrip(";")

        if not sql.upper().startswith("SELECT"):
            return QueryResult(
                columns=[], rows=[], row_count=0, sql=sql,
                error="Only SELECT queries are allowed (read-only tool)."
            )

        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            cur.execute(sql)
            columns = [desc[0] for desc in cur.description]
            rows = cur.fetchall()
            conn.close()

            return QueryResult(
                columns=columns,
                rows=[list(r) for r in rows],
                row_count=len(rows),
                sql=sql,
            )
        except Exception as e:
            return QueryResult(
                columns=[], rows=[], row_count=0, sql=sql,
                error=str(e),
            )

    def get_metric(self, metric_name: str) -> QueryResult:
        """Execute a predefined metric SQL from the OKF bundle."""
        sql = self.METRIC_SQL.get(metric_name)
        if not sql:
            available = ", ".join(self.METRIC_SQL.keys())
            return QueryResult(
                columns=[], rows=[], row_count=0, sql="",
                error=f"Unknown metric '{metric_name}'. Available: {available}"
            )
        return self.execute_query(sql)
