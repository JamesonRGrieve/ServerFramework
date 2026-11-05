# SPDX-License-Identifier: AGPL-3.0-or-later
"""Who owns a user-owned record, as its manager's create and update see it.

A user acts as themselves: a record they create is theirs, whatever owner
the call names, for a single create and for each of a batch's
``entities`` alike. ROOT and SYSTEM act on others' behalf, so they may
name another owner. An update never moves a record's owner or team, and
fields only the server writes (lifecycle, bookkeeping) are dropped from a
user's writes.

A manager composes these in its own ``create`` and ``update``::

    def create(self, **kwargs):
        return super().create(**each_created(kwargs, owned_by(self.requester.id)))

    def update(self, id, **kwargs):
        return super().update(id, **without(kwargs, OWNERSHIP_FIELDS))
"""

from typing import Any, Callable, Dict, Iterable, List, Optional

from zephyrex.database.StaticPermissions import is_root_id, is_system_id

# What places a record with its owner: an update never changes them.
OWNERSHIP_FIELDS = ("user_id", "team_id")

Prepare = Callable[[Dict[str, Any]], Dict[str, Any]]


def server_side(requester_id: Optional[str]) -> bool:
    """Whether the requester is ROOT or SYSTEM, who act on others' behalf;
    users (and no requester at all) act as themselves."""
    return bool(requester_id) and (
        is_root_id(str(requester_id)) or is_system_id(str(requester_id))
    )


def each_created(kwargs: Dict[str, Any], prepare: Prepare) -> Dict[str, Any]:
    """``kwargs`` for a create, or each of a batch's ``entities``, prepared.
    Every set of fields is copied before ``prepare`` sees it."""
    if isinstance(kwargs.get("entities"), list):
        return {**kwargs, "entities": [prepare(dict(e)) for e in kwargs["entities"]]}
    return prepare(dict(kwargs))


def created_records(result: Any) -> List[Any]:
    """What a create made: one record, or a batch's."""
    return result if isinstance(result, list) else [result]


def owned_by(
    requester_id: str,
    *,
    server_may_leave_unowned: bool = False,
    owner_never_internal: bool = False,
) -> Prepare:
    """Fields whose ``user_id`` is the requester. ROOT and SYSTEM may name
    another owner; when they name none (absent or empty), it is theirs.

    ``server_may_leave_unowned``: ROOT and SYSTEM may also name no owner
    at all, by passing ``user_id=None``; only an absent ``user_id`` makes
    the record theirs (a message an agent posts has no author).

    ``owner_never_internal``: the owner is never ROOT, SYSTEM or the
    template user (403), ROOT and SYSTEM themselves included, for a record
    that would let whoever holds it sign in as its owner (a linked
    identity, a security key)."""

    def prepare(fields: Dict[str, Any]) -> Dict[str, Any]:
        if not server_side(requester_id):
            fields["user_id"] = requester_id
        elif server_may_leave_unowned:
            fields.setdefault("user_id", requester_id)
        elif not fields.get("user_id"):
            fields["user_id"] = requester_id
        if owner_never_internal:
            # BLL_Auth's managers build on this package: imported when used.
            from zephyrex.logic.BLL_Auth import refuse_internal_account

            refuse_internal_account(fields["user_id"])
        return fields

    return prepare


def without(fields: Dict[str, Any], names: Iterable[str]) -> Dict[str, Any]:
    """``fields`` with each of ``names`` dropped, in place."""
    for name in names:
        fields.pop(name, None)
    return fields


def server_only(
    requester_id: str, fields: Dict[str, Any], names: Iterable[str]
) -> Dict[str, Any]:
    """``fields`` with each of ``names`` dropped, in place, unless ROOT or
    SYSTEM wrote them: the server alone writes those fields."""
    return fields if server_side(requester_id) else without(fields, names)
