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

A no-arg factory :func:`default_counter` is provided so callers (e.g. the
``Quota.try_consume`` integration in :mod:`extensions.quota.BLL_Quota`) can ask for a
ready-to-use counter without first knowing key/limit. The factory returns
an :class:`InMemoryDistributedCounter` with sensible defaults plus a
``try_consume(quotas, amount)`` shape that operates on Quota-like rows.

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
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple


class CounterExhaustedError(Exception):
    """Raised when an attempt to consume from a counter would exceed the limit."""


# In-process atomic-iteration helper for the Quota-list call shape. Lives at
# module level so it is reused by ``DistributedCounter.try_consume`` and any
# other consumer that needs to atomically decrement a list of Quota-like rows.
_QUOTA_LOCK = threading.Lock()


def _consume_quota_rows(rows: List[Any], amount: int = 1) -> bool:
    """Atomically decrement every row in ``rows`` by ``amount``.

    Returns True iff every row had headroom; otherwise leaves all rows
    unchanged. Each row must expose ``is_exhausted(additional=int) -> bool``
    and a ``consumed`` integer attribute.
    """
    if amount < 0:
        raise ValueError("amount must be non-negative")
    if not rows:
        return False
    with _QUOTA_LOCK:
        for row in rows:
            if hasattr(row, "is_exhausted"):
                if row.is_exhausted(additional=amount):
                    return False
            else:
                if (getattr(row, "consumed", 0) + amount) > getattr(
                    row, "limit", 0
                ):
                    return False
        for row in rows:
            row.consumed = getattr(row, "consumed", 0) + amount
        return True


class DistributedCounter:
    """Distributed counter primitive (Item 69).

    Concrete by design: ``DistributedCounter()`` must be instantiable with no
    required args so callers like ``Quota.try_consume`` (which does literal
    ``DistributedCounter()``) succeed without first knowing key/limit. The
    instance returned by the no-arg path is the default in-memory backend
    via ``__new__`` redirection.

    For typed downstream code, prefer :class:`InMemoryDistributedCounter` or
    :class:`PostgresDistributedCounter` directly.

    The method surface is intentionally polymorphic: ``try_consume`` accepts
    either the per-counter shape ``try_consume(amount=1)`` or the Quota-style
    list shape ``try_consume(quotas, amount=...)``. The list shape iterates
    the rows under the in-process lock, so a no-arg ``DistributedCounter()``
    returned from Quota.py works without falling back through ``except``.
    """

    def __new__(cls, *args, **kwargs):
        # When the abstract base is instantiated with no args, return the
        # default in-memory backend so downstream callers (Quota.try_consume,
        # default_counter()) get a usable, non-abstract instance.
        if cls is DistributedCounter and not args and not kwargs:
            return _no_arg_default()
        return object.__new__(cls)

    def __init__(
        self,
        key: Optional[str] = None,
        limit: Optional[int] = None,
        period_key: Optional[str] = None,
    ) -> None:
        # Allow no-arg construction for default-counter / Quota usage.
        self.key = key if key is not None else "_default"
        self.limit = limit if limit is not None else 1 << 62  # effectively unbounded
        self.period_key = period_key

    def try_consume(self, *args: Any, amount: int = 1) -> Any:
        """Atomically attempt to consume ``amount`` units.

        Polymorphic call shape (note: the return type depends on shape):

        * ``await try_consume(amount=1)`` -- per-counter consumption. Returns a
          coroutine that resolves to ``bool``.
        * ``try_consume(quotas, amount=1)`` -- Quota.py-style; iterates rows
          atomically under an in-process lock. Returns ``bool`` directly
          (synchronous), so callers like ``Quota.try_consume`` that use the
          legacy sync API still work without an event loop.

        Returns True on success and False if the limit would be exceeded.
        """
        # Quota-list shape detection: first positional is a list of objects
        # exposing the (consumed, limit, is_exhausted) attribute set. This
        # shape is sync-by-design so callers from a synchronous context
        # (e.g. Quota.try_consume) can use it without an await.
        if args and isinstance(args[0], list):
            return _consume_quota_rows(args[0], amount=amount)
        # Per-counter shape: return a coroutine for the standard async API.
        if args:
            try:
                amt = int(args[0])
            except (TypeError, ValueError):
                amt = amount
        else:
            amt = amount
        return self._try_consume_one(amt)

    async def _try_consume_one(self, amount: int) -> bool:
        """Default per-counter consumption.

        Subclasses override to apply their backend-specific atomic increment.
        The base class' permissive default exists so that the no-arg
        ``DistributedCounter()`` instance is harmless when used in test
        harnesses that do not exercise the limit.
        """
        return True

    async def release(self, amount: int) -> None:
        """Credit ``amount`` units back to the counter."""
        return None

    async def reset(self, period_key: str) -> None:
        """Reset the counter for a new period."""
        self.period_key = period_key

    async def consumed(self) -> int:
        """Return the currently consumed amount."""
        return 0


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
        key: Optional[str] = None,
        limit: Optional[int] = None,
        period_key: Optional[str] = None,
    ) -> None:
        # No-arg construction is allowed (returns a sane default counter); the
        # ``DistributedCounter()`` no-arg path lands here via _no_arg_default.
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

    async def _try_consume_one(self, amount: int) -> bool:
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

    async def _try_consume_one(self, amount: int) -> bool:
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


# ---------------------------------------------------------------------------
# No-arg default factory
# ---------------------------------------------------------------------------


def _no_arg_default() -> "DistributedCounter":
    """Construct the canonical no-arg default counter instance."""
    return InMemoryDistributedCounter("_default", limit=1 << 62)


def default_counter() -> "DistributedCounter":
    """Public factory: return a ready-to-use default counter instance.

    Equivalent to ``DistributedCounter()``. Provided as the canonical entry
    point for callers that prefer a named factory over the no-arg
    constructor.
    """
    return _no_arg_default()
