# SPDX-License-Identifier: AGPL-3.0-or-later
"""Valkey-backed entity cache — read-through with write-through invalidation.

Sits between the BLL layer and the DB layer. Caches entity DTOs by primary
key and optional secondary index fields. Mutations (create/update/delete)
invalidate the affected keys so stale reads never survive a write.

Cache topology:
    - ``entity:{table}:{id}``                → JSON-serialized DTO dict
    - ``idx:{table}:{field}:{value}``         → entity id (pointer for field lookups)

LIST results are NOT cached — the permutation space of filters, sort, and
pagination is too large to key deterministically. Only by-id and by-indexed-
field lookups hit the cache.

All Valkey operations are best-effort: a cache failure degrades to a DB
read, never to an error response. Invalidation failures log a warning
but do not block the mutation.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

_logger = logging.getLogger(__name__)

_DEFAULT_TTL = 300


class ValkeyEntityCache:
    """Per-entity-type read-through cache with write-through invalidation."""

    def __init__(self, client: Any, default_ttl: int = _DEFAULT_TTL) -> None:
        self._client = client
        self._default_ttl = default_ttl
        self._ttl_overrides: dict[str, int] = {}
        self._index_fields: dict[str, set[str]] = {}

    def configure_entity(
        self,
        table_name: str,
        ttl: int | None = None,
        index_fields: set[str] | None = None,
    ) -> None:
        """Configure caching behaviour for an entity type."""
        if ttl is not None:
            self._ttl_overrides[table_name] = ttl
        env_ttl = os.environ.get(f"VALKEY_ENTITY_TTL_{table_name.upper()}")
        if env_ttl is not None:
            try:
                self._ttl_overrides[table_name] = int(env_ttl)
            except (TypeError, ValueError):
                pass
        if index_fields:
            self._index_fields.setdefault(table_name, set()).update(index_fields)

    def _ttl_for(self, table: str) -> int:
        return self._ttl_overrides.get(table, self._default_ttl)

    @staticmethod
    def _entity_key(table: str, entity_id: str) -> str:
        return f"entity:{table}:{entity_id}"

    @staticmethod
    def _index_key(table: str, field: str, value: str) -> str:
        return f"idx:{table}:{field}:{value}"

    async def get_by_id(self, table: str, entity_id: str) -> dict | None:
        """Return the cached DTO dict for ``table:entity_id``, or None on miss."""
        try:
            raw = await self._client.get(self._entity_key(table, entity_id))
            if raw is None:
                return None
            return json.loads(raw)
        except Exception as exc:
            _logger.debug("ValkeyEntityCache.get_by_id miss/error: %s", exc)
            return None

    async def get_by_field(self, table: str, field: str, value: str) -> str | None:
        """Return the entity id pointed to by the index key, or None."""
        try:
            raw = await self._client.get(self._index_key(table, field, value))
            if raw is None:
                return None
            return raw.decode() if isinstance(raw, bytes) else str(raw)
        except Exception as exc:
            _logger.debug("ValkeyEntityCache.get_by_field miss/error: %s", exc)
            return None

    async def get_by_field_full(
        self, table: str, field: str, value: str
    ) -> dict | None:
        """Index lookup → entity id → full entity dict (two round-trips, pipelined)."""
        entity_id = await self.get_by_field(table, field, value)
        if entity_id is None:
            return None
        return await self.get_by_id(table, entity_id)

    async def put(
        self,
        table: str,
        entity_id: str,
        dto_dict: dict,
        index_fields: dict[str, str] | None = None,
    ) -> None:
        """Cache an entity DTO and its index pointers. Uses a pipeline."""
        ttl = self._ttl_for(table)
        try:
            pipe = self._client.pipeline(transaction=False)
            pipe.set(
                self._entity_key(table, entity_id),
                json.dumps(dto_dict, default=str),
                ex=ttl,
            )
            if index_fields:
                for field, value in index_fields.items():
                    if value is not None:
                        pipe.set(
                            self._index_key(table, field, str(value)),
                            entity_id,
                            ex=ttl,
                        )
            await pipe.execute()
        except Exception as exc:
            _logger.debug("ValkeyEntityCache.put error: %s", exc)

    async def invalidate(
        self,
        table: str,
        entity_id: str,
        old_index_values: dict[str, str] | None = None,
    ) -> None:
        """Delete the entity key and any old index keys."""
        try:
            keys_to_del = [self._entity_key(table, entity_id)]
            if old_index_values:
                for field, value in old_index_values.items():
                    if value is not None:
                        keys_to_del.append(self._index_key(table, field, str(value)))
            if keys_to_del:
                await self._client.delete(*keys_to_del)
        except Exception as exc:
            _logger.warning("ValkeyEntityCache.invalidate error: %s", exc)

    async def invalidate_table(self, table: str) -> None:
        """Nuclear invalidation: delete all keys for a table. For migrations."""
        try:
            patterns = [f"entity:{table}:*", f"idx:{table}:*"]
            for pattern in patterns:
                async for key in self._client.scan_iter(match=pattern, count=500):
                    await self._client.delete(key)
        except Exception as exc:
            _logger.warning("ValkeyEntityCache.invalidate_table error: %s", exc)
