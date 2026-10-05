# SPDX-License-Identifier: AGPL-3.0-or-later
"""A WordPress-shaped federated source, in process, for the tests: posts, a
custom post type, a taxonomy and registered meta, each described by the
JSON Schema the WordPress REST API publishes for it (``OPTIONS
/wp/v2/<rest_base>``'s ``schema``: ``readonly`` fields, ``["string",
"null"]`` types, inline ``title``/``content`` objects, term ids as integer
arrays, ``meta`` an object of the registered keys).

It proves the typed-federation contract holds for a provider unlike ERPNext
without a WordPress provider: records keyed by an integer ``id``, versions
in ``modified_gmt``, a taxonomy with no version (served read and create
only), and the provider's own access rule (a requester it does not serve is
answered 404). Records live in memory, as an upstream's would."""

from __future__ import annotations

import itertools
import threading
from copy import deepcopy
from typing import AbstractSet, Any, Dict, List, Mapping, Optional, Sequence

from fastapi import HTTPException

from zephyrex.extensions.federation.BLL_Federation_Typed import (
    AbstractFederatedSource,
    FederatedCall,
    FederatedQuery,
    FederatedSession,
    FederatedType,
    StaleRecord,
)
from zephyrex.lib.Preconditions import IfMatch

WORDPRESS_NAMESPACE = "wordpress_ab12cd34"
VERSION_FIELD = "modified_gmt"
_RENDERED = {"title", "content", "excerpt"}


def _rendered_object(*extra: str) -> Dict[str, Any]:
    properties: Dict[str, Any] = {
        "raw": {"type": "string", "context": ["edit"]},
        "rendered": {"type": "string", "context": ["view", "edit"], "readonly": True},
    }
    for name in extra:
        properties[name] = {"type": "boolean", "readonly": True}
    return {
        "type": "object",
        "context": ["view", "edit", "embed"],
        "properties": properties,
    }


def post_schema(
    title: str, taxonomies: Sequence[str], meta: Mapping[str, Any]
) -> Dict[str, Any]:
    """A post type's schema as WordPress publishes it."""
    properties: Dict[str, Any] = {
        "date": {"type": ["string", "null"], "format": "date-time"},
        "date_gmt": {"type": ["string", "null"], "format": "date-time"},
        "guid": {
            "type": "object",
            "readonly": True,
            "properties": {
                "raw": {"type": "string", "readonly": True},
                "rendered": {"type": "string", "readonly": True},
            },
        },
        "id": {"type": "integer", "readonly": True},
        "link": {"type": "string", "format": "uri", "readonly": True},
        "modified": {"type": "string", "format": "date-time", "readonly": True},
        VERSION_FIELD: {"type": "string", "format": "date-time", "readonly": True},
        "slug": {"type": "string"},
        "status": {
            "type": "string",
            "enum": ["publish", "future", "draft", "pending", "private"],
        },
        "type": {"type": "string", "readonly": True},
        "password": {"type": "string"},
        "title": _rendered_object(),
        "content": _rendered_object("protected"),
        "excerpt": _rendered_object("protected"),
        "author": {"type": "integer"},
        "featured_media": {"type": "integer"},
        "comment_status": {"type": "string", "enum": ["open", "closed"]},
        "sticky": {"type": "boolean"},
        "meta": {"type": "object", "properties": dict(meta)},
        # What WordPress adds to a response but no schema property can
        # hold under its own name.
        "_links": {"type": "object", "readonly": True},
    }
    for taxonomy in taxonomies:
        properties[taxonomy] = {"type": "array", "items": {"type": "integer"}}
    return {
        "$schema": "http://json-schema.org/draft-04/schema#",
        "title": title,
        "type": "object",
        "properties": properties,
    }


def term_schema(taxonomy: str) -> Dict[str, Any]:
    """A taxonomy's term schema as WordPress publishes it."""
    return {
        "$schema": "http://json-schema.org/draft-04/schema#",
        "title": taxonomy,
        "type": "object",
        "properties": {
            "id": {"type": "integer", "readonly": True},
            "count": {"type": "integer", "readonly": True},
            "description": {"type": "string"},
            "link": {"type": "string", "format": "uri", "readonly": True},
            "name": {"type": "string"},
            "slug": {"type": "string"},
            "taxonomy": {"type": "string", "enum": [taxonomy], "readonly": True},
            "parent": {"type": "integer"},
            "meta": {"type": "object", "properties": {}},
        },
    }


WORDPRESS_TYPES = (
    FederatedType(
        name="post",
        json_schema=post_schema(
            "post", ["categories", "tags"], {"footnotes": {"type": "string"}}
        ),
        key_field="id",
        version_field=VERSION_FIELD,
    ),
    FederatedType(
        name="book",
        json_schema=post_schema("book", ["genre"], {"isbn": {"type": "string"}}),
        key_field="id",
        version_field=VERSION_FIELD,
    ),
    FederatedType(name="genre", json_schema=term_schema("genre"), key_field="id"),
)


class WordPressShapedSite:
    """The site's records, by type."""

    def __init__(self) -> None:
        self.records: Dict[str, Dict[int, Dict[str, Any]]] = {
            t.name: {} for t in WORDPRESS_TYPES
        }
        self.lock = threading.Lock()
        self._ids = itertools.count(1)
        self._clock = itertools.count(1)

    def stamp(self) -> str:
        return f"2026-10-05T12:00:{next(self._clock):02d}"

    def write(
        self, type_name: str, record: Dict[str, Any], data: Mapping[str, Any]
    ) -> None:
        for key, value in data.items():
            if key in _RENDERED and isinstance(value, Mapping):
                merged = {**record.get(key, {}), **value}
                merged["rendered"] = f"<p>{merged.get('raw', '')}</p>"
                record[key] = merged
            elif key == "meta" and isinstance(value, Mapping):
                record["meta"] = {**record.get("meta", {}), **value}
            else:
                record[key] = deepcopy(value)
        if type_name != "genre":
            record["modified"] = record[VERSION_FIELD] = self.stamp()


class WordPressShapedSession(FederatedSession):
    def __init__(self, site: WordPressShapedSite) -> None:
        self.site = site

    def _found(self, type_name: str, key: Any) -> Dict[str, Any]:
        record = self.site.records[type_name].get(int(key))
        if record is None:
            raise HTTPException(status_code=404, detail=f"Invalid {type_name} ID.")
        return record

    def _current(
        self, type_name: str, key: Any, expected: Optional[IfMatch]
    ) -> Dict[str, Any]:
        record = self._found(type_name, key)
        if expected is not None and not expected.names(str(record[VERSION_FIELD])):
            raise StaleRecord(deepcopy(record))
        return record

    async def list(
        self, type_name: str, query: FederatedQuery
    ) -> Sequence[Mapping[str, Any]]:
        with self.site.lock:
            rows = sorted(
                self.site.records[type_name].values(), key=lambda row: row["id"]
            )
            wanted = (query.filters or {}).get("status")
            if wanted is not None:
                rows = [row for row in rows if row.get("status") == wanted]
            return deepcopy(rows[query.start : query.start + query.page_length])

    async def get(self, type_name: str, key: Any) -> Mapping[str, Any]:
        with self.site.lock:
            return deepcopy(self._found(type_name, key))

    async def create(
        self, type_name: str, data: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        with self.site.lock:
            record_id = next(self.site._ids)
            record: Dict[str, Any] = {
                "id": record_id,
                "type": type_name,
                "link": f"https://blog.example.com/?p={record_id}",
                "_links": {"self": [{"href": f"/wp/v2/{type_name}/{record_id}"}]},
            }
            if type_name == "genre":
                record.update({"count": 0, "taxonomy": "genre"})
            self.site.write(type_name, record, data)
            self.site.records[type_name][record_id] = record
            return deepcopy(record)

    async def update(
        self,
        type_name: str,
        key: Any,
        data: Mapping[str, Any],
        expected: Optional[IfMatch],
    ) -> Mapping[str, Any]:
        with self.site.lock:
            record = self._current(type_name, key, expected)
            self.site.write(type_name, record, data)
            return deepcopy(record)

    async def delete(
        self, type_name: str, key: Any, expected: Optional[IfMatch]
    ) -> None:
        with self.site.lock:
            self._current(type_name, key, expected)
            del self.site.records[type_name][int(key)]


class WordPressShapedSource(AbstractFederatedSource):
    """The site, for every requester but those it ``refuses`` (read live,
    as a provider's own access rule is)."""

    def __init__(
        self,
        site: WordPressShapedSite,
        namespace: str = WORDPRESS_NAMESPACE,
        refuses: AbstractSet[str] = frozenset(),
        types: Sequence[FederatedType] = WORDPRESS_TYPES,
    ) -> None:
        self.site = site
        self._namespace = namespace
        self.refuses = refuses
        self.types: List[FederatedType] = list(types)

    @property
    def namespace(self) -> str:
        return self._namespace

    @property
    def title(self) -> str:
        return "WordPress blog.example.com"

    async def catalogue(self) -> Sequence[FederatedType]:
        return self.types

    def session(self, call: FederatedCall) -> FederatedSession:
        if call.requester_id in self.refuses:
            raise HTTPException(status_code=404, detail="No such site")
        return WordPressShapedSession(self.site)


class UnreachableSource(WordPressShapedSource):
    """A source whose upstream cannot be reached when the app boots."""

    async def catalogue(self) -> Sequence[FederatedType]:
        raise HTTPException(status_code=502, detail="blog.example.com is not reachable")
