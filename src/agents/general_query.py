"""
agents/general_query.py — Agent 2: GeneralQueryAgent

Answers free-form, non-banking questions directly without routing
through the OKF pipeline. Writes state.final_answer.
"""

from __future__ import annotations

import logging

from .base import AgentState, BaseAgent
from .llm import call_llm

logger = logging.getLogger("clearbank.agent.general_query")


class GeneralQueryAgent(BaseAgent):
    """
    LLM agent. Answers free-form, non-domain questions directly.
    Bypasses the OKF pipeline entirely and writes final_answer.
    """

    name = "GeneralQueryAgent"

    _SYSTEM = (
        "You are a helpful AI assistant. "
        "Answer the user's question concisely and accurately."
    )

    def run(self, state: AgentState) -> AgentState:
        logger.info("[%s] Answering general query.", self.name)
        state.final_answer = call_llm(
            self._SYSTEM,
            state.user_query,
            history=state.conversation_history or None,
        )
        return state
