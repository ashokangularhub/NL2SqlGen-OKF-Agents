"""
conversation_history.py — Persistent conversation history manager.

Stores every user query + agent response as a JSON array in
``conversation_history.json`` at the project root.

Each entry schema:
{
  "id": 1,                            // sequential turn number
  "timestamp": "2026-07-25T10:30:00", // ISO-8601 UTC
  "user_query": "Show delinquent loans",
  "response": "Here are the delinquent loans…",
  "metadata": {
    "intent": "domain",
    "section_type": "Tables",
    "sql_attempts": 1,
    "error": null
  }
}
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("clearbank.history")

# Default path: <project_root>/conversation_history.json
_DEFAULT_PATH = Path(__file__).parent.parent / "conversation_history.json"


class ConversationHistory:
    """
    Load-on-init, append-and-save manager for a JSON conversation log.

    Parameters
    ----------
    path : Path | str | None
        Location of the JSON file.  Defaults to ``conversation_history.json``
        in the project root.  The file is created automatically if absent.
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path else _DEFAULT_PATH
        self._turns: list[dict] = self._load()

    # ── Public API ─────────────────────────────────────────────────────────

    def add_turn(
        self,
        user_query: str,
        response: str,
        *,
        intent: str = "",
        section_type: str = "",
        sql_attempts: int = 0,
        error: str | None = None,
    ) -> dict:
        """Append a new turn and persist the file.  Returns the saved entry."""
        entry: dict = {
            "id": len(self._turns) + 1,
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "user_query": user_query,
            "response": response,
            "metadata": {
                "intent": intent or None,
                "section_type": section_type or None,
                "sql_attempts": sql_attempts or None,
                "error": error or None,
            },
        }
        self._turns.append(entry)
        self._save()
        logger.info("History: saved turn #%d.", entry["id"])
        return entry

    def get_all(self) -> list[dict]:
        """Return all turns (oldest first)."""
        return list(self._turns)

    def get_recent(self, n: int = 10) -> list[dict]:
        """Return the *n* most recent turns."""
        return self._turns[-n:]

    def clear(self) -> None:
        """Erase all history and overwrite the file."""
        self._turns = []
        self._save()
        logger.info("History: cleared.")

    @property
    def path(self) -> Path:
        return self._path

    @property
    def total_turns(self) -> int:
        return len(self._turns)

    # ── Internal helpers ───────────────────────────────────────────────────

    def _load(self) -> list[dict]:
        if not self._path.exists():
            return []
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
            logger.warning(
                "History file had unexpected shape; starting fresh.")
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(
                "Could not read history file: %s — starting fresh.", exc)
        return []

    def _save(self) -> None:
        try:
            self._path.write_text(
                json.dumps(self._turns, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.error("Could not write history file: %s", exc)
