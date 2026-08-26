"""
api.py — ClearBank Agent FastAPI Service

Exposes HTTP endpoints that trigger the multi-agent pipeline.
The intent classifier is the entry-point for every request.

Endpoints
---------
POST /classify          — Run the full 12-agent pipeline (primary entry point).
POST /pipeline/run      — Alias for /classify; also runs the full pipeline.
GET  /history/recent    — Return the last N conversation turns.
DELETE /history         — Clear all conversation history.
GET  /health            — Liveness probe.

Run:
    uvicorn src.api:app --reload --port 8081

Or from the project root:
    uvicorn src.api:app --host 0.0.0.0 --port 8081
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import asyncio

from fastapi import FastAPI, HTTPException, Query as QParam, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
import json as _json

# ── Path setup — allow 'agents' package imports ───────────────────────────────
_SRC = Path(__file__).parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# Load .env (OPENAI_API_KEY, OKF_BUNDLE_AGENT_URL, ...) before agents are imported
from dotenv import load_dotenv  # noqa: E402
load_dotenv(_SRC.parent / ".env")

from agents import (  # noqa: E402
    AgentState,
    run_pipeline,
    stream_pipeline,
    _VALIDATION_FAILED,
    MAX_SQL_RETRIES,
    SQLGeneratorAgent,
    SQLValidatorAgent,
)
from conversation_history import ConversationHistory  # noqa: E402
from okf_client import get_okf_client  # noqa: E402

# ── Logging ───────────────────────────────────────────────────────────────────


def setup_logging() -> None:
    """Configure logging to display DEBUG/INFO messages in console."""
    # Create root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    # Remove any existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG)

    # Enhanced format with timestamp and logger name
    formatter = logging.Formatter(
        '%(asctime)s [%(name)s] %(levelname)s: %(message)s',
        datefmt='%H:%M:%S'
    )
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # Set specific loggers
    logging.getLogger("clearbank.api").setLevel(logging.DEBUG)
    logging.getLogger("clearbank.agent").setLevel(logging.DEBUG)
    logging.getLogger("uvicorn").setLevel(logging.INFO)
    logging.getLogger("uvicorn.access").setLevel(logging.INFO)


# Initialize logging on import
setup_logging()

logger = logging.getLogger("clearbank.api")

# ── Conversation history singleton ────────────────────────────────────────────

_history = ConversationHistory()

# ── In-memory session store for multi-turn conversations ──────────────────────
# Maps session_id → list of {"role": "user"|"assistant", "content": "..."}
# Keeps only the last SESSION_HISTORY_MAX_TURNS turns per session.

SESSION_HISTORY_MAX_TURNS = 10  # max turns retained per session
_sessions: dict[str, list[dict]] = {}

# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="ClearBank Agent API",
    description=(
        "FastAPI service exposing the ClearBank 12-agent pipeline. "
        "The intent classifier is the entry-point for every request."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Startup event ─────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup_event():
    """Log service startup."""
    logger.info(
        "[NL2SQLGEN-AGENT] ════════════════════════════════════════\n"
        "[NL2SQLGEN-AGENT] SERVICE STARTUP\n"
        "[NL2SQLGEN-AGENT] ClearBank Agent API started\n"
        "[NL2SQLGEN-AGENT] Listen on http://0.0.0.0:8081\n"
        "[NL2SQLGEN-AGENT] ════════════════════════════════════════"
    )


# ── Request / Response schemas ────────────────────────────────────────────────


class QueryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000,
                       description="User query text")
    session_id: str | None = Field(
        None,
        description=(
            "Optional session identifier. Pass the same value across turns "
            "to enable follow-up / multi-turn conversations."
        ),
    )


class PipelineResponse(BaseModel):
    query: str
    intent: str
    section_type: str
    sql_attempt: int
    generated_sql: str
    final_answer: str
    error: str | None = None


class HistoryEntry(BaseModel):
    id: int
    timestamp: str
    user_query: str
    response: str
    metadata: dict


class HistoryResponse(BaseModel):
    total_turns: int
    turns: list[HistoryEntry]


class GenerateSQLRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000,
                       description="Natural-language intent to translate into SQL")
    domain_hint: str | None = Field(
        None,
        description=(
            "Optional domain override passed straight to the OKF Bundle Agent "
            "(e.g. 'customer_support', 'retail_banking'). If omitted, the OKF "
            "Bundle Agent infers the domain from the query."
        ),
    )


class GenerateSQLResponse(BaseModel):
    success: bool
    sql: str | None = None
    section_type: str = ""
    domain: str = ""
    attempts: int = 0
    validator_feedback: str | None = None
    error: str | None = None


# ── Helper: extract query string from any request format ─────────────────────


async def _resolve_request(
    request: Request, qparam: str | None
) -> tuple[str | None, str | None]:
    """
    Extract the 'query' and optional 'session_id' from (in priority order):
      1. URL query parameter  ?query=...
      2. JSON body            {"query": "...", "session_id": "abc"}
      3. Form field           query=...
      4. Raw body text        (plain string)
    Returns (query, session_id) — either may be None.
    """
    if qparam:
        return qparam.strip() or None, None
    try:
        raw = await request.body()
        if not raw:
            return None, None
        ct = request.headers.get("content-type", "")
        if "application/json" in ct or not ct:
            try:
                data = _json.loads(raw)
                if isinstance(data, dict):
                    q_val: str | None = None
                    for key in ("query", "message"):
                        if key in data:
                            q_val = str(data[key]).strip() or None
                            break
                    sid = data.get("session_id")
                    session_id = str(sid).strip() or None if sid else None
                    return q_val, session_id
            except (_json.JSONDecodeError, KeyError):
                pass
        if "form" in ct:
            form = await request.form()
            v = form.get("query")
            return str(v).strip() or None if v else None, None
        # Last resort: treat entire body as the query string
        text = raw.decode("utf-8", errors="replace").strip()
        return text or None, None
    except Exception:
        return None, None


# Keep old name as alias for any callers outside the two main endpoints
async def _resolve_query(request: Request, qparam: str | None) -> str | None:
    q, _ = await _resolve_request(request, qparam)
    return q


# ── Endpoints ─────────────────────────────────────────────────────────────────


@app.get("/health", tags=["Monitoring"])
async def health() -> dict:
    """Liveness probe — returns service status and LLM mode."""
    api_key_set = bool(os.environ.get("OPENAI_API_KEY", "").strip())
    status_info = {
        "status": "ok",
        "llm_mode": "live" if api_key_set else "mock",
        "model": os.environ.get("OPENAI_MODEL", "gpt-4o"),
    }
    logger.info(
        "[NL2SQLGEN-AGENT] ════════════════════════════════════════\n"
        "[NL2SQLGEN-AGENT] HEALTH CHECK\n"
        "[NL2SQLGEN-AGENT] Status: %s\n"
        "[NL2SQLGEN-AGENT] LLM Mode: %s\n"
        "[NL2SQLGEN-AGENT] ════════════════════════════════════════",
        status_info["status"], status_info["llm_mode"]
    )
    return status_info


@app.post("/generate-sql", response_model=GenerateSQLResponse, tags=["Agents"])
async def generate_sql(payload: GenerateSQLRequest) -> GenerateSQLResponse:
    """
    Schema-aware SQL generation only — does NOT execute the query.

    Reuses the OKF Bundle Agent (section-selection / section-retrieval /
    context-building) plus the SQLGeneratorAgent <-> SQLValidatorAgent retry
    loop (capped at MAX_SQL_RETRIES), then returns the generated SQL without
    invoking SQLExecutorAgent. Intended for callers — e.g. the
    Product-Management-Agent in customer-support-rag-agent — that generate
    SQL here but execute it against their own tool
    (customer-support-mcp-tools' POST /sql-tool).
    """
    okf_client = get_okf_client()
    state = AgentState(user_query=payload.query)
    logger.info("[API /generate-sql] query=%.80s domain_hint=%s",
                payload.query, payload.domain_hint or "none")
    try:
        state.section_type, state.domain = okf_client.select_section(
            payload.query)
        if payload.domain_hint:
            state.domain = payload.domain_hint
        state.okf_content = okf_client.retrieve_section(
            state.section_type, state.domain)
        state.system_context = okf_client.build_context(
            payload.query, state.okf_content, state.domain)

        while state.sql_attempt < MAX_SQL_RETRIES:
            state.sql_attempt += 1
            state = SQLGeneratorAgent().run(state)
            state = SQLValidatorAgent().run(state)

            if state.error == _VALIDATION_FAILED:
                if state.sql_attempt >= MAX_SQL_RETRIES:
                    return GenerateSQLResponse(
                        success=False,
                        section_type=state.section_type,
                        domain=state.domain,
                        attempts=state.sql_attempt,
                        validator_feedback=state.validator_feedback,
                        error=(
                            f"Could not generate a valid SQL query after "
                            f"{MAX_SQL_RETRIES} attempts. "
                            f"Last feedback: {state.validator_feedback}"
                        ),
                    )
                state.error = ""
                continue

            return GenerateSQLResponse(
                success=True,
                sql=state.generated_sql,
                section_type=state.section_type,
                domain=state.domain,
                attempts=state.sql_attempt,
                validator_feedback=state.validator_feedback or None,
            )
    except Exception as exc:
        logger.error("[API /generate-sql] error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/classify", response_model=PipelineResponse, tags=["Agents"])
async def classify_intent(
    request: Request,
    query: str | None = QParam(
        None, description="User query text (alternative to JSON body)"),
) -> PipelineResponse:
    """
    Run the full 12-agent pipeline starting from the IntentClassifierAgent.

    This is the primary entry point that mirrors the agent_flow_diagram:
      IntentClassifierAgent
        ├── general → GeneralQueryAgent → final_answer
        └── domain  → OrchestrationAgent
                        ├── SectionSelectionAgent
                        ├── SectionRetrievalAgent
                        ├── ContextBuilderAgent
                        ├── KnowledgeBaseAgent  (Runbooks / Datasets branch)
                        └── SQLGeneratorAgent → SQLValidatorAgent
                              → SQLExecutorAgent → FastAPI :8000/query → ResponseSynthesizerAgent
                              → ErrorResponseGeneratorAgent (on failure)

    Accepts the query via **any** of these formats:

    - JSON body: ``{"query": "show delinquent loans"}``
    - URL parameter: ``?query=show+delinquent+loans``
    - Form field: ``query=show+delinquent+loans``
    - Raw plain-text body (the query string itself)
    """
    q, session_id = await _resolve_request(request, query)
    if not q:
        raise HTTPException(
            status_code=422,
            detail='Provide the query via JSON body {"query": "...", "session_id": "opt"}, ?query= URL param, form field, or raw body text.',
        )
    logger.info("[API /classify] session=%s query=%.80s",
                session_id or "none", q)
    session_history = _sessions.get(session_id, []) if session_id else []
    try:
        final_state: AgentState = run_pipeline(
            q, conversation_history=session_history)
        error_out = (
            final_state.error
            if final_state.error and final_state.error != _VALIDATION_FAILED
            else None
        )
        _history.add_turn(
            user_query=q,
            response=final_state.final_answer,
            intent=final_state.intent,
            section_type=final_state.section_type,
            sql_attempts=final_state.sql_attempt,
            error=error_out,
        )
        if session_id:
            turns = _sessions.setdefault(session_id, [])
            turns.append({"role": "user", "content": q})
            turns.append(
                {"role": "assistant", "content": final_state.final_answer})
            # Trim to last SESSION_HISTORY_MAX_TURNS pairs (2 messages per turn)
            if len(turns) > SESSION_HISTORY_MAX_TURNS * 2:
                _sessions[session_id] = turns[-(
                    SESSION_HISTORY_MAX_TURNS * 2):]
        return PipelineResponse(
            query=q,
            intent=final_state.intent,
            section_type=final_state.section_type,
            sql_attempt=final_state.sql_attempt,
            generated_sql=final_state.generated_sql,
            final_answer=final_state.final_answer,
            error=error_out,
        )
    except Exception as exc:
        logger.error("[API /classify] error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/classify/stream", tags=["Agents"])
async def classify_intent_stream(
    request: Request,
    query: str | None = QParam(
        None, description="User query text (alternative to JSON body)"),
) -> StreamingResponse:
    """
    Streaming variant of POST /classify.

    Returns ``text/event-stream`` (SSE). One event is emitted before and
    after each agent so clients can show live progress. The last event
    (``event == "final"``) carries the full pipeline result with the same
    fields as ``PipelineResponse``.

    Event shapes
    ------------
    ``pipeline_start`` : ``{"event": "pipeline_start", "query": "..."}``
    ``agent_start``    : ``{"event": "agent_start", "agent": "...", ...}``
    ``agent_done``     : ``{"event": "agent_done",  "agent": "...", ...}``
    ``final``          : ``{"event": "final", "query": ..., "final_answer": ..., ...}``
    """
    q, session_id = await _resolve_request(request, query)
    if not q:
        raise HTTPException(
            status_code=422,
            detail='Provide the query via JSON body {"query": "...", "session_id": "opt"}, ?query= URL param, form field, or raw body text.',
        )
    logger.info("[API /classify/stream] session=%s query=%.80s",
                session_id or "none", q)
    session_history = _sessions.get(session_id, []) if session_id else []

    _DONE = object()

    def _next_chunk(gen):
        try:
            return next(gen)
        except StopIteration:
            return _DONE

    async def _event_generator():
        loop = asyncio.get_event_loop()
        gen = stream_pipeline(q, conversation_history=session_history)
        final_data: dict | None = None
        while True:
            chunk = await loop.run_in_executor(None, _next_chunk, gen)
            if chunk is _DONE:
                break
            yield chunk
            # Capture final event so we can persist history after streaming
            try:
                parsed = _json.loads(chunk[6:])  # strip "data: "
                if parsed.get("event") == "final":
                    final_data = parsed
            except Exception:
                pass
        # Persist to conversation history and session store after stream ends
        if final_data:
            error_out = final_data.get("error")
            _history.add_turn(
                user_query=q,
                response=final_data.get("final_answer", ""),
                intent=final_data.get("intent", ""),
                section_type=final_data.get("section_type", ""),
                sql_attempts=final_data.get("sql_attempt", 0),
                error=error_out,
            )
            if session_id:
                turns = _sessions.setdefault(session_id, [])
                turns.append({"role": "user", "content": q})
                turns.append({"role": "assistant",
                              "content": final_data.get("final_answer", "")})
                if len(turns) > SESSION_HISTORY_MAX_TURNS * 2:
                    _sessions[session_id] = turns[-(
                        SESSION_HISTORY_MAX_TURNS * 2):]

    return StreamingResponse(_event_generator(), media_type="text/event-stream")


@app.post("/pipeline/run", response_model=PipelineResponse, tags=["Pipeline"])
async def run_full_pipeline(
    request: Request,
    query: str | None = QParam(
        None, description="User query text (alternative to JSON body)"),
) -> PipelineResponse:
    """
    Trigger the full 12-agent pipeline starting from the IntentClassifierAgent.

    Accepts the query via **any** of these formats:

    - JSON body: ``{"query": "show delinquent loans"}``
    - URL parameter: ``?query=show+delinquent+loans``
    - Form field: ``query=show+delinquent+loans``
    - Raw plain-text body (the query string itself)

    Pipeline flow:
      IntentClassifierAgent
        ├── general → GeneralQueryAgent
        └── domain  → OrchestrationAgent
                        ├── SectionSelectionAgent → SectionRetrievalAgent → ContextBuilderAgent
                        ├── KnowledgeBaseAgent  (Runbooks / Datasets branch)
                        └── SQLGeneratorAgent → SQLValidatorAgent → SQLExecutorAgent
    """
    q, session_id = await _resolve_request(request, query)
    if not q:
        raise HTTPException(
            status_code=422,
            detail='Provide the query via JSON body {"query": "...", "session_id": "opt"}, ?query= URL param, form field, or raw body text.',
        )
    logger.info("[API /pipeline/run] session=%s query=%.80s",
                session_id or "none", q)
    session_history = _sessions.get(session_id, []) if session_id else []
    try:
        final_state: AgentState = run_pipeline(
            q, conversation_history=session_history)
        error_out = (
            final_state.error
            if final_state.error and final_state.error != _VALIDATION_FAILED
            else None
        )
        _history.add_turn(
            user_query=q,
            response=final_state.final_answer,
            intent=final_state.intent,
            section_type=final_state.section_type,
            sql_attempts=final_state.sql_attempt,
            error=error_out,
        )
        if session_id:
            turns = _sessions.setdefault(session_id, [])
            turns.append({"role": "user", "content": q})
            turns.append(
                {"role": "assistant", "content": final_state.final_answer})
            if len(turns) > SESSION_HISTORY_MAX_TURNS * 2:
                _sessions[session_id] = turns[-(
                    SESSION_HISTORY_MAX_TURNS * 2):]
        return PipelineResponse(
            query=q,
            intent=final_state.intent,
            section_type=final_state.section_type,
            sql_attempt=final_state.sql_attempt,
            generated_sql=final_state.generated_sql,
            final_answer=final_state.final_answer,
            error=error_out,
        )
    except Exception as exc:
        logger.error("[API /pipeline/run] error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/history/recent", response_model=HistoryResponse, tags=["History"])
async def get_history(n: int = 10) -> HistoryResponse:
    """
    Return the last *n* conversation turns (default 10, max 100).
    """
    n = min(max(n, 1), 100)
    turns = _history.get_recent(n)
    return HistoryResponse(
        total_turns=_history.total_turns,
        turns=[HistoryEntry(**t) for t in turns],
    )


@app.delete("/history", tags=["History"])
async def clear_history() -> dict:
    """
    Clear all stored conversation history.
    """
    _history.clear()
    logger.info("[API /history] cleared.")
    return {"status": "cleared", "total_turns": 0}
