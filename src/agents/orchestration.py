"""
agents/orchestration.py — Agent 3: OrchestrationAgent

Coordinator agent (no LLM calls). State machine that drives the full domain
pipeline. Calls OKF Bundle Agent service for Steps 1–3, then branches to
KB sub-pipeline or SQL retry loop based on section classification.

NOTE: This agent now uses okf-bundle-agent service for OKF operations.
      Ensure OKF_BUNDLE_AGENT_URL environment variable is set.
"""

from __future__ import annotations

import logging

from .base import AgentState, BaseAgent, MAX_SQL_RETRIES, _VALIDATION_FAILED
from .sql_generator import SQLGeneratorAgent
from .sql_validator import SQLValidatorAgent
from .sql_executor import SQLExecutorAgent
from .error_response_generator import ErrorResponseGeneratorAgent
from .response_synthesizer import ResponseSynthesizerAgent
from okf_client import get_okf_client

logger = logging.getLogger("clearbank.agent.orchestration")


class OrchestrationAgent(BaseAgent):
    """
    Coordinator agent (no LLM calls). State machine that drives the full
    domain pipeline. Uses OKF Bundle Agent service for Steps 1–3, then
    branches to KB or SQL pipeline based on classification.
    """

    name = "OrchestrationAgent"

    def run(self, state: AgentState) -> AgentState:
        logger.info("[%s] Domain pipeline start.", self.name)

        okf_client = get_okf_client()

        try:
            # ── Step 1: Section Selection (via OKF Bundle Agent) ───────
            logger.info(
                "[%s] Calling OKF Bundle Agent for section selection", self.name)
            state.section_type = okf_client.select_section(state.user_query)

            # ── Step 2: Section Retrieval (via OKF Bundle Agent) ───────
            logger.info(
                "[%s] Calling OKF Bundle Agent for section retrieval", self.name)
            state.okf_content = okf_client.retrieve_section(state.section_type)

            # ── Step 3: Context Building (via OKF Bundle Agent) ────────
            logger.info(
                "[%s] Calling OKF Bundle Agent for context building", self.name)
            state.system_context = okf_client.build_context(
                state.user_query,
                state.okf_content
            )

            # ── Branch A: Runbooks / Datasets → Knowledge Base ────────────
            if state.section_type in ("Runbooks", "Datasets"):
                logger.info("[%s] → KB branch (%s).",
                            self.name, state.section_type)
                # Answer from knowledge base using OKF service
                state.final_answer = okf_client.query_knowledge_base(
                    state.user_query,
                    state.okf_content
                )
                return ResponseSynthesizerAgent().run(state)

            # ── Branch B: Tables / Metrics → SQL pipeline ─────────────────
            logger.info("[%s] → SQL pipeline branch (%s).",
                        self.name, state.section_type)
            state.sql_attempt = 0
            state.validator_feedback = ""
            state.error = ""

            while state.sql_attempt < MAX_SQL_RETRIES:
                state.sql_attempt += 1
                logger.info(
                    "[%s] SQL attempt %d/%d.", self.name, state.sql_attempt, MAX_SQL_RETRIES
                )

                state = SQLGeneratorAgent().run(state)
                state = SQLValidatorAgent().run(state)

                if state.error == _VALIDATION_FAILED:
                    if state.sql_attempt >= MAX_SQL_RETRIES:
                        logger.warning(
                            "[%s] Validation failed after %d attempts → ErrorResponseGenerator.",
                            self.name,
                            MAX_SQL_RETRIES,
                        )
                        state.error = (
                            f"Could not generate a valid SQL query after "
                            f"{MAX_SQL_RETRIES} attempts. "
                            f"Last feedback: {state.validator_feedback}"
                        )
                        return ErrorResponseGeneratorAgent().run(state)
                    state.error = ""
                    continue

                state = SQLExecutorAgent().run(state)

                if state.error:
                    logger.warning(
                        "[%s] DB execution error → ErrorResponseGenerator.", self.name
                    )
                    return ErrorResponseGeneratorAgent().run(state)

                return ResponseSynthesizerAgent().run(state)

            # Safety net — should be unreachable
            state.error = "Pipeline internal error: retry loop exited without result."
            return ErrorResponseGeneratorAgent().run(state)

        except Exception as exc:
            logger.error("[%s] OKF Bundle Agent error: %s", self.name, exc)
            state.error = f"OKF service error: {str(exc)}"
            return ErrorResponseGeneratorAgent().run(state)
