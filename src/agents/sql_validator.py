"""
agents/sql_validator.py — Agent 9: SQLValidatorAgent

LLM gate: validates generated SQL for schema alignment, SELECT-only safety,
and correctness. Requires confidence ≥ SQL_CONFIDENCE_THRESHOLD to pass.

ENHANCEMENTS:
  - Programmatic column name validation against extracted schema
  - Detects invalid columns like 'full_name' that don't exist in schema
  - Rejects SQL before it reaches the database
"""

from __future__ import annotations

import logging
import re

from .base import AgentState, BaseAgent, SQL_CONFIDENCE_THRESHOLD, _VALIDATION_FAILED
from .llm import call_llm

logger = logging.getLogger("clearbank.agent.sql_validator")


# ── Hardcoded Schemas for Common Tables (Authoritative Source) ────────────

KNOWN_SCHEMAS = {
    "customers": {
        "customer_id", "first_name", "last_name", "date_of_birth",
        "email", "phone", "kyc_status", "risk_tier", "onboarded_at", "status"
    },
    "loans": {
        "loan_id", "customer_id", "loan_type", "principal", "outstanding_balance",
        "interest_rate", "tenure_months", "disbursed_at", "maturity_date", "status"
    },
    "loan_payments": {
        "payment_id", "loan_id", "due_date", "paid_at", "amount_due",
        "amount_paid", "status"
    },
    "accounts": {
        "account_id", "customer_id", "account_type", "balance",
        "currency", "status", "opened_at", "closed_at"
    },
    "transactions": {
        "transaction_id", "account_id", "type", "amount", "currency",
        "timestamp", "status", "merchant", "description"
    },
    "flags": {
        "flag_id", "customer_id", "flag_type", "severity", "reason",
        "created_at", "resolved_at", "status"
    },
}

# Flatten all known columns for quick lookup
ALL_KNOWN_COLUMNS = set()
for columns in KNOWN_SCHEMAS.values():
    ALL_KNOWN_COLUMNS.update(columns)


# ── Programmatic Schema & Column Extraction ──────────────────────────────

def extract_schema_columns(context: str) -> set[str]:
    """
    Extract all valid column names from the schema context.

    Handles TWO formats:
      1. Bullet format: • `column_name` (type): description
      2. Markdown table: | `column_name` | type | description |

    Returns a set of lowercase column names for case-insensitive matching.
    Falls back to KNOWN_SCHEMAS if extraction fails.
    """
    columns = set()

    # TRY FORMAT 1: Bullet point format (• `column_name` (type):)
    pattern1 = r"•\s*`([^`]+)`\s*\([^)]+\):"
    for match in re.finditer(pattern1, context):
        col_name = match.group(1).lower()
        columns.add(col_name)

    # TRY FORMAT 2: Markdown table format (| `column_name` | type |)
    # Matches table rows like: | `payment_id` | UUID | Description |
    pattern2 = r"\|\s*`([^`]+)`\s*\|\s*\w+"
    for match in re.finditer(pattern2, context):
        col_name = match.group(1).lower()
        columns.add(col_name)

    # If we extracted columns from either format, return them
    if columns:
        logger.info(
            "[SQLValidator] extract_schema_columns: Found %d columns: %s",
            len(columns), sorted(list(columns))[:15])
        return columns

    # Fallback: return all known columns if extraction failed
    logger.warning(
        "[SQLValidator] extract_schema_columns: Pattern matching FAILED, using fallback (%d columns)",
        len(ALL_KNOWN_COLUMNS))
    return ALL_KNOWN_COLUMNS


def extract_columns_from_sql(sql: str) -> set[str]:
    """
    Extract all column references from SQL query.

    Looks for:
      - table.column references (e.g., c.full_name)
      - Plain column names in SELECT/WHERE clauses
      - Handles backticks and quotes

    Returns lowercase column names.
    """
    sql_lower = sql.lower()
    columns = set()

    # Match: table.column (e.g., c.full_name, l.loan_id)
    pattern = r"(\w+)\.(\w+)"
    for match in re.finditer(pattern, sql_lower):
        col_name = match.group(2)  # Get the column part after the dot
        columns.add(col_name)

    # Also extract unqualified columns from SELECT
    select_match = re.search(r"SELECT\s+(.+?)\s+FROM", sql_lower, re.DOTALL)
    if select_match:
        select_clause = select_match.group(1)
        # Split by comma and extract column names
        for col in select_clause.split(','):
            col = col.strip()
            # Remove table.column prefix if present
            if '.' in col:
                col = col.split('.')[-1]
            # Handle functions like COUNT(*), SUM(col), etc.
            col = re.sub(r'^[a-z_]+\s*\(', '', col)
            col = re.sub(r'\)\s*(?:as\s+\w+)?$', '', col)
            col = col.replace('`', '').replace('"', '').replace("'", '')
            if col and col != '*':
                columns.add(col.strip().lower())

    return columns


def validate_columns_exist(sql: str, context: str) -> tuple[bool, str]:
    """
    Programmatically validate that all columns in SQL exist in schema.

    Returns:
      (valid: bool, error_msg: str)
    """
    schema_columns = extract_schema_columns(context)
    sql_columns = extract_columns_from_sql(sql)

    if not schema_columns:
        # If we couldn't extract schema, skip programmatic validation
        return True, ""

    # Check for invalid columns
    invalid_columns = sql_columns - schema_columns
    if invalid_columns:
        invalid_list = ', '.join(sorted(invalid_columns))

        logger.warning(
            "[SQLValidator] Column validation FAILED:\n"
            "[SQLValidator] SQL columns found: %s\n"
            "[SQLValidator] Schema columns available: %s\n"
            "[SQLValidator] Invalid/unknown columns: %s",
            sorted(sql_columns), sorted(schema_columns)[:20], invalid_list
        )

        # Provide specific guidance for common mistakes
        guidance = ""

        if 'full_name' in invalid_columns:
            guidance = "\n💡 'full_name' does NOT exist. Use 'first_name' and 'last_name' SEPARATELY in SELECT clause."

        if 'next_payment_due' in invalid_columns:
            guidance = (
                "\n💡 'next_payment_due' does NOT exist in Loans table.\n"
                "   For upcoming payments, use: LoanPayments.due_date WHERE status = 'upcoming'\n"
                "   Join: Loans.loan_id = LoanPayments.loan_id"
            )

        if 'payment_due_date' in invalid_columns:
            guidance = (
                "\n💡 'payment_due_date' does NOT exist.\n"
                "   Correct column: LoanPayments.due_date (scheduled date)\n"
                "   Or use: LoanPayments.paid_at (actual payment timestamp)"
            )

        if 'payment_date' in invalid_columns:
            guidance = (
                "\n💡 'payment_date' does NOT exist. Use one of:\n"
                "   • LoanPayments.due_date (scheduled date)\n"
                "   • LoanPayments.paid_at (actual payment timestamp)"
            )

        error_msg = (
            f"❌ SQL ERROR: Column names in query don't exist in database schema.\n"
            f"Invalid columns: {invalid_list}\n"
            f"Valid columns available: {', '.join(sorted(schema_columns))}"
            f"{guidance}"
        )
        return False, error_msg

    return True, ""


def validate_join_column_qualification(sql: str) -> tuple[bool, str]:
    """
    Check for ambiguous column references in JOINs.

    If SQL contains a JOIN, columns that exist in multiple tables MUST be
    qualified with their table name (e.g., Customers.customer_id, not just customer_id).

    Returns:
      (valid: bool, error_msg: str)
    """
    sql_upper = sql.upper()

    # Check if this query has a JOIN
    if 'JOIN' not in sql_upper:
        return True, ""

    # Columns that appear in multiple tables (common foreign keys)
    shared_columns = {
        'customer_id',      # in customers, loans, accounts, flags
        'account_id',       # in accounts, transactions
        'status',           # in customers, accounts, transactions, flags, loans
    }

    # Extract unqualified columns from the SELECT clause
    select_match = re.search(r"SELECT\s+(.+?)\s+FROM", sql_upper, re.DOTALL)
    if not select_match:
        return True, ""

    select_clause = select_match.group(1)

    # Find columns that are:
    # 1. Unqualified (not preceded by table.column)
    # 2. Exist in multiple tables
    problematic_columns = []

    for col in shared_columns:
        col_upper = col.upper()
        # Match: word boundary + column name + not preceded by dot
        # Pattern matches "customer_id" but not "Customers.customer_id"
        pattern = rf"(?<!\w|\.)(?<!\.)(?:^|,|\s)({col_upper})(?:\s|,|$|\))"

        for match in re.finditer(pattern, select_clause, re.IGNORECASE):
            # Make sure it's not already qualified
            start_pos = match.start()
            before_text = select_clause[max(0, start_pos-20):start_pos]
            if '.' not in before_text:
                problematic_columns.append(col)
                break

    if problematic_columns:
        error_msg = (
            f"❌ SQL ERROR: Ambiguous column references in JOIN query.\n"
            f"These columns exist in multiple tables and must be qualified:\n"
            f"  - {', '.join(problematic_columns)}\n\n"
            f"SOLUTION: Use table prefixes for all columns when joining:\n"
            f"  WRONG: SELECT customer_id, account_id FROM Customers JOIN Accounts ...\n"
            f"  RIGHT: SELECT Customers.customer_id, Accounts.account_id FROM Customers JOIN Accounts ...\n\n"
            f"IMPORTANT: Qualify ALL columns with their table names when using JOINs."
        )
        return False, error_msg

    return True, ""


def extract_tables_from_sql(sql: str) -> set[str]:
    """
    Extract table names referenced in FROM/JOIN clauses (lowercase).
    Handles optional quoting, e.g. FROM "Loan Payments" or FROM loan_payments.
    """
    tables = set()

    # Quoted names (may contain spaces), e.g. FROM "Loan Payments"
    for match in re.finditer(r'\b(?:FROM|JOIN)\s+"([^"]+)"', sql, re.IGNORECASE):
        tables.add(match.group(1).strip().lower())

    # Plain identifiers (snake_case, single token, no spaces), e.g. FROM loan_payments
    for match in re.finditer(r'\b(?:FROM|JOIN)\s+(\w+)', sql, re.IGNORECASE):
        tables.add(match.group(1).strip().lower())

    return tables


def validate_table_names_exist(sql: str) -> tuple[bool, str]:
    """
    Programmatically validate that all FROM/JOIN table names are real,
    known snake_case PostgreSQL tables (KNOWN_SCHEMAS is authoritative).

    This exists because the LLM validator occasionally hallucinates that a
    correct snake_case table name (e.g. 'loan_payments') is wrong and should
    be a human-readable title (e.g. 'Loan Payments'). Catching this
    deterministically avoids that non-deterministic false rejection.

    Returns:
      (valid: bool, error_msg: str)
    """
    known_tables = set(KNOWN_SCHEMAS.keys())
    sql_tables = extract_tables_from_sql(sql)

    invalid_tables = {t for t in sql_tables if t not in known_tables}
    if invalid_tables:
        error_msg = (
            f"❌ SQL ERROR: Table name(s) don't exist in database: {', '.join(sorted(invalid_tables))}\n"
            f"Valid tables (snake_case, exact spelling): {', '.join(sorted(known_tables))}\n"
            f"💡 Table names must be lowercase snake_case (e.g. 'loan_payments'), "
            f"NEVER human-readable titles with spaces or CamelCase (e.g. NOT 'Loan Payments', NOT 'LoanPayments')."
        )
        return False, error_msg

    return True, ""


class SQLValidatorAgent(BaseAgent):
    """
    LLM gate agent. Validates generated SQL for schema alignment, SELECT-only
    safety, and correctness. Requires confidence ≥ 0.85 to pass.

    On failure: sets state.error = _VALIDATION_FAILED and state.validator_feedback.
    On success: clears both fields.
    """

    name = "SQLValidatorAgent"

    _SYSTEM = (
        "You are a SQL validator for a banking system. Be thorough but fair.\n\n"
        "IMPORTANT: Table names and column names have ALREADY been verified\n"
        "programmatically against the real database schema before you see this SQL.\n"
        "Every table name (e.g. loan_payments, customers, loans) is CONFIRMED CORRECT\n"
        "snake_case exactly as it appears in the SQL. Do NOT flag table or column\n"
        "names as misspelled, wrong case, or needing spaces/CamelCase — that has\n"
        "already been checked and passed. Focus ONLY on the checks below.\n\n"
        "Check the SQL against the schema context:\n"
        "  1. Must be a single SELECT statement only (no INSERT/UPDATE/DELETE/DROP)\n"
        "  2. All columns must be fully qualified in JOINs (table_name.column_name)\n"
        "  3. ENUM values must match schema (case-sensitive)\n"
        "  4. JOINs must use correct foreign-key columns\n"
        "  5. No cartesian products; no unnecessary subqueries\n"
        "  6. Syntax must be valid PostgreSQL\n\n"
        "PASS if:\n"
        "  • SELECT includes only columns from the schema\n"
        "  • All columns in JOINs are qualified with table names\n"
        "  • WHERE clause uses valid columns and operators\n"
        "  • Confidence is high (≥0.85)\n\n"
        "FAIL with clear feedback if:\n"
        "  • Columns in JOINs are unqualified (ambiguous)\n"
        "  • ENUM values don't match schema\n"
        "  • Syntax is invalid PostgreSQL\n\n"
        "Respond ONLY with valid JSON:\n"
        "{\"valid\": true|false, \"confidence\": 0.0-1.0, \"feedback\": \"<clear reason if invalid>\"}"
    )

    def run(self, state: AgentState) -> AgentState:
        logger.info("[%s] Validating SQL (attempt %d).",
                    self.name, state.sql_attempt)

        if not state.generated_sql.strip().upper().startswith("SELECT"):
            state.error = _VALIDATION_FAILED
            state.validator_feedback = (
                "Generated SQL is not a SELECT statement. "
                "Only read-only SELECT queries are allowed."
            )
            logger.warning("[%s] Hard-rejected non-SELECT SQL.", self.name)
            return state

        # PROGRAMMATIC VALIDATION: Check that all columns exist in schema
        # This catches errors like 'full_name' that don't exist
        columns_valid, column_error = validate_columns_exist(
            state.generated_sql, state.system_context
        )
        if not columns_valid:
            state.error = _VALIDATION_FAILED
            state.validator_feedback = column_error

            # Log what columns were extracted from schema
            schema_cols = extract_schema_columns(state.system_context)
            logger.warning(
                "[%s] ❌ FAIL — Column validation\n"
                "[%s] Extracted schema columns: %s\n"
                "[%s] Error: %s",
                self.name, self.name, sorted(
                    schema_cols) if schema_cols else "NONE",
                self.name, column_error
            )
            return state

        # PROGRAMMATIC VALIDATION: Check for ambiguous columns in JOINs
        # This catches errors like using 'customer_id' without 'Customers.' prefix
        join_valid, join_error = validate_join_column_qualification(
            state.generated_sql
        )
        if not join_valid:
            state.error = _VALIDATION_FAILED
            state.validator_feedback = join_error
            logger.warning("[%s] FAIL — JOIN column qualification: %s",
                           self.name, join_error)
            return state

        # PROGRAMMATIC VALIDATION: Check that all table names are real, known
        # snake_case tables. This is authoritative and prevents the LLM from
        # non-deterministically "correcting" a valid table name (e.g. flagging
        # 'loan_payments' as wrong and suggesting 'Loan Payments' instead).
        tables_valid, table_error = validate_table_names_exist(
            state.generated_sql
        )
        if not tables_valid:
            state.error = _VALIDATION_FAILED
            state.validator_feedback = table_error
            logger.warning("[%s] FAIL — Table name validation: %s",
                           self.name, table_error)
            return state

        user_msg = (
            f"Schema Context:\n{state.system_context}\n\n"
            f"SQL to validate:\n{state.generated_sql}"
        )
        result = call_llm(self._SYSTEM, user_msg, json_mode=True)

        if isinstance(result, dict):
            valid: bool = bool(result.get("valid", False))
            confidence: float = float(result.get("confidence", 0.0))
            feedback: str = str(result.get("feedback", ""))
        else:
            valid, confidence, feedback = False, 0.0, str(result)

        if valid and confidence >= SQL_CONFIDENCE_THRESHOLD:
            state.error = ""
            state.validator_feedback = ""
            logger.info("[%s] ✅ PASS — confidence=%.2f", self.name, confidence)
        else:
            state.error = _VALIDATION_FAILED
            state.validator_feedback = (
                feedback
                or f"Confidence {confidence:.2f} below threshold {SQL_CONFIDENCE_THRESHOLD}."
            )
            logger.warning("[%s] FAIL — %s", self.name,
                           state.validator_feedback)

        return state
