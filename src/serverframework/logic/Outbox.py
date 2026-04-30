"""Transactional outbox + dead-letter queue primitives (Item 35).

This module owns the canonical idempotency-key store (per Item 4's refinement):
when a BLL mutation enrolls into the outbox, the idempotency key is written into
the outbox row in the same transaction as the local mutation, guaranteeing that
retries -- including retries that cross process restarts -- reuse the original
key.

SQL schema (canonical) for the durable outbox + DLQ tables::

    CREATE TABLE outbox (
        id                  TEXT PRIMARY KEY,
        operation_type      TEXT NOT NULL,
        target_provider     TEXT NOT NULL,
        target_ability      TEXT NOT NULL,
        payload             JSONB NOT NULL,
        idempotency_key     TEXT NOT NULL UNIQUE,
        deadline            TIMESTAMPTZ,
        retry_count         INT NOT NULL DEFAULT 0,
        status              TEXT NOT NULL DEFAULT 'pending',
        created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
        last_attempted_at   TIMESTAMPTZ,
        last_error          TEXT
    );
    CREATE INDEX ix_outbox_status_created ON outbox (status, created_at);
    CREATE INDEX ix_outbox_idempotency_key ON outbox (idempotency_key);

    CREATE TABLE dlq (
        id                  TEXT PRIMARY KEY,
        operation_type      TEXT NOT NULL,
        target_provider     TEXT NOT NULL,
        target_ability      TEXT NOT NULL,
        payload             JSONB NOT NULL,
        idempotency_key     TEXT NOT NULL,
        final_error         TEXT NOT NULL,
        operator_action     TEXT NOT NULL DEFAULT 'pending',
        moved_at            TIMESTAMPTZ NOT NULL DEFAULT now()
    );

The ``OutboxDrainService`` (built on Item 28's ``QueueConsumerService``) is
imported lazily in callers; this module deliberately does not import the
service layer at module load time to avoid cycles.
"""

from __future__ import annotations

import threading
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class OutboxEntry(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    operation_type: str
    target_provider: str
    target_ability: str
    payload: dict
    idempotency_key: str
    deadline: Optional[datetime] = None
    retry_count: int = 0
    status: Literal["pending", "in_flight", "complete", "dlq"] = "pending"
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    last_attempted_at: Optional[datetime] = None
    last_error: Optional[str] = None


class DLQEntry(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: str
    operation_type: str
    target_provider: str
    target_ability: str
    payload: dict
    idempotency_key: str
    final_error: str
    operator_action: Literal["pending", "replay", "discard"] = "pending"
    moved_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


# ---------------------------------------------------------------------------
# Store abstraction
# ---------------------------------------------------------------------------


class OutboxStore(ABC):
    """Abstract storage for outbox + dead-letter-queue entries."""

    @abstractmethod
    def enqueue(self, entry: OutboxEntry) -> str:
        """Persist a new pending entry. Returns the entry id."""

    @abstractmethod
    def claim_pending(self, limit: int = 10) -> List[OutboxEntry]:
        """Atomically transition up to ``limit`` pending entries to ``in_flight``."""

    @abstractmethod
    def mark_complete(self, id: str) -> None:
        """Mark the entry as successfully delivered."""

    @abstractmethod
    def mark_failed(
        self, id: str, error: BaseException, max_retries: int = 5
    ) -> None:
        """Record a failure. Moves to DLQ once ``retry_count > max_retries``."""

    @abstractmethod
    def find_by_idempotency_key(self, key: str) -> Optional[OutboxEntry]:
        """Return the existing entry for ``key`` or None."""

    @abstractmethod
    def list_dlq(self, limit: int = 100) -> List[DLQEntry]:
        """List dead-letter entries newest-first."""


# ---------------------------------------------------------------------------
# In-memory implementation
# ---------------------------------------------------------------------------


class InMemoryOutboxStore(OutboxStore):
    """A thread-safe in-memory outbox store, suitable for tests and dev.

    Not durable; everything is lost on process exit.
    """

    def __init__(self) -> None:
        self._entries: Dict[str, OutboxEntry] = {}
        self._dlq: Dict[str, DLQEntry] = {}
        self._by_key: Dict[str, str] = {}  # idempotency_key -> entry id
        self._lock = threading.RLock()

    def enqueue(self, entry: OutboxEntry) -> str:
        with self._lock:
            existing_id = self._by_key.get(entry.idempotency_key)
            if existing_id is not None:
                # Idempotent: return the prior id without overwriting.
                return existing_id
            self._entries[entry.id] = entry
            self._by_key[entry.idempotency_key] = entry.id
            return entry.id

    def claim_pending(self, limit: int = 10) -> List[OutboxEntry]:
        with self._lock:
            claimed: List[OutboxEntry] = []
            # Sort to give deterministic FIFO behavior.
            ordered = sorted(
                (e for e in self._entries.values() if e.status == "pending"),
                key=lambda e: e.created_at,
            )
            for entry in ordered[:limit]:
                entry.status = "in_flight"
                entry.last_attempted_at = datetime.now(timezone.utc)
                claimed.append(entry)
            return claimed

    def mark_complete(self, id: str) -> None:
        with self._lock:
            entry = self._entries.get(id)
            if entry is None:
                return
            entry.status = "complete"

    def mark_failed(
        self, id: str, error: BaseException, max_retries: int = 5
    ) -> None:
        with self._lock:
            entry = self._entries.get(id)
            if entry is None:
                return
            entry.retry_count += 1
            entry.last_error = f"{type(error).__name__}: {error}"
            entry.last_attempted_at = datetime.now(timezone.utc)
            if entry.retry_count > max_retries:
                entry.status = "dlq"
                self._dlq[entry.id] = DLQEntry(
                    id=entry.id,
                    operation_type=entry.operation_type,
                    target_provider=entry.target_provider,
                    target_ability=entry.target_ability,
                    payload=entry.payload,
                    idempotency_key=entry.idempotency_key,
                    final_error=entry.last_error or "unknown",
                )
            else:
                # Return to pending for the drain service to pick up again.
                entry.status = "pending"

    def find_by_idempotency_key(self, key: str) -> Optional[OutboxEntry]:
        with self._lock:
            entry_id = self._by_key.get(key)
            if entry_id is None:
                return None
            return self._entries.get(entry_id)

    def list_dlq(self, limit: int = 100) -> List[DLQEntry]:
        with self._lock:
            ordered = sorted(
                self._dlq.values(), key=lambda e: e.moved_at, reverse=True
            )
            return ordered[:limit]


# ---------------------------------------------------------------------------
# OutboxDrainService -- built on Item 28's QueueConsumerService
# ---------------------------------------------------------------------------


class OutboxDrainService:
    """Background drain for the outbox.

    Built on top of :class:`logic.AbstractService.QueueConsumerService`. The
    class is constructed lazily by :func:`make_outbox_drain_service` so that
    importing :mod:`logic.Outbox` does not pull the service layer at module
    load time -- this keeps the import graph acyclic for callers that only
    need the model + store types (e.g. BLL writers enrolling rows).
    """

    def __init__(
        self,
        store: OutboxStore,
        executor: "Optional[OutboxOperationExecutor]" = None,
        max_retries: int = 5,
        **service_kwargs,
    ) -> None:
        # Lazy import to avoid module-load cycle.
        from serverframework.logic.AbstractService import QueueConsumerService, QueueSource

        class _OutboxQueueSource(QueueSource):
            """Adapter that exposes ``OutboxStore`` as a ``QueueSource``."""

            def __init__(self, store: OutboxStore) -> None:
                self._store = store
                self._claimed: List[OutboxEntry] = []

            async def pull_one(self) -> Optional[dict]:
                if not self._claimed:
                    self._claimed = list(self._store.claim_pending(limit=10))
                if not self._claimed:
                    return None
                entry = self._claimed.pop(0)
                return entry.model_dump() | {"_outbox_id": entry.id}

            async def ack(self, item: dict) -> None:
                eid = item.get("_outbox_id") or item.get("id")
                if eid:
                    self._store.mark_complete(str(eid))

            async def nack(self, item: dict) -> None:
                # The OutboxStore implementation moves the entry back to
                # pending on mark_failed; nack here is a no-op so the
                # store remains the single source of truth.
                return None

            async def move_to_dlq(self, item: dict, reason: str) -> None:
                eid = item.get("_outbox_id") or item.get("id")
                if eid:
                    self._store.mark_failed(
                        str(eid), RuntimeError(reason), max_retries=0
                    )

        class _DrainConsumer(QueueConsumerService):
            def __init__(self, *args, executor=None, store=None, **kwargs):
                super().__init__(*args, **kwargs)
                self._executor = executor
                self._store = store

            async def process(self, item: dict) -> None:
                eid = item.get("_outbox_id") or item.get("id")
                if self._executor is None:
                    # No executor wired -- treat the entry as delivered.
                    return None
                try:
                    await self._executor(item)
                except Exception as e:
                    if eid is not None:
                        self._store.mark_failed(
                            str(eid), e, max_retries=self.max_retries
                        )
                    raise

        self.store = store
        self.executor = executor
        self._queue_source = _OutboxQueueSource(store)
        # Build the underlying consumer with sensible service defaults.
        kwargs = dict(service_kwargs)
        kwargs.setdefault("requester_id", "system:outbox-drain")
        kwargs.setdefault("interval_seconds", 1)
        self._consumer = _DrainConsumer(
            queue_source=self._queue_source,
            max_retries=max_retries,
            executor=executor,
            store=store,
            **kwargs,
        )

    # -- proxy lifecycle to the underlying consumer ---------------------------

    def start(self) -> None:
        self._consumer.start()

    def stop(self) -> None:
        self._consumer.stop()

    def pause(self) -> None:
        self._consumer.pause()

    def resume(self) -> None:
        self._consumer.resume()

    def health(self):  # type: ignore[no-untyped-def]
        return self._consumer.health()

    async def update(self) -> None:
        """One drain step (claim + dispatch)."""
        await self._consumer.update()

    async def run_service_loop(self) -> None:
        await self._consumer.run_service_loop()


# Type alias used in OutboxDrainService.__init__. Defined late because the
# class signature only needs ``Callable`` semantics.
OutboxOperationExecutor = "Optional[ ... ]"  # documented below.

import typing as _t  # noqa: E402

OutboxOperationExecutor = _t.Callable[[dict], _t.Awaitable[None]]


def make_outbox_drain_service(
    store: OutboxStore,
    executor: Optional[OutboxOperationExecutor] = None,
    **kwargs,
) -> OutboxDrainService:
    """Factory that produces a fully-wired :class:`OutboxDrainService`.

    Provided as the canonical construction path so callers do not have to
    remember the keyword names accepted by the underlying consumer.
    """
    return OutboxDrainService(store=store, executor=executor, **kwargs)
