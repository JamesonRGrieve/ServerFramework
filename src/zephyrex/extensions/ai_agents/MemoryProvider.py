"""Long-term agent memory providers.

Long-term memory is durable, unbounded, and recalled on demand — distinct from
short-term memory (the bounded per-turn working set in ``AgentMemoryModel``).
It lives behind a provider abstraction so the backend can evolve without
touching the agent/turn code:

- :class:`SQLiteMemoryProvider` — the default: a *plain separate SQLite
  database* (not the app's main DB), agent-scoped by an ``agent_id`` column.
  Recall is keyword + recency for now.
- A future ``PgVectorMemoryProvider`` implements the same interface with
  embedding storage + vector similarity ``recall`` — the ``store``/``recall``
  seam is where that drops in.

The provider is agent-scoped in its API (every call takes ``agent_id``), which
is what "agent-level" means here: one store, partitioned per agent.
"""

import json
import os
import sqlite3
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from zephyrex.lib.Environment import env

# Default location for the separate long-term-memory SQLite DB. Overridable via
# the AGENT_LTM_SQLITE_PATH env var (a deployment/operator placement choice).
DEFAULT_SQLITE_PATH = "agent_long_term_memory.db"


class AbstractMemoryProvider(ABC):
    """Agent-scoped long-term memory backend. Every method takes ``agent_id``."""

    @abstractmethod
    def store(
        self,
        agent_id: str,
        content: str,
        *,
        key: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Persist a long-term memory; return its id."""

    @abstractmethod
    def recall(
        self, agent_id: str, query: str, *, limit: int = 5
    ) -> List[Dict[str, Any]]:
        """Return the memories most relevant to ``query`` (most recent first
        among matches). A future vector backend ranks by embedding similarity."""

    @abstractmethod
    def recent(self, agent_id: str, *, limit: int = 10) -> List[Dict[str, Any]]:
        """Return the agent's most recent long-term memories."""

    @abstractmethod
    def forget(self, agent_id: str, memory_id: str) -> bool:
        """Delete one memory; return whether a row was removed."""


class SQLiteMemoryProvider(AbstractMemoryProvider):
    """Long-term memory in a standalone SQLite file, scoped by ``agent_id``.

    Uses a fresh connection per operation so it is safe to call from the
    monitor loop and the fire-and-forget conversation hook thread alike. Recall
    is keyword (LIKE) + recency; the schema carries an ``embedding`` column that
    stays NULL until a vector backend populates it.
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path or env("AGENT_LTM_SQLITE_PATH") or DEFAULT_SQLITE_PATH
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS long_term_memories (
                    id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    key TEXT,
                    content TEXT NOT NULL,
                    metadata TEXT,
                    embedding BLOB,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_ltm_agent "
                "ON long_term_memories (agent_id, created_at)"
            )

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "id": row["id"],
            "key": row["key"],
            "content": row["content"],
            "metadata": json.loads(row["metadata"]) if row["metadata"] else None,
            "created_at": row["created_at"],
        }

    def store(
        self,
        agent_id: str,
        content: str,
        *,
        key: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        memory_id = str(uuid.uuid4())
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO long_term_memories "
                "(id, agent_id, key, content, metadata, embedding, created_at) "
                "VALUES (?, ?, ?, ?, ?, NULL, ?)",
                (
                    memory_id,
                    agent_id,
                    key,
                    content,
                    json.dumps(metadata) if metadata else None,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        return memory_id

    def recall(
        self, agent_id: str, query: str, *, limit: int = 5
    ) -> List[Dict[str, Any]]:
        if not query or not query.strip():
            return self.recent(agent_id, limit=limit)
        like = f"%{query.strip()}%"
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM long_term_memories WHERE agent_id = ? "
                "AND (content LIKE ? OR key LIKE ?) "
                "ORDER BY created_at DESC LIMIT ?",
                (agent_id, like, like, limit),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def recent(self, agent_id: str, *, limit: int = 10) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM long_term_memories WHERE agent_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (agent_id, limit),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def forget(self, agent_id: str, memory_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM long_term_memories WHERE agent_id = ? AND id = ?",
                (agent_id, memory_id),
            )
            return cur.rowcount > 0


def default_memory_provider() -> AbstractMemoryProvider:
    """The configured long-term memory provider. SQLite today; the return type
    is the abstraction so a pgvector provider can replace it via config later."""
    return SQLiteMemoryProvider()
