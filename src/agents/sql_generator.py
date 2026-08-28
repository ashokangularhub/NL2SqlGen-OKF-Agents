"""
agents/sql_generator.py — Agent 8: SQLGeneratorAgent

Generates a single SQLite SELECT query using the structured context from
ContextBuilderAgent. Incorporates validator feedback on retry attempts.
"""

from __future__ import annotations

import logging
import re

from .base import AgentState, BaseAgent, MAX_SQL_RETRIES
from .llm import call_llm

logger = logging.getLogger("clearbank.agent.sql_generator")


class SQLGeneratorAgent(BaseAgent):
    """
    LLM agent. Generates a single SQLite SELECT query using the structured
    context from ContextBuilderAgent. On retry, incorporates validator feedback
    to revise the previous SQL attempt.
    """

    name = "SQLGeneratorAgent"

    _SYSTEM = (
        "You are a SQL generator for a multi-domain PostgreSQL database "
        "(common_knowledgebase_db), covering retail banking (ClearBank) and "
        "e-commerce customer/product support (Aurora Electronics).\n\n"
        "CRITICAL RULES (VIOLATIONS CAUSE SQL ERRORS):\n"
        "  1. ALWAYS use EXACT column names from schema context\n"
        "  2. ALWAYS use EXACT table names from 'Database Table Names' section (snake_case)\n"
        "     Examples: loan_payments (NOT LoanPayments), bank_customers (NOT BankCustomers)\n"
        "  3. NEVER combine columns (e.g., first_name + last_name is NOT 'full_name')\n"
        "  4. NEVER invent columns that don't exist in schema\n"
        "  5. If a column isn't in schema, SELECT both separate columns OR use one that exists\n"
        "  6. Output ONLY a single SELECT statement — no INSERT/UPDATE/DELETE/DROP\n"
        "  7. ENUM values are case-sensitive (e.g., status = 'active', kyc_status = 'verified')\n"
        "  8. For text filters, use: LOWER(column) = LOWER('value') for case-insensitive matching\n"
        "  9. Apply LIMIT 50 unless user requests aggregate/metric\n"
        "  10. Do NOT wrap output in markdown or explanations\n"
        "  11. **WHEN USING JOINS: ALWAYS qualify ALL column names with their table name**\n"
        "       Example: SELECT bank_customers.customer_id, bank_customers.first_name, loans.loan_id, loans.status\n"
        "       DO NOT use unqualified names like 'customer_id' when joining—this causes ambiguous errors\n"
        "  12. **Date/time filters**: Use ISO format (YYYY-MM-DD) and comparison operators\n"
        "       Example: WHERE loans.maturity_date > '2024-01-01'\n"
        "  13. **Date arithmetic**: `date_column - date_column` returns an INTEGER (days) in PostgreSQL, NOT an interval\n"
        "       ✅ CORRECT: SELECT CURRENT_DATE - loan_payments.due_date AS days_overdue\n"
        "       ❌ WRONG: SELECT EXTRACT(DAY FROM CURRENT_DATE - loan_payments.due_date) — fails with\n"
        "                 'function pg_catalog.extract(unknown, integer) does not exist'\n"
        "       Only use EXTRACT(...) on an actual INTERVAL/TIMESTAMP, e.g. EXTRACT(DAY FROM AGE(a, b))\n"
        "  14. **Schema qualification**: retail_banking tables live in the default 'public' schema\n"
        "       (use bare names: FROM bank_customers). customer_support tables (products, orders,\n"
        "       inventory, returns, etc.) live in a dedicated 'customer_support' schema and MUST be\n"
        "       schema-qualified ONLY in the FROM/JOIN target itself:\n"
        "       FROM customer_support.orders JOIN customer_support.order_items ON order_items.order_id = orders.order_id\n"
        "       When qualifying COLUMNS (in SELECT/WHERE/ON), use the bare table name WITHOUT the\n"
        "       schema prefix — e.g. orders.order_status, NOT customer_support.orders.order_status.\n"
        "       A 3-part schema.table.column reference will NOT be recognized by this pipeline's validator.\n"
        "       The 'Database Table Names' section always gives you the exact FROM/JOIN name to use —\n"
        "       copy it verbatim (it already includes the schema prefix when one applies).\n"
        "  15. **Compound 'value + is-it-active/status' questions**: When the user asks for a value/attribute\n"
        "       PLUS whether some condition is currently true (e.g. \"what is the price, and is there an\n"
        "       active promotion?\"), do NOT put that condition in the WHERE clause — that silently drops\n"
        "       the row (and the value the user also asked for) whenever the condition is false/NULL.\n"
        "       Instead SELECT the value normally and expose the condition as a CASE-derived boolean column:\n"
        "       ✅ CORRECT: SELECT product_pricing.current_price, product_pricing.promo_label,\n"
        "                     CASE WHEN product_pricing.promo_label IS NOT NULL\n"
        "                          AND CURRENT_DATE BETWEEN product_pricing.promo_start_date AND product_pricing.promo_end_date\n"
        "                     THEN TRUE ELSE FALSE END AS promotion_active\n"
        "                   FROM ... WHERE product_variants.variant_label = 'Pearl White'\n"
        "       ❌ WRONG: ... WHERE variant_label = 'Pearl White'\n"
        "                 AND CURRENT_DATE BETWEEN promo_start_date AND promo_end_date\n"
        "                 (returns ZERO rows whenever there's no active promo, hiding the price too)\n\n"
        "TABLE NAME REFERENCE:\n"
        "  ✅ Correct (snake_case): FROM bank_customers, FROM loan_payments, JOIN loans ON ...\n"
        "  ✅ Correct (schema-qualified FROM/JOIN, bare column qualifiers):\n"
        "       FROM customer_support.orders JOIN customer_support.customers\n"
        "       ON customers.customer_id = orders.customer_id\n"
        "  ❌ WRONG (CamelCase): FROM LoanPayments, FROM BankCustomers (will cause 'relation does not exist' error)\n"
        "  ❌ WRONG (missing schema in FROM): FROM orders (will fail — must be customer_support.orders)\n"
        "  ❌ WRONG (3-part column qualifier): customer_support.orders.order_status (use orders.order_status)\n\n"
        "BEFORE GENERATING SQL:\n"
        "  1. Check the 'Database Table Names' section for the ACTUAL table names to use\n"
        "  2. Identify all tables needed for the query\n"
        "  3. List JOIN paths from the context\n"
        "  4. Read schema carefully. List all columns you plan to use.\n"
        "  5. Check that all columns exist in the schema\n"
        "  6. If using JOINs, prefix EVERY column with its snake_case table name (without the schema prefix)\n"
        "  7. Handle NULL values if needed (WHERE column IS NOT NULL)\n\n"
        "COMMON RETAIL BANKING QUERIES (with correct table names):\n"
        "  • Loans + Bank Customers: JOIN on loans.customer_id = bank_customers.customer_id\n"
        "  • Loans + Loan Payments: JOIN on loans.loan_id = loan_payments.loan_id\n"
        "  • Upcoming Payments: SELECT loan_payments.due_date FROM loan_payments WHERE status = 'upcoming'\n"
        "  • Bank Customers + Bank Accounts: JOIN on bank_customers.customer_id = bank_accounts.customer_id\n"
        "  • Filter by status: WHERE loans.status = 'active'\n"
        "  • Filter by date: WHERE loan_payments.due_date > '2024-01-15'\n\n"
        "COMMON E-COMMERCE / CUSTOMER SUPPORT QUERIES (schema-qualified FROM/JOIN, bare column refs):\n"
        "  • Orders + Customers: FROM customer_support.orders JOIN customer_support.customers "
        "ON customers.customer_id = orders.customer_id\n"
        "  • Order Items + Orders: FROM customer_support.order_items JOIN customer_support.orders "
        "ON order_items.order_id = orders.order_id\n"
        "  • Inventory availability: SELECT * FROM customer_support.inventory "
        "WHERE quantity_on_hand - quantity_reserved <= reorder_threshold\n"
        "  • Filter by order status: WHERE orders.order_status = 'DELIVERED'\n"
        "  • Returns: FROM customer_support.return_requests JOIN customer_support.order_items "
        "ON return_requests.order_item_id = order_items.order_item_id\n\n"
        "COLUMN NAME MAPPINGS (Do NOT use alternative spellings):\n"
        "  • loan_payments.due_date = when payment is DUE (NOT 'next_payment_due' or 'payment_date')\n"
        "  • loan_payments.paid_at = TIMESTAMP when payment was RECEIVED (NULL if unpaid)\n"
        "  • loan_payments.status = ENUM: 'upcoming', 'paid', 'overdue', 'partial', 'waived'\n"
        "  • loans.maturity_date = final maturity of the loan (NOT 'next_payment_due')\n"
        "  • bank_customers.first_name + bank_customers.last_name = customer's name (NOT 'full_name')\n"
        "  • customer_support.orders.order_status = ENUM (NOT 'status') — column ref: orders.order_status\n"
        "  • customer_support.order_items.item_status = ENUM (NOT 'status') — column ref: order_items.item_status\n"
        "  • customer_support.customers.full_name = customer's name (single column, unlike bank_customers) — column ref: customers.full_name\n\n"
        "If validator feedback says a column is ambiguous or doesn't exist:\n"
        "  1. Check the schema for all tables involved in JOINs\n"
        "  2. Rewrite using fully qualified names: table_name.column_name\n"
        "  3. Do NOT try the same unqualified column again"
    )

    def run(self, state: AgentState) -> AgentState:
        logger.info(
            "[%s] Generating SQL (attempt %d/%d).",
            self.name,
            state.sql_attempt,
            MAX_SQL_RETRIES,
        )

        # Log the schema context being used
        logger.debug(
            "[%s] Schema Context (first 500 chars):\n%s",
            self.name,
            state.system_context[:500]
        )
        logger.debug("[%s] User Query: %s", self.name, state.user_query)

        # Log schema context details
        if state.system_context:
            # Check what tables are in the context
            has_loans = "loans" in state.system_context.lower()
            has_customers = "bank_customers" in state.system_context.lower()
            has_payments = "loan_payments" in state.system_context.lower(
            ) or "loan payments" in state.system_context.lower()
            has_due_date = "due_date" in state.system_context.lower()

            logger.info(
                "[%s] ════════════════════════════════════════\n"
                "[%s] SCHEMA CONTEXT CHECK:\n"
                "[%s] Tables found: Loans=%s, Customers=%s, LoanPayments=%s\n"
                "[%s] Key columns: due_date=%s\n"
                "[%s] Context size: %d chars\n"
                "[%s] ════════════════════════════════════════",
                self.name, self.name,
                self.name, "✓" if has_loans else "✗", "✓" if has_customers else "✗", "✓" if has_payments else "✗",
                self.name, "✓" if has_due_date else "✗",
                self.name, len(state.system_context),
                self.name
            )

        user_msg = (
            f"Domain: {state.domain or 'unspecified'}\n"
            f"Schema Context:\n{state.system_context}\n\n"
            f"User Query: {state.user_query}"
        )
        if state.validator_feedback:
            logger.warning(
                "[%s] Retry with validator feedback: %s",
                self.name,
                state.validator_feedback
            )
            user_msg += (
                f"\n\nPrevious SQL (attempt {state.sql_attempt - 1}):\n{state.generated_sql}\n\n"
                f"Validator Feedback:\n{state.validator_feedback}\n\n"
                "Please fix the above issues in the revised SQL."
            )

        raw: str = call_llm(
            self._SYSTEM,
            user_msg,
            history=state.conversation_history or None,
        )
        sql = re.sub(
            r"^```(?:sql)?\s*|\s*```$", "", raw.strip(), flags=re.MULTILINE
        ).strip()
        state.generated_sql = sql

        # Log full SQL query for debugging
        logger.info("[%s] GENERATED SQL (Attempt %d):\n%s",
                    self.name, state.sql_attempt, sql)

        return state
