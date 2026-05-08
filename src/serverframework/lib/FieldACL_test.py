import pytest
from pydantic import BaseModel, Field

from serverframework.lib.FieldACL import (
    DEFAULT_SENTINEL_MODE,
    MASK_PLACEHOLDER,
    SENTINEL_MASK,
    SENTINEL_OMIT,
    FieldACLCache,
    FieldACLViolation,
    Sensitive,
    apply_field_acl_to_response,
    collect_restricted_fields,
    enforce_query_field_access,
    filter_response_dict,
    get_required_permissions,
    requires,
    restricted_for_filter_or_order,
    validate_query_field_access,
)


class UserModel(BaseModel):
    id: int
    name: str
    ssn: str = Field(..., json_schema_extra=requires("auth.user.read_ssn"))
    salary: float = Field(
        ...,
        json_schema_extra=requires("hr.compensation.read", "hr.employee.read"),
    )


class PublicModel(BaseModel):
    id: int
    name: str


def test_filter_includes_field_when_permission_granted():
    user = UserModel(id=1, name="Alice", ssn="123-45-6789", salary=100000.0)
    has_perm = lambda p: p == "auth.user.read_ssn"
    out = filter_response_dict(user, has_perm)
    assert out["ssn"] == "123-45-6789"


def test_filter_omits_field_when_permission_denied():
    user = UserModel(id=1, name="Alice", ssn="123-45-6789", salary=100000.0)
    has_perm = lambda p: False
    out = filter_response_dict(user, has_perm)
    assert "ssn" not in out
    assert out["id"] == 1
    assert out["name"] == "Alice"


def test_filter_and_semantics_requires_all_permissions():
    user = UserModel(id=1, name="Alice", ssn="123-45-6789", salary=100000.0)

    has_one = lambda p: p == "hr.compensation.read"
    out = filter_response_dict(user, has_one)
    assert "salary" not in out

    has_both = lambda p: p in {"hr.compensation.read", "hr.employee.read"}
    out2 = filter_response_dict(user, has_both)
    assert out2["salary"] == 100000.0


def test_unrestricted_field_always_appears():
    user = UserModel(id=1, name="Alice", ssn="x", salary=0.0)
    has_perm = lambda p: False
    out = filter_response_dict(user, has_perm)
    assert out["id"] == 1
    assert out["name"] == "Alice"


def test_collect_restricted_fields_sorted():
    result = collect_restricted_fields(UserModel)
    assert result == [
        ("salary", frozenset({"hr.compensation.read", "hr.employee.read"})),
        ("ssn", frozenset({"auth.user.read_ssn"})),
    ]


def test_restricted_for_filter_or_order():
    result = restricted_for_filter_or_order(UserModel)
    assert result == frozenset({"ssn", "salary"})


def test_enforce_query_field_access_raises_on_disallowed():
    with pytest.raises(FieldACLViolation) as exc_info:
        enforce_query_field_access(
            UserModel,
            ["id", "ssn"],
            has_permission=lambda _: False,
            context="filter",
        )
    assert "ssn" in exc_info.value.field_names


def test_enforce_query_field_access_passes_when_permitted():
    enforce_query_field_access(
        UserModel,
        ["id", "ssn"],
        has_permission=lambda _: True,
    )


def test_enforce_query_field_access_no_permission_resolver_is_no_op():
    enforce_query_field_access(UserModel, ["ssn"], has_permission=None)


def test_model_with_no_restricted_fields():
    assert collect_restricted_fields(PublicModel) == []
    assert restricted_for_filter_or_order(PublicModel) == frozenset()


def test_get_required_permissions_no_metadata():
    info = PublicModel.model_fields["name"]
    assert get_required_permissions(info) == frozenset()


def test_get_required_permissions_with_metadata():
    info = UserModel.model_fields["ssn"]
    assert get_required_permissions(info) == frozenset({"auth.user.read_ssn"})


# ---------------------------------------------------------------------------
# Sensitive[T] alias
# ---------------------------------------------------------------------------


class SensitiveDemo(BaseModel):
    public: str
    secret: str = Field(..., **Sensitive("auth.read_secret"))
    multi_secret: str = Field(
        ...,
        **Sensitive("auth.read_a", additional=["auth.read_b"]),
    )


def test_sensitive_alias_attaches_metadata():
    assert get_required_permissions(SensitiveDemo.model_fields["secret"]) == frozenset(
        {"auth.read_secret"}
    )
    assert get_required_permissions(
        SensitiveDemo.model_fields["multi_secret"]
    ) == frozenset({"auth.read_a", "auth.read_b"})


def test_sensitive_demo_filter_omit_default():
    instance = SensitiveDemo(public="ok", secret="s", multi_secret="ms")
    out = filter_response_dict(instance, lambda p: False)
    assert out == {"public": "ok"}


# ---------------------------------------------------------------------------
# FieldACLCache
# ---------------------------------------------------------------------------


def test_cache_memoizes_per_requester():
    cache = FieldACLCache()
    calls: list[str] = []

    def has_perm(p):
        calls.append(p)
        return p == "auth.user.read_ssn"

    a1 = cache.allowed_fields(UserModel, requester_id=1, has_permission=has_perm)
    a2 = cache.allowed_fields(UserModel, requester_id=1, has_permission=has_perm)
    # Second call uses cache so the resolver fires no additional times.
    assert a1 == a2
    assert calls.count("auth.user.read_ssn") == 1


def test_cache_distinguishes_requesters():
    cache = FieldACLCache()
    a = cache.allowed_fields(
        UserModel, requester_id=1, has_permission=lambda p: True
    )
    b = cache.allowed_fields(
        UserModel, requester_id=2, has_permission=lambda p: False
    )
    assert "ssn" in a
    assert "ssn" not in b


def test_cache_includes_unrestricted_fields_always():
    cache = FieldACLCache()
    allowed = cache.allowed_fields(
        UserModel, requester_id=1, has_permission=lambda p: False
    )
    assert "id" in allowed
    assert "name" in allowed


# ---------------------------------------------------------------------------
# apply_field_acl_to_response — REST/GraphQL response integration helper
# ---------------------------------------------------------------------------


def test_apply_to_dict_omits_disallowed():
    user = UserModel(id=1, name="Alice", ssn="123", salary=1.0)
    payload = user.model_dump()
    out = apply_field_acl_to_response(payload, UserModel, lambda p: False)
    assert "ssn" not in out
    assert "salary" not in out
    assert out["name"] == "Alice"


def test_apply_to_dict_passes_when_permitted():
    user = UserModel(id=1, name="Alice", ssn="123", salary=1.0)
    payload = user.model_dump()
    out = apply_field_acl_to_response(payload, UserModel, lambda p: True)
    assert out["ssn"] == "123"
    assert out["salary"] == 1.0


def test_apply_to_list_filters_each_row():
    users = [
        UserModel(id=i, name=f"u{i}", ssn=f"ssn{i}", salary=10.0).model_dump()
        for i in range(5)
    ]
    out = apply_field_acl_to_response(users, UserModel, lambda p: False)
    for row in out:
        assert "ssn" not in row
        assert "salary" not in row


def test_apply_mask_mode_replaces_value():
    user = UserModel(id=1, name="Alice", ssn="123", salary=1.0)
    payload = user.model_dump()
    out = apply_field_acl_to_response(
        payload, UserModel, lambda p: False, sentinel_mode=SENTINEL_MASK
    )
    assert out["ssn"] == MASK_PLACEHOLDER
    assert out["salary"] == MASK_PLACEHOLDER
    assert out["name"] == "Alice"


def test_apply_returns_unchanged_when_no_permission_resolver():
    user = UserModel(id=1, name="Alice", ssn="123", salary=1.0).model_dump()
    out = apply_field_acl_to_response(user, UserModel, None)
    assert out == user


def test_apply_returns_unchanged_when_model_cls_none():
    payload = {"foo": "bar", "ssn": "x"}
    out = apply_field_acl_to_response(payload, None, lambda p: False)
    assert out == payload


def test_apply_returns_unchanged_for_unrestricted_model():
    payload = PublicModel(id=1, name="Alice").model_dump()
    out = apply_field_acl_to_response(payload, PublicModel, lambda p: False)
    assert out == payload


def test_apply_handles_basemodel_input():
    user = UserModel(id=1, name="Alice", ssn="123", salary=1.0)
    out = apply_field_acl_to_response(user, UserModel, lambda p: False)
    assert "ssn" not in out
    assert out["name"] == "Alice"


def test_apply_handles_none_payload():
    assert apply_field_acl_to_response(None, UserModel, lambda p: False) is None


def test_apply_with_shared_cache_amortizes_cost():
    cache = FieldACLCache()
    calls: list[str] = []

    def has_perm(p):
        calls.append(p)
        return False

    rows = [
        UserModel(id=i, name=f"u{i}", ssn=f"s{i}", salary=1.0).model_dump()
        for i in range(20)
    ]
    out = apply_field_acl_to_response(
        rows, UserModel, has_perm, cache=cache, requester_id=42
    )
    # Once amortized, the cache resolves both restricted permissions for
    # this requester once each, regardless of how many rows are filtered.
    assert len(out) == 20
    for row in out:
        assert "ssn" not in row
    # 'auth.user.read_ssn' resolves once because allowed-set computation
    # short-circuits on the first denial — but salary requires both
    # permissions so they fire once each. Net: at most one call per
    # restricted permission across the whole list.
    assert calls.count("auth.user.read_ssn") == 1


# ---------------------------------------------------------------------------
# validate_query_field_access — search / order-by inference-attack rejection
# ---------------------------------------------------------------------------


def test_validate_rejects_restricted_field():
    disallowed = validate_query_field_access(
        UserModel, ["ssn"], lambda p: False, context="sort_by"
    )
    assert disallowed == ["ssn"]


def test_validate_passes_unrestricted_field():
    disallowed = validate_query_field_access(
        UserModel, ["name"], lambda p: False, context="sort_by"
    )
    assert disallowed == []


def test_validate_passes_when_grant_held():
    disallowed = validate_query_field_access(
        UserModel,
        ["ssn", "salary"],
        lambda p: True,
        context="filter",
    )
    assert disallowed == []


def test_validate_partial_grants_block_all_required():
    disallowed = validate_query_field_access(
        UserModel,
        ["salary"],
        lambda p: p == "hr.compensation.read",
        context="sort_by",
    )
    assert disallowed == ["salary"]


def test_validate_returns_empty_when_no_resolver():
    disallowed = validate_query_field_access(
        UserModel, ["ssn", "name"], None, context="filter"
    )
    assert disallowed == []


def test_validate_returns_empty_when_no_restricted_fields():
    disallowed = validate_query_field_access(
        PublicModel, ["id", "name"], lambda p: False, context="sort_by"
    )
    assert disallowed == []


def test_validate_handles_empty_field_list():
    disallowed = validate_query_field_access(
        UserModel, [], lambda p: False, context="sort_by"
    )
    assert disallowed == []


def test_field_acl_violation_carries_context():
    err = FieldACLViolation(["ssn", "salary"], "sort_by")
    assert err.field_names == ("ssn", "salary")
    assert err.context == "sort_by"
    assert "sort_by" in str(err)
    assert "ssn" in str(err)


def test_default_sentinel_mode_is_omit():
    assert DEFAULT_SENTINEL_MODE == SENTINEL_OMIT


# ---------------------------------------------------------------------------
# Pydantic2FastAPI integration helpers (Item 45 REST wiring)
# ---------------------------------------------------------------------------


class _FakeRequester:
    def __init__(self, perms: set[str]):
        self.id = "r1"
        self._perms = perms

    def has_permission(self, name: str) -> bool:
        return name in self._perms


class _FakeManager:
    def __init__(self, perms: set[str]):
        self.requester = _FakeRequester(perms)


def test_resolve_has_permission_no_requester():
    from serverframework.lib.Pydantic2FastAPI import _resolve_has_permission

    class M:
        requester = None

    assert _resolve_has_permission(M()) is None


def test_resolve_has_permission_no_method():
    from serverframework.lib.Pydantic2FastAPI import _resolve_has_permission

    class R:
        id = "r1"

    class M:
        requester = R()

    assert _resolve_has_permission(M()) is None


def test_resolve_has_permission_returns_callable():
    from serverframework.lib.Pydantic2FastAPI import _resolve_has_permission

    mgr = _FakeManager({"auth.user.read_ssn"})
    fn = _resolve_has_permission(mgr)
    assert callable(fn)
    assert fn("auth.user.read_ssn") is True
    assert fn("auth.user.read_other") is False


def test_apply_field_acl_to_payload_filters_dict():
    from serverframework.lib.Pydantic2FastAPI import apply_field_acl_to_payload

    mgr = _FakeManager(set())
    user = UserModel(id=1, name="Alice", ssn="123", salary=1.0).model_dump()
    out = apply_field_acl_to_payload(user, mgr, UserModel)
    assert "ssn" not in out
    assert out["name"] == "Alice"


def test_apply_field_acl_to_payload_no_op_when_no_permission_function():
    from serverframework.lib.Pydantic2FastAPI import apply_field_acl_to_payload

    class M:
        requester = None

    user = UserModel(id=1, name="Alice", ssn="123", salary=1.0).model_dump()
    out = apply_field_acl_to_payload(user, M(), UserModel)
    assert out == user


def test_apply_field_acl_to_payload_respects_grant():
    from serverframework.lib.Pydantic2FastAPI import apply_field_acl_to_payload

    mgr = _FakeManager({"auth.user.read_ssn", "hr.compensation.read", "hr.employee.read"})
    user = UserModel(id=1, name="Alice", ssn="123", salary=1.0).model_dump()
    out = apply_field_acl_to_payload(user, mgr, UserModel)
    assert out["ssn"] == "123"
    assert out["salary"] == 1.0


def test_validate_field_acl_query_raises_on_restricted_sort():
    from fastapi import HTTPException

    from serverframework.lib.Pydantic2FastAPI import validate_field_acl_query

    mgr = _FakeManager(set())
    with pytest.raises(HTTPException) as exc:
        validate_field_acl_query(mgr, UserModel, ["ssn"], "sort_by")
    assert exc.value.status_code == 403
    assert "sort_by" in str(exc.value.detail)


def test_validate_field_acl_query_passes_when_grant_held():
    from serverframework.lib.Pydantic2FastAPI import validate_field_acl_query

    mgr = _FakeManager({"auth.user.read_ssn"})
    validate_field_acl_query(mgr, UserModel, ["ssn"], "sort_by")  # no raise


def test_validate_field_acl_query_no_op_without_resolver():
    from serverframework.lib.Pydantic2FastAPI import validate_field_acl_query

    class M:
        requester = None

    validate_field_acl_query(M(), UserModel, ["ssn"], "sort_by")  # no raise
