"""Advisory locking primitive (Item 53).

Canonical advisory-lock abstraction that callers reach for instead of rolling
their own ``SELECT ... FOR UPDATE`` / ``threading.Lock`` / ad-hoc patterns.

Two backends ship:

- ``InMemoryAdvisoryLock``: per-process ``asyncio.Lock`` keyed by name. Default,
  appropriate for tests and single-process deployments.
- ``PostgresAdvisoryLock``: uses ``pg_advisory_xact_lock(hashtext(name))`` for
  cross-process serialization with auto-release on transaction commit/rollback.
  DB binding is deferred via a session_provider injection point.

Backend selection is controlled by the ``LOCK_BACKEND`` environment variable
(``"postgres"`` or ``"memory"``; default is ``"memory"``).

Usage::

    async with acquire_lock("payment.subscription_renew:user-42", timeout=5):
        ...
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator, Callable, Dict, Optional


class LockTimeoutError(Exception):
    """Raised when an advisory lock cannot be acquired within the configured timeout."""


# ---------------------------------------------------------------------------
# In-memory backend
# ---------------------------------------------------------------------------


class InMemoryAdvisoryLock:
    """A process-local advisory lock, keyed by name."""

    _locks: Dict[str, asyncio.Lock] = {}

    def __init__(self, name: str, timeout: Optional[float] = None) -> None:
        self.name = name
        self.timeout = timeout
        self._lock: Optional[asyncio.Lock] = None
        self._acquired = False

    @classmethod
    def _lock_for(cls, name: str) -> asyncio.Lock:
        lock = cls._locks.get(name)
        if lock is None:
            lock = asyncio.Lock()
            cls._locks[name] = lock
        return lock

    async def __aenter__(self) -> "InMemoryAdvisoryLock":
        self._lock = self._lock_for(self.name)
        if self.timeout is None:
            await self._lock.acquire()
        else:
            try:
                await asyncio.wait_for(self._lock.acquire(), timeout=self.timeout)
            except asyncio.TimeoutError as e:
                raise LockTimeoutError(
                    f"Timed out acquiring in-memory advisory lock {self.name!r}"
                    f" after {self.timeout}s"
                ) from e
        self._acquired = True
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._acquired and self._lock is not None and self._lock.locked():
            self._lock.release()
            self._acquired = False


# ---------------------------------------------------------------------------
# Postgres backend
# ---------------------------------------------------------------------------


SessionProvider = Callable[[], object]


class PostgresAdvisoryLock:
    """Postgres advisory lock using ``pg_advisory_xact_lock(hashtext(name))``.

    Transaction-scoped by default for safety: the lock auto-releases on commit
    or rollback, matching the framework's preferred lifecycle.
    """

    def __init__(
        self,
        name: str,
        timeout: Optional[float] = None,
        session_provider: Optional[SessionProvider] = None,
    ) -> None:
        self.name = name
        self.timeout = timeout
        self._session_provider = session_provider
        self._session = None
        self._acquired = False

    async def __aenter__(self) -> "PostgresAdvisoryLock":
        from sqlalchemy import text

        if self._session_provider is None:
            raise RuntimeError(
                "PostgresAdvisoryLock requires a session_provider injection."
            )
        self._session = self._session_provider()

        if self.timeout is not None:
            ms = int(self.timeout * 1000)
            self._session.execute(text(f"SET LOCAL lock_timeout = '{ms}ms'"))

        try:
            self._session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:name))"),
                {"name": self.name},
            )
            self._acquired = True
        except Exception as e:
            # Postgres raises a generic operational error on lock_timeout.
            if "lock timeout" in str(e).lower() or "canceling" in str(e).lower():
                raise LockTimeoutError(
                    f"Timed out acquiring Postgres advisory lock {self.name!r}"
                ) from e
            raise
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        # Transaction-scoped locks release automatically on commit/rollback.
        # We do not force commit here; the caller controls the transaction.
        self._acquired = False


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


# Optional session provider for Postgres backend. Callers wire this up at
# bootstrap time if they want the postgres backend to be functional without
# passing the provider per-call.
_global_postgres_session_provider: Optional[SessionProvider] = None


def set_postgres_session_provider(provider: Optional[SessionProvider]) -> None:
    """Configure a global session provider for the Postgres backend."""
    global _global_postgres_session_provider
    _global_postgres_session_provider = provider


def _select_backend() -> str:
    return os.environ.get("LOCK_BACKEND", "memory").strip().lower()


@asynccontextmanager
async def acquire_lock(
    name: str,
    timeout: Optional[float] = None,
    session_provider: Optional[SessionProvider] = None,
) -> AsyncIterator[None]:
    """Acquire an advisory lock by name.

    Backend is selected via the ``LOCK_BACKEND`` environment variable. Raises
    :class:`LockTimeoutError` if ``timeout`` is provided and exhausted.
    """
    backend = _select_backend()
    if backend == "postgres":
        provider = session_provider or _global_postgres_session_provider
        lock = PostgresAdvisoryLock(name, timeout=timeout, session_provider=provider)
    else:
        lock = InMemoryAdvisoryLock(name, timeout=timeout)
    async with lock:
        yield
