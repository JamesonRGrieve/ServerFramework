from pydantic import BaseModel, Field

from serverframework.lib.FieldACL import (
    collect_restricted_fields,
    filter_response_dict,
    get_required_permissions,
    requires,
    restricted_for_filter_or_order,
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


def test_model_with_no_restricted_fields():
    assert collect_restricted_fields(PublicModel) == []
    assert restricted_for_filter_or_order(PublicModel) == frozenset()


def test_get_required_permissions_no_metadata():
    info = PublicModel.model_fields["name"]
    assert get_required_permissions(info) == frozenset()


def test_get_required_permissions_with_metadata():
    info = UserModel.model_fields["ssn"]
    assert get_required_permissions(info) == frozenset({"auth.user.read_ssn"})
