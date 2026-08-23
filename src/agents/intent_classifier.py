"""
agents/intent_classifier.py — Agent 1: IntentClassifierAgent

Classifies the user query as 'domain' (banking-specific) or 'general'
and writes the result to state.intent.
"""

from __future__ import annotations

import logging

from .base import AgentState, BaseAgent
from .llm import call_llm

logger = logging.getLogger("clearbank.agent.intent_classifier")


class IntentClassifierAgent(BaseAgent):
    """
    LLM agent. Classifies user query as 'domain' (banking-specific) or
    'general' (open-ended). Routes to OrchestrationAgent or GeneralQueryAgent.
    """

    name = "IntentClassifierAgent"

    _SYSTEM = (
        "You are an intent classifier for a banking AI assistant.\n\n"
        "Classify the user's query as one of:\n"
        "  - 'domain'  : banking-specific (loans, accounts, KYC, AML, transactions, "
        "metrics, runbooks, flags, customers)\n"
        "  - 'general' : open-ended, non-banking question\n\n"
        "Respond ONLY with valid JSON: {\"intent\": \"domain\"} or {\"intent\": \"general\"}"
    )

    def run(self, state: AgentState) -> AgentState:
        logger.info("[%s] Classifying: %.60s…", self.name, state.user_query)
        result = call_llm(
            self._SYSTEM,
            state.user_query,
            json_mode=True,
            history=state.conversation_history or None,
        )
        state.intent = (
            result.get("intent", "domain") if isinstance(
                result, dict) else "domain"
        )
        logger.info("[%s] → intent=%s", self.name, state.intent)
        return state
