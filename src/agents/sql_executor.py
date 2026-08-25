"""
agents/sql_executor.py — Agent 10: SQLExecutorAgent

Tool agent: POSTs validated SQL to the active SQL execution backend (see
SQL_EXECUTOR_BACKENDS in base.py) and populates state.db_result on success,
or state.error on failure.
"""

from __future__ import annotations

import logging

import httpx

from .base import AgentState, BaseAgent, get_sql_service_url

logger = logging.getLogger("clearbank.agent.sql_executor")


class SQLExecutorAgent(BaseAgent):
    """
    Tool agent. POSTs the validated SQL to the active SQL execution backend
    (resolved via get_sql_service_url() / SQL_EXECUTOR_BACKEND env var).
    Populates state.db_result on success; sets state.error on DB-level or
    connection failures.
    """

    name = "SQLExecutorAgent"

    def run(self, state: AgentState) -> AgentState:
        sql = state.generated_sql.strip()
        sql_service_url = get_sql_service_url()
        logger.info(
            "[%s] ════════════════════════════════════════\n"
            "[%s] EXECUTING SQL QUERY:\n"
            "[%s] ════════════════════════════════════════\n%s\n"
            "[%s] ════════════════════════════════════════",
            self.name, self.name, self.name, sql, self.name
        )

        if not sql.upper().startswith("SELECT"):
            state.error = "Executor rejected non-SELECT SQL."
            logger.error("[%s] ❌ VALIDATION FAILED: %s",
                         self.name, state.error)
            return state

        try:
            logger.info("[%s] Sending request to: %s",
                        self.name, sql_service_url)
            resp = httpx.post(
                sql_service_url,
                json={"sql": sql, "max_rows": 1000},
                timeout=30,
            )
            logger.debug("[%s] HTTP Response Status: %d",
                         self.name, resp.status_code)
            resp.raise_for_status()
            data: dict = resp.json()
        except httpx.ConnectError:
            state.error = (
                f"Cannot reach database service at {sql_service_url}. "
                "Ensure the FastAPI server is running (`uvicorn ...`)."
            )
            logger.error(
                "[%s] ❌ CONNECTION ERROR:\n%s\n"
                "[%s] Please check if the configured SQL backend is running",
                self.name, state.error, self.name
            )
            return state
        except httpx.HTTPStatusError as exc:
            error_detail = exc.response.text[:500]
            state.error = (
                f"Database API returned HTTP {exc.response.status_code}: "
                f"{error_detail}"
            )
            logger.error(
                "[%s] ❌ HTTP ERROR %d:\n"
                "[%s] SQL Query:\n%s\n"
                "[%s] Error Response:\n%s",
                self.name, exc.response.status_code,
                self.name, sql,
                self.name, error_detail
            )
            return state
        except Exception as exc:
            state.error = f"Unexpected error calling database service: {exc}"
            logger.error(
                "[%s] ❌ UNEXPECTED ERROR:\n%s\n"
                "[%s] SQL Query:\n%s",
                self.name, state.error, self.name, sql
            )
            return state

        if data.get("error"):
            state.error = f"Database query error: {data['error']}"
            logger.error(
                "[%s] ❌ DATABASE ERROR:\n"
                "[%s] SQL Query:\n%s\n"
                "[%s] Error Message:\n%s",
                self.name, self.name, sql, self.name, state.error
            )
            return state

        state.db_result = data
        state.error = ""
        truncated = data.get("truncated", False)
        row_count = data.get("row_count", 0)

        logger.info(
            "[%s] ✅ QUERY SUCCESS:\n"
            "[%s] Rows returned: %d%s\n"
            "[%s] Result columns: %s",
            self.name, self.name, row_count,
            " (truncated)" if truncated else "",
            self.name, list(data.get("columns", []))
        )
        return state
