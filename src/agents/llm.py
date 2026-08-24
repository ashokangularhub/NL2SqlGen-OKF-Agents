"""
agents/llm.py — LLM helper: call_llm(), mock stubs, and SQL heuristics.

Import call_llm wherever LLM inference is needed. The function falls back to
an intelligent mock when OPENAI_API_KEY is not set.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import httpx

from .base import OPENAI_MODEL, OPENAI_URL

logger = logging.getLogger("clearbank.llm")


# ── Public helper ──────────────────────────────────────────────────────────────


def call_llm(
    system: str,
    user: str,
    *,
    json_mode: bool = False,
    history: list[dict] | None = None,
) -> str | dict[str, Any]:
    """
    Call OpenAI Chat Completions (default: gpt-4o) via httpx.
    Returns a parsed dict when json_mode=True, otherwise a plain string.
    Falls back to an intelligent mock stub when OPENAI_API_KEY is absent.
    Override model via OPENAI_MODEL env var.

    Parameters
    ----------
    history : list of {"role": "user"|"assistant", "content": "..."} dicts
        Prior conversation turns inserted between the system message and the
        current user message so the model has follow-up context.
    """
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        logger.debug("No OPENAI_API_KEY — mock LLM stub active.")
        return _mock_llm(system, user, json_mode=json_mode)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    messages: list[dict] = [{"role": "system", "content": system}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user})

    payload: dict[str, Any] = {
        "model": OPENAI_MODEL,
        "max_tokens": 2048,
        "messages": messages,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    try:
        response = httpx.post(OPENAI_URL, headers=headers,
                              json=payload, timeout=60)
        response.raise_for_status()
        text: str = response.json()["choices"][0]["message"]["content"].strip()
        logger.debug("LLM response received (%d chars).", len(text))
    except httpx.HTTPStatusError as exc:
        logger.error(
            "LLM API HTTP %s: %s", exc.response.status_code, exc.response.text[:300]
        )
        raise
    except Exception as exc:
        logger.error("LLM call failed: %s", exc)
        raise

    if json_mode:
        cleaned = re.sub(
            r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE
        ).strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            logger.warning(
                "JSON parse failed on LLM output; wrapping in 'raw' key.")
            return {"raw": text}

    return text


# ── Mock LLM (offline / demo mode) ───────────────────────────────────────────


def _mock_llm(system: str, user: str, *, json_mode: bool) -> str | dict[str, Any]:
    """Keyword-driven stubs that produce realistic responses without an API key."""
    sl = system.lower()
    ul = user.lower()

    # ── Intent classification ─────────────────────────────────────────
    if "intent" in sl or ("classify" in sl and "domain" in sl):
        domain_kws = [
            "loan", "customer", "account", "transaction", "kyc", "aml",
            "flag", "balance", "payment", "delinquent", "npa", "runbook",
            "clearbank", "banking", "fraud", "risk", "metric",
        ]
        intent = "domain" if any(kw in ul for kw in domain_kws) else "general"
        return {"intent": intent} if json_mode else intent

    # ── Error response ────────────────────────────────────────────────
    if "error handler" in sl or ("unable to complete" in sl and "expose" in sl):
        return (
            "I was unable to complete your request. This may be due to an ambiguous "
            "query or a temporary database issue. Please try rephrasing your question, "
            "or contact support if the issue persists."
        )

    # ── SQL generation ────────────────────────────────────────────────
    if "you are a sql generator" in sl:
        return _mock_sql_for_query(user)

    # ── SQL validation ────────────────────────────────────────────────
    if "you are a sql validator" in sl:
        result = {
            "valid": True,
            "confidence": 0.91,
            "feedback": "SQL aligns with the OKF schema.",
        }
        return result if json_mode else json.dumps(result)

    # ── Context building ──────────────────────────────────────────────
    if "context builder" in sl or ("context" in sl and "schema context" in sl):
        return (
            "# Schema Context (Mock)\n\n"
            "**Tables:** bank_customers, bank_accounts, transactions, loans, loan_payments, flags\n\n"
            "**Key columns:**\n"
            "- bank_customers: customer_id, full_name, email, kyc_status ∈ {verified,pending,expired,rejected}, "
            "status ∈ {active,inactive,blacklisted}, created_at\n"
            "- bank_accounts: account_id, customer_id (FK), account_type ∈ {savings,checking,fixed_deposit}, "
            "balance, status ∈ {active,frozen,blocked,closed}\n"
            "- transactions: txn_id, customer_id (FK), account_id (FK), amount, type, "
            "status ∈ {completed,pending,failed,reversed}, txn_at\n"
            "- loans: loan_id, customer_id (FK), principal, outstanding_balance, interest_rate, "
            "status ∈ {active,delinquent,written_off,closed}, disbursed_at\n"
            "- loan_payments: payment_id, loan_id (FK), emi_amount, due_date, paid_date, "
            "status ∈ {paid,overdue,pending}\n"
            "- flags: flag_id, customer_id (FK), flag_type ∈ {aml,fraud,kyc,risk}, "
            "severity ∈ {low,medium,high,critical}, status ∈ {open,resolved,escalated}\n"
        )

    # ── Knowledge base ────────────────────────────────────────────────
    if "knowledge base agent" in sl or any(w in sl for w in ["runbook", "compliance workflow"]):
        return (
            "**ClearBank Knowledge Base Answer** *(Mock)*\n\n"
            "Based on the OKF bundle content, here is the relevant guidance "
            f"for your query:\n\n> {ul[:200]}\n\n"
            "Please refer to the full runbook in `okf_bundle/runbooks/` for complete steps."
        )

    # ── Response synthesis ────────────────────────────────────────────
    if "response synthesizer" in sl or "synthesiz" in sl:
        return f"**Answer** *(Mock)*\n\n{ul[:400]}"

    # ── General fallback ──────────────────────────────────────────────
    return f"I can help with that. *(Mock)* You asked: {user[:200]}"


def _mock_sql_for_query(query: str) -> str:
    """Return a plausible SELECT statement for common banking queries."""
    # Match against the actual user question only — the schema context
    # preamble contains table/column/ENUM words that would otherwise trigger
    # the wrong branch (e.g. "flags" in the schema listing).
    marker = "user query:"
    idx = query.lower().rfind(marker)
    q = query[idx + len(marker):].lower() if idx != -1 else query.lower()
    if "delinquent" in q or "overdue loan" in q:
        return (
            "SELECT l.loan_id, c.first_name, c.last_name, l.outstanding_balance, l.status "
            "FROM loans l JOIN bank_customers c ON l.customer_id = c.customer_id "
            "WHERE l.status = 'delinquent' ORDER BY l.outstanding_balance DESC LIMIT 20"
        )
    if "kyc" in q and any(w in q for w in ["pending", "expired", "unverified"]):
        return (
            "SELECT customer_id, first_name, last_name, kyc_status, onboarded_at "
            "FROM bank_customers WHERE kyc_status IN ('pending','expired') "
            "ORDER BY onboarded_at"
        )
    if "transaction" in q and any(w in q for w in ["failed", "pending"]):
        return (
            "SELECT t.txn_id, c.first_name, c.last_name, t.amount, t.status, t.txn_at "
            "FROM transactions t "
            "JOIN bank_accounts a ON t.account_id = a.account_id "
            "JOIN bank_customers c ON a.customer_id = c.customer_id "
            "WHERE t.status IN ('failed','pending') ORDER BY t.txn_at DESC LIMIT 20"
        )
    if any(w in q for w in ["flag", "aml", "fraud", "alert"]):
        return (
            "SELECT flag_id, entity_type, entity_id, flag_reason, severity, status, raised_at "
            "FROM flags WHERE status = 'open' ORDER BY raised_at DESC LIMIT 20"
        )
    if "frozen" in q or "blocked" in q:
        return (
            "SELECT a.account_id, c.first_name, c.last_name, a.account_type, a.status, a.balance "
            "FROM bank_accounts a JOIN bank_customers c ON a.customer_id = c.customer_id "
            "WHERE a.status IN ('frozen','closed')"
        )
    if "npa" in q:
        return (
            "SELECT ROUND(100.0 * SUM(CASE WHEN status IN ('delinquent','written_off') "
            "THEN outstanding_balance ELSE 0 END) / NULLIF(SUM(outstanding_balance),0),2) "
            "AS npa_ratio_pct FROM loans WHERE status IN ('active','delinquent','written_off')"
        )
    if "delinquency rate" in q:
        return (
            "SELECT TO_CHAR(lp.due_date, 'YYYY-MM') AS month, "
            "ROUND(100.0 * COUNT(DISTINCT CASE WHEN lp.status='overdue' THEN l.loan_id END) "
            "/ NULLIF(COUNT(DISTINCT l.loan_id),0),2) AS delinquency_rate_pct "
            "FROM loans l JOIN loan_payments lp ON l.loan_id = lp.loan_id "
            "WHERE l.status IN ('active','delinquent') GROUP BY 1 ORDER BY 1 DESC LIMIT 6"
        )
    if "transaction success" in q or "success rate" in q:
        return (
            "SELECT TO_CHAR(txn_at, 'YYYY-MM') AS month, "
            "ROUND(100.0 * SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) "
            "/ NULLIF(COUNT(*),0),2) AS success_rate_pct, COUNT(*) AS total "
            "FROM transactions GROUP BY 1 ORDER BY 1 DESC LIMIT 6"
        )
    if "kyc completion" in q or "kyc rate" in q:
        return (
            "SELECT ROUND(100.0 * SUM(CASE WHEN kyc_status='verified' THEN 1 ELSE 0 END) "
            "/ NULLIF(COUNT(*),0),2) AS kyc_completion_rate_pct, "
            "COUNT(*) AS total_customers FROM bank_customers WHERE status='active'"
        )
    if "loan" in q and any(w in q for w in ["count", "how many", "summary"]):
        return (
            "SELECT status, COUNT(*) AS count, "
            "ROUND(SUM(outstanding_balance),2) AS total_outstanding "
            "FROM loans GROUP BY status ORDER BY count DESC"
        )
    return (
        "SELECT customer_id, first_name, last_name, email, kyc_status, status "
        "FROM bank_customers WHERE status = 'active' ORDER BY onboarded_at DESC LIMIT 20"
    )
