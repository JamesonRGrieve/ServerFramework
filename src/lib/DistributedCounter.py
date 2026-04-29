"""Distributed counter primitive (Item 69).

Provides a multi-process atomic counter with `INCR ... WHERE counter < limit RETURNING`
semantics. The shared canonical primitive consumed by token-bucket rate limiting
(Item 17), atomic quota decrement (Item 19), and per-tenant fairness scheduling
(Item 57).

Two backends are provided:
- :class:`InMemoryDistributedCounter`: process-local, asyncio.Lock-guarded, suitable
  for single-process tests and dev.
- :class:`PostgresDistributedCounter`: backed by an UPDATE...WHERE...RETURNING
  statement against a `distributed_counter` table. The DB session is injected via
  a `_session_provider` callable so this module imposes no top-level SQLAlchemy
  bind.

SQL schema (canonical) for the Postgres backend::

    CREATE TABLE distributed_counter (
        key         TEXT PRIMARY KEY,
        period_key  TEXT,
        consumed    BIGINT NOT NULL DEFAULT 0,
        "limit"     BIGINT NOT NULL,
        updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX ix_distributed_counter_period ON distributed_counter (period_key);
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Callable, Dict, Optional, Tuple


class CounterExhaustedError(Exception):
    """Raised when an attempt to consume from a counter would exceed the limit."""


class DistributedCounter(ABC):
    """Abstract distributed counter primitive."""

    def __init__(
        self,
        key: str,
        limit: int,
        period_key: Optional[str] = None,
    ) -> None:
        self.key = key
        self.limit = limit
        self.period_key = period_key

    @abstractmethod
    async def try_consume(self, amount: int = 1) -> bool:
        """Atomically attempt to consume ``amount`` units.

        Returns True on success and False if the limit would be exceeded.
        """

    @abstractmethod
    async def release(self, amount: int) -> None:
        """Credit ``amount`` units back to the counter (used by pre-estimate true-up)."""

    @abstractmethod
    async def reset(self, period_key: str) -> None:
        """Reset the counter for a new period."""

    @abstractmethod
    async def consumed(self) -> int:
        """Return the currently consumed amount."""


# ---------------------------------------------------------------------------
# In-memory backend
# ---------------------------------------------------------------------------


class InMemoryDistributedCounter(DistributedCounter):
    """A process-local distributed counter, intended for tests and single-process use.

    State is shared across all instances with the same ``(key, period_key)`` tuple
    inside a single process via class-level dictionaries.
    """

    _state: Dict[Tuple[str, Optional[str]], int] = {}
    _locks: Dict[Tuple[str, Optional[str]], asyncio.Lock] = {}

    def __init__(
        self,
        key: str,
        limit: int,
        period_key: Optional[str] = None,
    ) -> None:
        super().__init__(key, limit, period_key)

    @classmethod
    def _lock_for(cls, key: Tuple[str, Optional[str]]) -> asyncio.Lock:
        lock = cls._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            cls._locks[key] = lock
        return lock

    @property
    def _composite_key(self) -> Tuple[str, Optional[str]]:
        return (self.key, self.period_key)

    async def try_consume(self, amount: int = 1) -> bool:
        if amount <= 0:
            return True
        async with self._lock_for(self._composite_key):
            current = self._state.get(self._composite_key, 0)
            if current + amount > self.limit:
                return False
            self._state[self._composite_key] = current + amount
            return True

    async def release(self, amount: int) -> None:
        if amount <= 0:
            return
        async with self._lock_for(self._composite_key):
            current = self._state.get(self._composite_key, 0)
            self._state[self._composite_key] = max(0, current - amount)

    async def reset(self, period_key: str) -> None:
        old_key = self._composite_key
        async with self._lock_for(old_key):
            self._state.pop(old_key, None)
        self.period_key = period_key
        # Pre-create lock for the new period for symmetry.
        self._lock_for(self._composite_key)

    async def consumed(self) -> int:
        async with self._lock_for(self._composite_key):
            return self._state.get(self._composite_key, 0)

    @classmethod
    def _reset_all(cls) -> None:
        """Test helper: drop all in-memory state."""
        cls._state.clear()
        cls._locks.clear()


# ---------------------------------------------------------------------------
# Postgres backend
# ---------------------------------------------------------------------------


SessionProvider = Callable[[], object]


class PostgresDistributedCounter(DistributedCounter):
    """Postgres-backed distributed counter.

    Executes::

        UPDATE distributed_counter
           SET consumed = consumed + :amount,
               updated_at = now()
         WHERE key = :key
           AND consumed + :amount <= "limit"
        RETURNING consumed;

    DB binding is deferred via ``session_provider`` so importing this module does
    not pull SQLAlchemy or a live DB into scope.
    """

    def __init__(
        self,
        key: str,
        limit: int,
        period_key: Optional[str] = None,
        session_provider: Optional[SessionProvider] = None,
    ) -> None:
        super().__init__(key, limit, period_key)
        self._session_provider = session_provider

    def _session(self):
        if self._session_provider is None:
            raise RuntimeError(
                "PostgresDistributedCounter requires a session_provider injection "
                "before any database operation."
            )
        return self._session_provider()

    async def try_consume(self, amount: int = 1) -> bool:
        from sqlalchemy import text  # local import: keep module import light

        if amount <= 0:
            return True
        session = self._session()
        # Ensure row exists.
        session.execute(
            text(
                "INSERT INTO distributed_counter (key, period_key, consumed, \"limit\")"
                " VALUES (:key, :period_key, 0, :limit)"
                " ON CONFLICT (key) DO NOTHING"
            ),
            {"key": self.key, "period_key": self.period_key, "limit": self.limit},
        )
        result = session.execute(
            text(
                "UPDATE distributed_counter SET consumed = consumed + :amount,"
                " updated_at = now() WHERE key = :key"
                " AND consumed + :amount <= \"limit\" RETURNING consumed"
            ),
            {"amount": amount, "key": self.key},
        ).fetchone()
        session.commit()
        return result is not None

    async def release(self, amount: int) -> None:
        from sqlalchemy import text

        if amount <= 0:
            return
        session = self._session()
        session.execute(
            text(
                "UPDATE distributed_counter"
                " SET consumed = GREATEST(0, consumed - :amount), updated_at = now()"
                " WHERE key = :key"
            ),
            {"amount": amount, "key": self.key},
        )
        session.commit()

    async def reset(self, period_key: str) -> None:
        from sqlalchemy import text

        session = self._session()
        session.execute(
            text(
                "UPDATE distributed_counter"
                " SET consumed = 0, period_key = :period_key, updated_at = now()"
                " WHERE key = :key"
            ),
            {"period_key": period_key, "key": self.key},
        )
        session.commit()
        self.period_key = period_key

    async def consumed(self) -> int:
        from sqlalchemy import text

        session = self._session()
        row = session.execute(
            text("SELECT consumed FROM distributed_counter WHERE key = :key"),
            {"key": self.key},
        ).fetchone()
        return int(row[0]) if row else 0
