# SPDX-License-Identifier: AGPL-3.0-or-later
"""Purging what was deleted.

A delete in the framework is soft: the row is hidden (``deleted_at`` is set)
but stays in the database. With ``PRIVACY_PURGE_DELETED_AFTER_DAYS`` set, a
daily service removes for good every row, in every table of the app, deleted
longer ago than that: a deleted account, and everything erased with it, is
then gone rather than hidden.

A deleted row that anything still points at stays, so a foreign key's
``ON DELETE CASCADE`` can never take a live row with it (a live script filed
in a deleted folder, a payment record that names a deleted account). The
pointing rows go first: each pass purges what nothing names any more, and
passes repeat until one purges nothing.

Purging removes rows only. An extension that keeps files beside its rows
deletes them when the row is deleted, as recordings and media do.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, cast

from sqlalchemy import MetaData, Table, and_, delete, exists, not_, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger
from zephyrex.logic.AbstractService import AbstractService

PURGE_SETTING = "PRIVACY_PURGE_DELETED_AFTER_DAYS"
SERVICE_ID = "privacy_purge_deleted"
TICK_SECONDS = 24 * 60 * 60
# Each pass purges at least one more layer of rows that pointed at each other;
# no schema nests deeper than this.
MAX_PASSES = 50


def retention_from_setting() -> Optional[timedelta]:
    """How long a deleted row is kept, or None when the setting is unset
    (deletions then stay soft for ever)."""
    value = env(PURGE_SETTING).strip()
    if value == "":
        return None
    if not value.isdigit() or int(value) < 1:
        raise ValueError(
            f"{PURGE_SETTING} must be a whole number of days, at least 1 (got {value!r})"
        )
    return timedelta(days=int(value))


def _unreferenced(table: Table, metadata: MetaData) -> List[Any]:
    """A condition for each foreign key that points at ``table``: no row
    names this one (a row naming itself, as an account its own creator, does
    not count)."""
    conditions: List[Any] = []
    for referrer in metadata.sorted_tables:
        for key in referrer.foreign_keys:
            if key.column.table is not table:
                continue
            pointing = referrer.alias() if referrer is table else referrer
            match = pointing.c[key.parent.name] == table.c[key.column.name]
            if referrer is table:
                match = and_(
                    match,
                    *(
                        pointing.c[column.name] != column
                        for column in table.primary_key.columns
                    ),
                )
            conditions.append(not_(exists(select(1).where(match))))
    return conditions


def _deleted(session: Session, table: Table, where: Any) -> int:
    """Delete ``table``'s rows matching ``where`` in a savepoint, and say how
    many went (IntegrityError, with none gone, when a foreign key keeps one)."""
    with session.begin_nested():
        result = cast("CursorResult[Any]", session.execute(delete(table).where(where)))
        return int(result.rowcount or 0)


def _purge_table(
    table: Table, metadata: MetaData, session: Session, cutoff: datetime
) -> int:
    expired = and_(
        table.c.deleted_at.is_not(None),
        table.c.deleted_at < cutoff,
        *_unreferenced(table, metadata),
    )
    try:
        return _deleted(session, table, expired)
    except IntegrityError:
        pass
    # A foreign key the database holds but the models do not declare (a
    # migration's own), or a row that came to point at one since the check,
    # keeps a row: purge the others one by one around it.
    keys = list(table.primary_key.columns)
    purged = 0
    for row in session.execute(select(*keys).where(expired)).all():
        try:
            purged += _deleted(
                session, table, and_(*(key == value for key, value in zip(keys, row)))
            )
        except IntegrityError as exc:
            logger.info(
                "Purge of a %s row kept by a reference: %s", table.name, exc.orig
            )
    return purged


def purge_deleted(
    metadata: MetaData, session: Session, cutoff: datetime
) -> Dict[str, int]:
    """Remove every row deleted before ``cutoff`` (naive UTC, as ``deleted_at``
    is stored) that nothing points at, and return how many went from each table."""
    tables = [table for table in metadata.sorted_tables if "deleted_at" in table.c]
    purged: Dict[str, int] = {}
    for _ in range(MAX_PASSES):
        progress = 0
        # Referrers before what they refer to, where the schema orders them.
        for table in reversed(tables):
            count = _purge_table(table, metadata, session, cutoff)
            if count:
                purged[table.name] = purged.get(table.name, 0) + count
                progress += count
        session.commit()
        if progress == 0:
            break
    return purged


class PurgeService(AbstractService):
    """Purges, once a day as ROOT, what was deleted more than ``retention`` ago."""

    def __init__(
        self,
        requester_id: str,
        model_registry: Any,
        retention: timedelta,
        interval_seconds: int = TICK_SECONDS,
        **kwargs: Any,
    ):
        super().__init__(
            requester_id=requester_id,
            interval_seconds=interval_seconds,
            service_id=SERVICE_ID,
            **kwargs,
        )
        self.model_registry = model_registry
        self.retention = retention

    def purge(self, now: Optional[datetime] = None) -> Dict[str, int]:
        moment = now or datetime.now(timezone.utc)
        cutoff = moment.astimezone(timezone.utc).replace(tzinfo=None) - self.retention
        session = self.model_registry.DB.session()
        try:
            return purge_deleted(
                self.model_registry.DB.manager.Base.metadata, session, cutoff
            )
        finally:
            session.close()

    async def update(self) -> None:
        import asyncio

        try:
            purged = await asyncio.to_thread(self.purge)
        except Exception as exc:  # the database is away: try again tomorrow
            logger.warning("privacy: purge failed (%s: %s)", type(exc).__name__, exc)
            return
        if purged:
            logger.info("privacy: purged deleted rows %s", purged)


def purge_services(model_registry: Any, requester_id: str) -> List[AbstractService]:
    """The daily purge, when the app turned it on."""
    retention = retention_from_setting()
    if retention is None:
        return []
    return [
        PurgeService(
            requester_id=requester_id,
            model_registry=model_registry,
            retention=retention,
        )
    ]
