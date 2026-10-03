# SPDX-License-Identifier: AGPL-3.0-or-later
"""Optimistic concurrency: an entity's version, its ETag, and If-Match.

A record's version is its ``updated_at``, or its ``created_at`` when it was
never updated, in exactly the form the response body carries it (ISO 8601,
to the microsecond the application stamps). Its ETag is that version in
quotes, so a client can take it from the ``ETag`` header or copy it from any
row it holds.

A write names the version it was based on in ``If-Match``. The HTTP layer
(the generic REST routes, ``@custom_route`` routes and GraphQL mutations)
binds what the request expects with :func:`expect_versions`; the database
layer's ``update``/``delete`` claim the expectation for the row they write,
under a row lock, and refuse a stale one before writing. The binding answers
that refusal with 412 and the record as the requester may see it. A batch is
pre-checked whole by the manager, so one stale record refuses all of it.
The binding is request-scoped context rather than a parameter because writes
reach the database through manager overrides and custom-route code whose
signatures the framework does not own; a write to a row no request named is
never affected.

``IF_MATCH_REQUIRED`` (default false) turns a missing If-Match on a bound
save (PUT/PATCH/DELETE) from accepted into 428.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Mapping, Optional, Set, Tuple

from fastapi import HTTPException, status
from fastapi.encoders import jsonable_encoder

from zephyrex.lib.Environment import env_bool

IF_MATCH_REQUIRED_SETTING = "IF_MATCH_REQUIRED"
IF_MATCH_HEADER = "If-Match"
REQUIRED_DETAIL = "If-Match required"
STALE_DETAIL = "The record was changed since it was read"
# Timestamp fields naming a record's version, in order of preference.
VERSION_FIELDS = ("updated_at", "created_at")
# One entity-tag of an If-Match list: optionally weak, always quoted.
_ENTITY_TAG = re.compile(r'(?:W/)?"([^"]*)"')
_ANY_VERSION = "*"
# Requests that may write: a save replaces or removes a record the client
# read; any other write is an action on it.
_SAVE_METHODS = frozenset({"PUT", "PATCH", "DELETE"})
_WRITE_METHODS = _SAVE_METHODS | {"POST"}


def entity_version(entity: Any) -> Optional[str]:
    """The record's version: ``updated_at`` (else ``created_at``) serialised
    as the response body serialises it; None for a record without either."""
    for name in VERSION_FIELDS:
        if isinstance(entity, Mapping):
            value = entity.get(name)
        else:
            value = getattr(entity, name, None)
        if value:
            return value if isinstance(value, str) else str(jsonable_encoder(value))
    return None


def entity_etag(entity: Any) -> Optional[str]:
    """The record's ETag header value, ``"<version>"``."""
    version = entity_version(entity)
    return None if version is None else f'"{version}"'


def etag_headers(entity: Any) -> Dict[str, str]:
    """``{"ETag": ...}`` for the record, or no headers when it has no version."""
    etag = entity_etag(entity)
    return {} if etag is None else {"ETag": etag}


def if_match_required() -> bool:
    """Whether a bound write without If-Match is refused (428)."""
    return env_bool(IF_MATCH_REQUIRED_SETTING)


@dataclass(frozen=True)
class IfMatch:
    """A parsed If-Match: ``*`` (any current version) or a set of versions.

    Tags are compared by their opaque value: a ``W/`` prefix (as a proxy
    that compresses the body may add) and the quotes are not part of the
    version, and a bare unquoted version is accepted as copied from a row.
    """

    any_version: bool
    versions: frozenset[str]

    @classmethod
    def parse(cls, header: str) -> "IfMatch":
        value = header.strip()
        if value == _ANY_VERSION:
            return cls(any_version=True, versions=frozenset())
        tags = _ENTITY_TAG.findall(value)
        return cls(any_version=False, versions=frozenset(tags or [value]))

    def names(self, version: str) -> bool:
        """Whether this If-Match names the version (opaque value)."""
        return self.any_version or version in self.versions

    def matches(self, entity: Any) -> bool:
        """Whether the record exists at a version this If-Match names."""
        if self.any_version:
            return entity is not None
        version = entity_version(entity)
        return version is not None and self.names(version)


def none_match_hits(if_none_match: str, etag: str) -> bool:
    """Whether an If-None-Match names ``etag`` (weak comparison, RFC 9110
    13.1.2: the ``W/`` prefix is ignored; ``*`` names any current one)."""
    return IfMatch.parse(if_none_match).names(_opaque(etag))


def _opaque(etag: str) -> str:
    """An entity-tag's opaque value, without ``W/`` and quotes."""
    tags = _ENTITY_TAG.findall(etag)
    return tags[0] if tags else etag.strip()


class StaleVersionError(Exception):
    """The row is not at the version the request's If-Match names (raised by
    the database layer under the row lock, before any write). Whoever owns
    the record's visibility, the manager or the route, answers it with 412
    and the record as the requester may see it."""

    def __init__(self, table: str, entity_id: str) -> None:
        super().__init__(f"{table} {entity_id} changed since it was read")
        self.table = table
        self.entity_id = entity_id


class PreconditionError(HTTPException):
    """A refused If-Match precondition; :meth:`body` is the response body."""

    def __init__(
        self,
        status_code: int,
        message: str,
        extra: Mapping[str, Any],
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=message, headers=headers)
        self.message = message
        self.extra: Dict[str, Any] = dict(extra)

    def body(self) -> Dict[str, Any]:
        return {"detail": self.message, **jsonable_encoder(self.extra)}


class PreconditionFailed(PreconditionError):
    """412: the If-Match names a version that is no longer current.

    ``current`` is the record as the requester may see it, so the client can
    merge and retry with its ETag; a batch lists every stale id and their
    current records.
    """

    def __init__(self, current: Any, *, stale_ids: Optional[List[str]] = None) -> None:
        extra: Dict[str, Any] = {"current": current}
        headers: Optional[Dict[str, str]] = None
        if stale_ids is not None:
            extra["stale_ids"] = stale_ids
        else:
            headers = etag_headers(current) or None
        super().__init__(
            status.HTTP_412_PRECONDITION_FAILED, STALE_DETAIL, extra, headers
        )


class PreconditionRequired(PreconditionError):
    """428: If-Match is required and the write did not send one."""

    def __init__(self, *, missing_ids: Optional[List[str]] = None) -> None:
        extra = {} if missing_ids is None else {"missing_ids": missing_ids}
        super().__init__(status.HTTP_428_PRECONDITION_REQUIRED, REQUIRED_DETAIL, extra)


@dataclass
class _Expectations:
    table: str
    by_id: Dict[str, Optional[IfMatch]]
    # Whether IF_MATCH_REQUIRED holds this request to naming a version: a
    # save (PUT/PATCH/DELETE) is; an action (POST) is checked only when it
    # sends one.
    may_require: bool = True
    claimed: Set[str] = field(default_factory=set)

    def requires(self) -> bool:
        return self.may_require and if_match_required()


_expectations: ContextVar[Optional[_Expectations]] = ContextVar(
    "zephyrex_write_preconditions", default=None
)


def table_of(manager: Any) -> Optional[str]:
    """The table a logic manager writes, or None for a manager with no model."""
    try:
        return str(manager.DB.__tablename__)
    except (AttributeError, TypeError):
        return None


def _parse(header: Optional[str]) -> Optional[IfMatch]:
    return IfMatch.parse(header) if header and header.strip() else None


@contextmanager
def expect_versions(
    manager: Any,
    if_match_by_id: Mapping[str, Optional[str]],
    *,
    may_require: bool = True,
) -> Iterator[None]:
    """Bind, for the duration of a request's write, the If-Match each named
    record of ``manager``'s table is expected at (None: the request sent
    none). A stale write inside is answered with 412 carrying the record as
    ``manager.visible_current`` shows it to the requester."""
    table = table_of(manager)
    if table is None or not if_match_by_id:
        yield
        return
    by_id = {
        str(entity_id): _parse(header) for entity_id, header in if_match_by_id.items()
    }
    token = _expectations.set(_Expectations(table, by_id, may_require))
    try:
        yield
    except StaleVersionError as stale:
        raise PreconditionFailed(manager.visible_current(stale.entity_id)) from stale
    finally:
        _expectations.reset(token)


def route_target_id(path_params: Mapping[str, Any]) -> Optional[str]:
    """The record a custom route's path names: its ``{id}``, else its one
    ``{..._id}`` parameter; None when the path names no single record."""
    if "id" in path_params:
        return str(path_params["id"])
    named = [value for name, value in path_params.items() if name.endswith("_id")]
    return str(named[0]) if len(named) == 1 else None


@contextmanager
def expect_route_version(
    manager: Any, method: str, path_params: Mapping[str, Any], if_match: Optional[str]
) -> Iterator[None]:
    """Bind a custom route's If-Match to the record of the route's own
    manager that its path names, for any write the route makes to it. Reads
    bind nothing; a save (PUT/PATCH/DELETE) is held to IF_MATCH_REQUIRED, an
    action (POST) only to the If-Match it sends."""
    verb = method.upper()
    if verb not in _WRITE_METHODS:
        yield
        return
    target = route_target_id(path_params)
    if target is None:
        # The path names its record by more than one id (a membership is
        # /{team_id}/user/{user_id}): the route resolves it and binds it with
        # expect_route_record.
        token = _route_if_match.set(_RouteIfMatch(if_match, verb in _SAVE_METHODS))
        try:
            yield
        finally:
            _route_if_match.reset(token)
        return
    with expect_versions(
        manager, {target: if_match}, may_require=verb in _SAVE_METHODS
    ):
        yield


@dataclass(frozen=True)
class _RouteIfMatch:
    header: Optional[str]
    may_require: bool


_route_if_match: ContextVar[Optional[_RouteIfMatch]] = ContextVar(
    "zephyrex_route_if_match", default=None
)


@contextmanager
def expect_route_record(manager: Any, entity_id: str) -> Iterator[None]:
    """Hold a write to the record a custom route resolved itself to the
    request's If-Match, for a route whose path names its record by several
    ids. Outside such a request it binds nothing."""
    pending = _route_if_match.get()
    if pending is None:
        yield
        return
    with expect_versions(
        manager, {entity_id: pending.header}, may_require=pending.may_require
    ):
        yield


def expected_version(table: str, entity_id: str) -> Tuple[bool, Optional[IfMatch]]:
    """``(bound, if_match)`` for a record, without claiming it."""
    bound = _expectations.get()
    if bound is None or bound.table != table or entity_id not in bound.by_id:
        return False, None
    return True, bound.by_id[entity_id]


def missing_is_refused() -> bool:
    """Whether the bound request must name a version (IF_MATCH_REQUIRED, for
    a save)."""
    bound = _expectations.get()
    return bound is not None and bound.requires()


def claim_expected_version(table: str, entity_id: str) -> Optional[IfMatch]:
    """The If-Match the request names for this record's write, claimed once:
    a second write of the same record in the request is the server's own and
    is not held to the version the client read. Raises 428 when one is
    required and the request sent none."""
    bound, if_match = expected_version(table, entity_id)
    if not bound:
        return None
    expectations = _expectations.get()
    assert expectations is not None
    if entity_id in expectations.claimed:
        return None
    expectations.claimed.add(entity_id)
    if if_match is None and expectations.requires():
        raise PreconditionRequired()
    return if_match
