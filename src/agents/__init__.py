"""
agents/__init__.py — Public API for the agents package.

All agent classes and shared types are re-exported here so callers only
need: `from agents import IntentClassifierAgent, AgentState, run_pipeline`
"""

from __future__ import annotations

import json
from typing import Generator

from .base import AgentState, BaseAgent, _VALIDATION_FAILED, MAX_SQL_RETRIES
from .llm import call_llm
from .intent_classifier import IntentClassifierAgent
from .general_query import GeneralQueryAgent
from .sql_generator import SQLGeneratorAgent
from .sql_validator import SQLValidatorAgent
from .sql_executor import SQLExecutorAgent
from .error_response_generator import ErrorResponseGeneratorAgent
from .response_synthesizer import ResponseSynthesizerAgent
from .orchestration import OrchestrationAgent
from okf_client import get_okf_client

import logging

logger = logging.getLogger("clearbank.pipeline")


def run_pipeline(
    query: str,
    conversation_history: list[dict] | None = None,
) -> AgentState:
    """
    Run the full 12-agent pipeline for a user query.
    Entry point: IntentClassifierAgent → GeneralQueryAgent | OrchestrationAgent.
    Returns the final AgentState with final_answer populated.

    Parameters
    ----------
    conversation_history : list of {"role": "user"|"assistant", "content": "..."} dicts
        Prior turns from the current session.  Passed into AgentState so
        context-aware agents (IntentClassifier, GeneralQuery, SQLGenerator)
        can resolve follow-up references.
    """
    state = AgentState(
        user_query=query,
        conversation_history=conversation_history or [],
    )
    logger.info("=== Pipeline START: %.80s", query)

    state = IntentClassifierAgent().run(state)

    if state.intent == "general":
        logger.info("=== Routing to GeneralQueryAgent.")
        state = GeneralQueryAgent().run(state)
    else:
        state = OrchestrationAgent().run(state)

    logger.info("=== Pipeline END — answer: %d chars.",
                len(state.final_answer))
    return state


def stream_pipeline(
    query: str,
    conversation_history: list[dict] | None = None,
) -> Generator[str, None, None]:
    """
    Generator variant of run_pipeline that yields SSE-formatted strings
    (``data: {...}\\n\\n``) as each agent completes.

    Events
    ------
    ``pipeline_start``  — emitted once at the beginning.
    ``agent_start``     — before each agent runs.
    ``agent_done``      — after each agent completes.
    ``final``           — last event; payload mirrors PipelineResponse.
    """

    def _sse(data: dict) -> str:
        return f"data: {json.dumps(data)}\n\n"

    state = AgentState(
        user_query=query,
        conversation_history=conversation_history or [],
    )
    logger.info("=== Stream Pipeline START: %.80s", query)

    yield _sse({"event": "pipeline_start", "query": query})

    # ── Agent 1: Intent classification ───────────────────────────────────────
    yield _sse({"event": "agent_start", "agent": "IntentClassifierAgent"})
    state = IntentClassifierAgent().run(state)
    yield _sse({"event": "agent_done", "agent": "IntentClassifierAgent",
                "intent": state.intent})

    if state.intent == "general":
        # ── General branch ────────────────────────────────────────────────────
        yield _sse({"event": "agent_start", "agent": "GeneralQueryAgent"})
        state = GeneralQueryAgent().run(state)
        yield _sse({"event": "agent_done", "agent": "GeneralQueryAgent"})

    else:
        # ── Domain branch (via OKF Bundle Agent service) ──────────────────
        okf_client = get_okf_client()

        try:
            yield _sse({"event": "agent_start", "agent": "SectionSelectionAgent"})
            state.section_type, state.domain = okf_client.select_section(
                state.user_query)
            yield _sse({"event": "agent_done", "agent": "SectionSelectionAgent",
                        "section_type": state.section_type, "domain": state.domain})

            yield _sse({"event": "agent_start", "agent": "SectionRetrievalAgent"})
            state.okf_content = okf_client.retrieve_section(
                state.section_type, state.domain)
            yield _sse({"event": "agent_done", "agent": "SectionRetrievalAgent"})

            yield _sse({"event": "agent_start", "agent": "ContextBuilderAgent"})
            state.system_context = okf_client.build_context(
                state.user_query, state.okf_content, state.domain)
            yield _sse({"event": "agent_done", "agent": "ContextBuilderAgent"})
        except Exception as exc:
            logger.error("=== OKF Bundle Agent error: %s", exc)
            state.error = f"OKF service error: {str(exc)}"
            yield _sse({"event": "agent_start",
                        "agent": "ErrorResponseGeneratorAgent"})
            state = ErrorResponseGeneratorAgent().run(state)
            yield _sse({"event": "agent_done",
                        "agent": "ErrorResponseGeneratorAgent"})
            error_out = state.error if state.error != _VALIDATION_FAILED else None
            yield _sse({
                "event": "final",
                "query": query,
                "intent": state.intent,
                "section_type": state.section_type,
                "sql_attempt": state.sql_attempt,
                "generated_sql": state.generated_sql,
                "final_answer": state.final_answer,
                "error": error_out,
            })
            return

        if state.section_type in ("Runbooks", "Datasets"):
            # ── KB sub-pipeline (via OKF Bundle Agent service) ─────────────────
            yield _sse({"event": "agent_start", "agent": "KnowledgeBaseAgent"})
            state.final_answer = okf_client.query_knowledge_base(
                state.user_query, state.okf_content)
            yield _sse({"event": "agent_done", "agent": "KnowledgeBaseAgent"})

            yield _sse({"event": "agent_start", "agent": "ResponseSynthesizerAgent"})
            state = ResponseSynthesizerAgent().run(state)
            yield _sse({"event": "agent_done", "agent": "ResponseSynthesizerAgent"})

        else:
            # ── SQL retry loop ────────────────────────────────────────────────
            state.sql_attempt = 0
            state.validator_feedback = ""
            state.error = ""

            while state.sql_attempt < MAX_SQL_RETRIES:
                state.sql_attempt += 1

                yield _sse({"event": "agent_start", "agent": "SQLGeneratorAgent",
                            "attempt": state.sql_attempt})
                state = SQLGeneratorAgent().run(state)
                yield _sse({"event": "agent_done", "agent": "SQLGeneratorAgent"})

                yield _sse({"event": "agent_start", "agent": "SQLValidatorAgent",
                            "attempt": state.sql_attempt})
                state = SQLValidatorAgent().run(state)
                yield _sse({"event": "agent_done", "agent": "SQLValidatorAgent"})

                if state.error == _VALIDATION_FAILED:
                    if state.sql_attempt >= MAX_SQL_RETRIES:
                        state.error = (
                            f"Could not generate a valid SQL query after "
                            f"{MAX_SQL_RETRIES} attempts. "
                            f"Last feedback: {state.validator_feedback}"
                        )
                        yield _sse({"event": "agent_start",
                                    "agent": "ErrorResponseGeneratorAgent"})
                        state = ErrorResponseGeneratorAgent().run(state)
                        yield _sse({"event": "agent_done",
                                    "agent": "ErrorResponseGeneratorAgent"})
                        break
                    state.error = ""
                    continue

                yield _sse({"event": "agent_start", "agent": "SQLExecutorAgent"})
                state = SQLExecutorAgent().run(state)
                yield _sse({"event": "agent_done", "agent": "SQLExecutorAgent"})

                if state.error:
                    yield _sse({"event": "agent_start",
                                "agent": "ErrorResponseGeneratorAgent"})
                    state = ErrorResponseGeneratorAgent().run(state)
                    yield _sse({"event": "agent_done",
                                "agent": "ErrorResponseGeneratorAgent"})
                    break

                yield _sse({"event": "agent_start",
                            "agent": "ResponseSynthesizerAgent"})
                state = ResponseSynthesizerAgent().run(state)
                yield _sse({"event": "agent_done",
                            "agent": "ResponseSynthesizerAgent"})
                break

    error_out = (
        state.error
        if state.error and state.error != _VALIDATION_FAILED
        else None
    )
    logger.info("=== Stream Pipeline END — answer: %d chars.",
                len(state.final_answer))
    yield _sse({
        "event": "final",
        "query": query,
        "intent": state.intent,
        "section_type": state.section_type,
        "sql_attempt": state.sql_attempt,
        "generated_sql": state.generated_sql,
        "final_answer": state.final_answer,
        "error": error_out,
    })


__all__ = [
    "AgentState",
    "BaseAgent",
    "call_llm",
    "run_pipeline",
    "stream_pipeline",
    "_VALIDATION_FAILED",
    "IntentClassifierAgent",
    "GeneralQueryAgent",
    "SQLGeneratorAgent",
    "SQLValidatorAgent",
    "SQLExecutorAgent",
    "ErrorResponseGeneratorAgent",
    "ResponseSynthesizerAgent",
    "OrchestrationAgent",
]
