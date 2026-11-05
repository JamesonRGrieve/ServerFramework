"""Replay-protection set primitive.

Use case: "has this nonce-shaped value been seen in the last N seconds?"
Drives TOTP replay protection (``auth_mfa``), webhook idempotency
(``webhooks``), and any future single-use-token verification path that
needs to be safe across processes.

This module exposes:

* :class:`ReplayCache` — abstract contract: ``mark_used`` and
  ``is_used``, both keyed on a string and bounded by a TTL (in seconds).
* :class:`InMemoryReplayCache` — process-local default. Backed by
  ``cachetools.TTLCache`` when available, else a plain dict. Suitable
  for single-worker/single-process deployments only.
* :func:`get_replay_cache` / :func:`set_replay_cache` — process-global
  binding so a deployment can install a Redis/Valkey-backed cache once
  at startup and have every consumer pick it up.

The default in-memory backend warns at module load if the deployment
configures ``UVICORN_WORKERS > 1`` and no shared cache is registered:
that combination silently degrades replay protection. Production
deployments must register a shared cache (e.g. via the
``database_memory`` Valkey provider) before enabling MFA or webhook
replay rejection.
"""

from __future__ import annotations

import heapq
import threading
import time
from abc import ABC, abstractmethod
from typing import Optional

from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger


class ReplayCache(ABC):
    """Contract: mark a key, then check whether it has been marked."""

    @abstractmethod
    def mark_used(self, key: str, ttl_seconds: int) -> None: ...

    @abstractmethod
    def is_used(self, key: str) -> bool: ...

    def mark_if_unused(self, key: str, ttl_seconds: int) -> bool:
        """Atomic compare-and-set: mark ``key`` only if it is not already
        marked. Returns True iff this caller is the one that set it.

        H-7 — read-then-write (``if not is_used: mark_used``) is racy
        across concurrent callbacks for the same OAuth state nonce or
        single-use token. Subclasses MUST override this with a real
        atomic primitive on the underlying store (Valkey ``SET NX EX``,
        SQL ``INSERT … ON CONFLICT DO NOTHING``). The default
        implementation here is intentionally NOT atomic and is safe only
        for single-process deployments.
        """
        if self.is_used(key):
            return False
        self.mark_used(key, ttl_seconds)
        return True


class InMemoryReplayCache(ReplayCache):
    """Process-local replay cache.

    Honours the per-call ``ttl_seconds`` argument: each entry stores a
    deadline and ``is_used`` returns False once the deadline has passed.

    ``max_entries`` is a real bound (#282). Deadlines sit in a min-heap, so
    each write drops what has expired in amortised O(log n), never a scan
    of the whole cache under the lock. When the cache is full of live
    entries, the one closest to expiry is evicted, with a warning: memory
    stays bounded, and a deployment whose live keys outgrow the bound
    installs a shared (Valkey) cache instead.
    """

    def __init__(self, max_entries: int = 100_000):
        self._lock = threading.Lock()
        self._max_entries = max_entries
        self._impl: dict = {}
        # (deadline, key); an entry is stale once its key was re-marked
        # with another deadline or removed.
        self._deadlines: list[tuple[float, str]] = []
        self._warned_full = False

    def _store(self, key: str, deadline: float, now: float) -> None:
        """Record ``key`` until ``deadline``, then restore the bound.
        Caller holds the lock."""
        self._impl[key] = deadline
        heapq.heappush(self._deadlines, (deadline, key))
        while self._deadlines and self._deadlines[0][0] < now:
            old_deadline, old_key = heapq.heappop(self._deadlines)
            if self._impl.get(old_key) == old_deadline:
                del self._impl[old_key]
        while len(self._impl) > self._max_entries:
            old_deadline, old_key = heapq.heappop(self._deadlines)
            if self._impl.get(old_key) == old_deadline:
                del self._impl[old_key]
                if not self._warned_full:
                    self._warned_full = True
                    logger.warning(
                        "InMemoryReplayCache is full (%d live entries): evicting "
                        "the entries closest to expiry. Install a shared replay "
                        "cache (Valkey) for this load.",
                        self._max_entries,
                    )
        # Re-marked keys leave stale heap entries; rebuild before they
        # outnumber the live ones.
        if len(self._deadlines) > 2 * max(len(self._impl), 1024):
            self._deadlines = [(d, k) for k, d in self._impl.items()]
            heapq.heapify(self._deadlines)

    def mark_used(self, key: str, ttl_seconds: int) -> None:
        now = time.time()
        with self._lock:
            self._store(key, now + ttl_seconds, now)

    def is_used(self, key: str) -> bool:
        with self._lock:
            entry = self._impl.get(key)
            if entry is None:
                return False
            if entry < time.time():
                self._impl.pop(key, None)
                return False
            return True

    def mark_if_unused(self, key: str, ttl_seconds: int) -> bool:
        """Atomic CAS within a single process via the cache lock."""
        now = time.time()
        with self._lock:
            existing = self._impl.get(key)
            if existing is not None and existing >= now:
                return False
            self._store(key, now + ttl_seconds, now)
            return True


_GLOBAL_REPLAY_CACHE: Optional[ReplayCache] = None


def set_replay_cache(cache: ReplayCache) -> None:
    """Install a shared replay cache. Call once at process boot before
    any auth code path runs."""
    global _GLOBAL_REPLAY_CACHE
    _GLOBAL_REPLAY_CACHE = cache


def get_replay_cache() -> ReplayCache:
    """Return the active replay cache, creating an in-memory default on
    first use. The default is correct only for single-process deployments;
    multi-worker setups must call :func:`set_replay_cache` with a shared
    backend (Redis/Valkey/Postgres-backed) before serving traffic."""
    global _GLOBAL_REPLAY_CACHE
    if _GLOBAL_REPLAY_CACHE is None:
        _GLOBAL_REPLAY_CACHE = InMemoryReplayCache()
        workers_raw = env("UVICORN_WORKERS") or ""
        try:
            workers = int(workers_raw)
        except (TypeError, ValueError):
            workers = 1
        if workers > 1:
            logger.warning(
                "InMemoryReplayCache is being used with UVICORN_WORKERS=%s. "
                "Replay protection is per-worker; install a shared cache "
                "(e.g. via database_memory/Valkey) at boot.",
                workers,
            )
    return _GLOBAL_REPLAY_CACHE
