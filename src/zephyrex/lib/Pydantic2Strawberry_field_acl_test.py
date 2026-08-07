"""Tests for the GraphQL ``_apply_field_acl`` resolver helper (Item 45).

Item 45 calls for the same Pydantic ``Field(..., requires=[...])`` metadata
that drives REST field stripping to also drive GraphQL response stripping.
The framework's GraphQL CRUD resolvers (``_add_query_resolver``,
``_add_list_query_resolver``, ``_add_create_mutation_resolver``,
``_add_update_mutation_resolver``) call into ``GraphQLManager._apply_field_acl``
before returning, so requesters lacking a permission see the same omit/mask
behavior on the GraphQL surface as on REST.

These tests exercise the helper directly without spinning up a full schema —
the resolver wiring is verified by inspection of the call sites at the top
of each file. Authors who add new resolvers to ``GraphQLManager`` follow the
same call-into-``_apply_field_acl`` convention.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel, Field

from zephyrex.lib.FieldACL import requires


class _Sensitive(BaseModel):
    """A model with one open and two restricted fields, mirroring the
    pattern in the FieldACL_test models."""

    id: int
    name: str
    ssn: str = Field(..., json_schema_extra=requires("auth.user.read_ssn"))
    salary: float = Field(
        ..., json_schema_extra=requires("hr.read_salary", "hr.read_employee")
    )


class _OpenModel(BaseModel):
    id: int
    name: str


class _FakeRequester:
    def __init__(self, granted: set[str]):
        self._granted = granted

    def has_permission(self, name: str) -> bool:
        return name in self._granted


class _FakeManagerWithBaseModel:
    """Mirrors core BLL managers (``BaseModel`` ClassVar)."""

    BaseModel = _Sensitive

    def __init__(self, requester):
        self.requester = requester


class _FakeManagerWithModel:
    """Mirrors extension managers (``Model`` ClassVar)."""

    Model = _Sensitive

    def __init__(self, requester):
        self.requester = requester


class _FakeManagerNoRequester:
    BaseModel = _Sensitive
    requester = None


def _make_manager(granted: set[str], cls=_FakeManagerWithBaseModel):
    return cls(_FakeRequester(granted))


def _make_graphql_manager() -> Any:
    """Build a GraphQLManager with a stub model registry — only the
    helper method is exercised so the registry contents do not matter."""
    from zephyrex.lib.Pydantic2Strawberry import GraphQLManager

    registry = MagicMock()
    registry.model_relationships = []
    return GraphQLManager(model_registry=registry)


# ---------------------------------------------------------------------------
# Helper exists and applies the FieldACL contract
# ---------------------------------------------------------------------------


def test_apply_field_acl_strips_disallowed_fields_on_basemodel_manager():
    """A requester without ``auth.user.read_ssn`` sees the SSN field
    stripped from the GraphQL resolver result."""
    gm = _make_graphql_manager()
    manager = _make_manager(set())
    user = _Sensitive(id=1, name="Alice", ssn="123-45-6789", salary=100_000.0)

    result = gm._apply_field_acl(manager, user)
    assert isinstance(result, dict)
    assert "ssn" not in result
    assert "salary" not in result
    assert result["id"] == 1
    assert result["name"] == "Alice"


def test_apply_field_acl_includes_field_when_permission_granted():
    gm = _make_graphql_manager()
    manager = _make_manager({"auth.user.read_ssn"})
    user = _Sensitive(id=1, name="Bob", ssn="111-22-3333", salary=80_000.0)

    result = gm._apply_field_acl(manager, user)
    assert result["ssn"] == "111-22-3333"


def test_apply_field_acl_and_semantics_for_multi_permission_field():
    """The salary field requires both ``hr.read_salary`` AND
    ``hr.read_employee``; partial grant must omit."""
    gm = _make_graphql_manager()
    manager = _make_manager({"hr.read_salary"})
    user = _Sensitive(id=1, name="Alice", ssn="x", salary=100_000.0)

    result = gm._apply_field_acl(manager, user)
    assert "salary" not in result

    manager2 = _make_manager({"hr.read_salary", "hr.read_employee"})
    result2 = gm._apply_field_acl(manager2, user)
    assert result2["salary"] == 100_000.0


# ---------------------------------------------------------------------------
# List / collection results
# ---------------------------------------------------------------------------


def test_apply_field_acl_to_list_strips_per_row():
    """List results from ``manager.list`` are filtered per row, sharing
    the per-(model, requester) cache for performance (Item 45)."""
    gm = _make_graphql_manager()
    manager = _make_manager(set())
    rows = [
        _Sensitive(id=1, name="Alice", ssn="111", salary=100_000.0),
        _Sensitive(id=2, name="Bob", ssn="222", salary=110_000.0),
    ]

    result = gm._apply_field_acl(manager, rows)
    assert isinstance(result, list)
    assert len(result) == 2
    for row in result:
        assert "ssn" not in row
        assert "salary" not in row
        assert row["name"] in {"Alice", "Bob"}


# ---------------------------------------------------------------------------
# Pass-through paths (no-op cases)
# ---------------------------------------------------------------------------


def test_apply_field_acl_returns_none_unchanged():
    """A None result from ``manager.get`` (not-found) round-trips
    untouched."""
    gm = _make_graphql_manager()
    manager = _make_manager(set())
    assert gm._apply_field_acl(manager, None) is None


def test_apply_field_acl_no_op_when_no_requester():
    """System-key audit jobs and framework-internal callers reach
    resolvers without a requester; the helper is a no-op so they still
    work (per the FieldACL contract)."""
    gm = _make_graphql_manager()
    manager = _FakeManagerNoRequester()
    user = _Sensitive(id=1, name="Alice", ssn="x", salary=0.0)
    result = gm._apply_field_acl(manager, user)
    # Pass-through: still a model instance, not a dict.
    assert result is user


def test_apply_field_acl_no_op_when_model_has_no_restricted_fields():
    """A model without ``requires``-tagged fields is a pass-through —
    the helper does not pay the dict-conversion cost."""
    gm = _make_graphql_manager()

    class _Manager:
        BaseModel = _OpenModel
        requester = _FakeRequester(set())

    obj = _OpenModel(id=1, name="open")
    result = gm._apply_field_acl(_Manager(), obj)
    assert result is obj


def test_apply_field_acl_handles_extension_manager_model_attribute():
    """Extension managers expose ``Model`` rather than ``BaseModel``; the
    helper consults both."""
    gm = _make_graphql_manager()
    manager = _make_manager(set(), cls=_FakeManagerWithModel)
    user = _Sensitive(id=1, name="Alice", ssn="111", salary=100.0)

    result = gm._apply_field_acl(manager, user)
    assert "ssn" not in result


def test_apply_field_acl_no_op_when_manager_has_no_model_attribute():
    """A manager that exposes neither ``BaseModel`` nor ``Model`` is a
    pass-through (defensive — the helper cannot know which fields are
    restricted without a model class)."""
    gm = _make_graphql_manager()

    class _BareManager:
        requester = _FakeRequester(set())

    user = _Sensitive(id=1, name="Alice", ssn="111", salary=100.0)
    result = gm._apply_field_acl(_BareManager(), user)
    assert result is user


# ---------------------------------------------------------------------------
# Sentinel-mode override (Item 45 — operator-configurable mask vs omit)
# ---------------------------------------------------------------------------


def test_apply_field_acl_respects_mask_sentinel(monkeypatch):
    """``FIELD_ACL_SENTINEL=mask`` replaces disallowed fields with the
    framework's mask placeholder rather than dropping them."""
    from zephyrex.lib.FieldACL import MASK_PLACEHOLDER

    monkeypatch.setenv("FIELD_ACL_SENTINEL", "mask")
    gm = _make_graphql_manager()
    manager = _make_manager(set())
    user = _Sensitive(id=1, name="Alice", ssn="x", salary=0.0)

    result = gm._apply_field_acl(manager, user)
    assert result["ssn"] == MASK_PLACEHOLDER
    assert result["salary"] == MASK_PLACEHOLDER


def test_apply_field_acl_default_omit_when_env_unset(monkeypatch):
    """Without ``FIELD_ACL_SENTINEL`` set, the default ``omit`` mode
    drops the field rather than masking."""
    monkeypatch.delenv("FIELD_ACL_SENTINEL", raising=False)
    gm = _make_graphql_manager()
    manager = _make_manager(set())
    user = _Sensitive(id=1, name="Alice", ssn="x", salary=0.0)

    result = gm._apply_field_acl(manager, user)
    assert "ssn" not in result
    assert "salary" not in result


# ---------------------------------------------------------------------------
# Resolver wiring sanity checks (the helper is reachable from every CRUD
# resolver call site)
# ---------------------------------------------------------------------------


def test_helper_called_in_query_resolver_source():
    """Sanity: the get resolver path calls into ``_apply_field_acl``
    before returning. Reading the source of ``_add_query_resolver``
    locks this in so a future refactor cannot silently break the
    Item 45 contract."""
    import inspect

    from zephyrex.lib.Pydantic2Strawberry import GraphQLManager

    src = inspect.getsource(GraphQLManager._add_query_resolver)
    assert src.count("self._apply_field_acl") >= 2  # user + non-user branches


def test_helper_called_in_list_resolver_source():
    import inspect

    from zephyrex.lib.Pydantic2Strawberry import GraphQLManager

    src = inspect.getsource(GraphQLManager._add_list_query_resolver)
    assert "self._apply_field_acl" in src


def test_helper_called_in_create_resolver_source():
    import inspect

    from zephyrex.lib.Pydantic2Strawberry import GraphQLManager

    src = inspect.getsource(GraphQLManager._add_create_mutation_resolver)
    assert "self._apply_field_acl" in src


def test_helper_called_in_update_resolver_source():
    import inspect

    from zephyrex.lib.Pydantic2Strawberry import GraphQLManager

    src = inspect.getsource(GraphQLManager._add_update_mutation_resolver)
    assert "self._apply_field_acl" in src
