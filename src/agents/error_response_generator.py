"""
agents/error_response_generator.py — Agent 11: ErrorResponseGeneratorAgent

Produces a user-friendly error message when SQL retries are exhausted or
a DB connection/query failure occurs. Does not expose raw SQL or traces.
"""

from __future__ import annotations

import logging

from .base import AgentState, BaseAgent
from .llm import call_llm

logger = logging.getLogger("clearbank.agent.error_response_generator")


class ErrorResponseGeneratorAgent(BaseAgent):
    """
    LLM agent. Produces a graceful, user-friendly error message when SQL
    retries are exhausted or a DB connection / query failure occurs.
    Does not expose raw SQL or internal stack traces.
    """

    name = "ErrorResponseGeneratorAgent"

    _SYSTEM = (
        "You are a banking assistant error handler. Be helpful and constructive.\n\n"
        "The system was unable to complete the user's request. Generate a polite explanation "
        "and suggest specific corrections based ONLY on the information provided.\n\n"
        "CRITICAL RULES:\n"
        "  ❌ DO NOT invent or suggest column names NOT mentioned in the error\n"
        "  ❌ DO NOT suggest column names that sound similar but different\n"
        "  ✅ ONLY suggest columns explicitly listed in the validator feedback\n"
        "  ✅ Use exact column names from the error message\n"
        "  ✅ Reference table names if provided in the error\n\n"
        "WRONG (making up column names):\n"
        "  'Try using payment_due_date' (if not mentioned in error)\n"
        "  'Consider using name instead of full_name' (if not mentioned)\n\n"
        "RIGHT (using columns from error):\n"
        "  'The error mentions use due_date or paid_at — try: WHERE due_date > today'\n"
        "  'The system suggests filtering by status = upcoming — try adding that'\n\n"
        "Do NOT expose:\n"
        "  - Raw SQL or code\n"
        "  - Stack traces or technical details\n\n"
        "Keep response to 3–4 sentences, friendly, actionable."
    )

    def run(self, state: AgentState) -> AgentState:
        logger.info(
            "[%s] ════════════════════════════════════════\n"
            "[%s] ERROR RESPONSE GENERATION\n"
            "[%s] Query: %s\n"
            "[%s] Attempts made: %d\n"
            "[%s] Error: %.100s\n"
            "[%s] ════════════════════════════════════════",
            self.name, self.name, self.name, state.user_query,
            self.name, state.sql_attempt,
            self.name, state.error,
            self.name
        )

        user_msg = (
            f"User query: {state.user_query}\n\n"
            f"Internal error (do not expose): {state.error}\n\n"
            f"SQL attempts made: {state.sql_attempt}\n\n"
            f"Last validator feedback: {state.validator_feedback if state.validator_feedback else 'None'}"
        )
        state.final_answer = call_llm(self._SYSTEM, user_msg)

        logger.info(
            "[%s] ✅ ERROR RESPONSE GENERATED:\n%s",
            self.name, state.final_answer
        )

        return state
