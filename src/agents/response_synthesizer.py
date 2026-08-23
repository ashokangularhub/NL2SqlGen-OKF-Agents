"""
agents/response_synthesizer.py — Agent 12: ResponseSynthesizerAgent

Single exit point for all successful domain responses. Formats the final
answer with OKF concepts, metric thresholds, and live database results
as a professional, polished markdown response.
"""

from __future__ import annotations

import logging

from .base import AgentState, BaseAgent
from .llm import call_llm

logger = logging.getLogger("clearbank.agent.response_synthesizer")


class ResponseSynthesizerAgent(BaseAgent):
    """
    LLM agent. Single exit point for all successful domain responses.
    Formats the final answer with OKF concepts, metric thresholds, and
    live database results as a professional, polished markdown response.
    """

    name = "ResponseSynthesizerAgent"

    _SYSTEM = (
        "You are a response synthesizer for a banking AI assistant at ClearBank.\n\n"
        "Given the user's query, OKF knowledge context, and (optionally) live "
        "database results, produce a clear, professional final answer:\n"
        "  - Lead with a direct answer to the question\n"
        "  - Present tabular data as a markdown table\n"
        "  - Cite OKF metric thresholds or business rules where relevant\n"
        "  - Keep the tone professional and concise\n"
        "  - If a knowledge base answer is already present, polish and present it"
    )

    @staticmethod
    def _db_to_markdown(db_result: dict) -> str:
        """Format the FastAPI /query response as a markdown table."""
        if not db_result:
            return ""
        if db_result.get("error"):
            return f"*Query error: {db_result['error']}*"
        columns: list = db_result.get("columns", [])
        rows: list = db_result.get("rows", [])
        if not columns or not rows:
            return "*No rows returned.*"
        lines = [
            "| " + " | ".join(str(c) for c in columns) + " |",
            "| " + " | ".join(["---"] * len(columns)) + " |",
        ]
        for row in rows[:30]:
            lines.append(
                "| " + " | ".join("NULL" if v is None else str(v)
                                  for v in row) + " |"
            )
        total = db_result.get("row_count", len(rows))
        if total > 30:
            lines.append(f"\n*…showing 30 of {total} rows*")
        return "\n".join(lines)

    def run(self, state: AgentState) -> AgentState:
        logger.info("[%s] Synthesizing final response.", self.name)
        db_md = self._db_to_markdown(state.db_result)

        user_msg = (
            f"User query: {state.user_query}\n\n"
            f"OKF Context:\n{state.system_context[:1000]}\n\n"
        )
        if state.final_answer:
            user_msg += f"Knowledge Base Answer:\n{state.final_answer}\n\n"
        if db_md:
            user_msg += (
                f"Live Database Results "
                f"(SQL: `{state.generated_sql[:120]}`):\n\n{db_md}\n\n"
            )

        state.final_answer = call_llm(self._SYSTEM, user_msg)
        return state
