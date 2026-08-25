"""
agents/base.py — Shared state, base class, and pipeline constants.

All agent modules import AgentState, BaseAgent, and constants from here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# ── Constants ─────────────────────────────────────────────────────────────────

# Registry of SQL execution backends this pipeline can call. Every backend
# must implement the same REST contract as sql-service's POST /query:
#   request:  {"sql": str, "max_rows": int}
#   response: {"columns": [...], "rows": [[...]], "row_count": int,
#              "truncated": bool, "sql": str}
# Add new tools/projects here (name -> (env var holding its base URL, default
# base URL)); select the active one via the SQL_EXECUTOR_BACKEND env var.
SQL_EXECUTOR_BACKENDS: dict[str, tuple[str, str]] = {
    "sql_service": ("SQL_SERVICE_URL", "http://localhost:8000"),
    "customer_support_db_tool": ("CUSTOMER_SUPPORT_DB_TOOL_URL", "http://localhost:8003"),
}


def get_sql_service_url() -> str:
    """Resolve the active SQL execution backend's /query URL.

    Backend is chosen via the SQL_EXECUTOR_BACKEND env var (default
    "sql_service"); falls back to "sql_service" if an unknown name is set.
    """
    backend = os.environ.get("SQL_EXECUTOR_BACKEND", "sql_service")
    env_var, default_url = SQL_EXECUTOR_BACKENDS.get(
        backend, SQL_EXECUTOR_BACKENDS["sql_service"]
    )
    base_url = os.environ.get(env_var, default_url)
    return base_url.rstrip("/") + "/query"


OPENAI_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o")
MAX_SQL_RETRIES = 3
SQL_CONFIDENCE_THRESHOLD = 0.85

# Sentinel: SQLValidatorAgent sets state.error to this to signal a retry
_VALIDATION_FAILED = "__VALIDATION_FAILED__"


# ── Shared Pipeline State ──────────────────────────────────────────────────────


@dataclass
class AgentState:
    """
    Mutable state object threaded through every agent in the pipeline.
    All agents read from and write to this single shared object.
    """

    user_query: str
    # Previous turns: [{"role": "user"|"assistant", "content": "..."}]
    conversation_history: list = field(default_factory=list)
    intent: str = ""               # "domain" | "general"
    section_type: str = ""         # "Tables" | "Metrics" | "Runbooks" | "Datasets"
    domain: str = ""               # "retail_banking" | "customer_support"; "" = all domains
    # raw OKF markdown (SectionRetrievalAgent output)
    okf_content: str = ""
    # structured prompt (ContextBuilderAgent output)
    system_context: str = ""
    generated_sql: str = ""        # current SQL candidate
    sql_attempt: int = 0           # 1–3
    validator_feedback: str = ""   # validator feedback on SQL failure
    # raw JSON from FastAPI /query
    db_result: dict = field(default_factory=dict)
    final_answer: str = ""         # final formatted response
    error: str = ""                # pipeline error; _VALIDATION_FAILED for SQL retries


# ── Abstract Base Agent ────────────────────────────────────────────────────────


class BaseAgent:
    """Abstract base class for all pipeline agents."""

    name: str = "BaseAgent"

    def run(self, state: AgentState) -> AgentState:
        raise NotImplementedError(f"{self.name}.run() not implemented")

    def __repr__(self) -> str:
        return f"<{self.name}>"
