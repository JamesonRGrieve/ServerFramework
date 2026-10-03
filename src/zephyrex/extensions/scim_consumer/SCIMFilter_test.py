# SPDX-License-Identifier: AGPL-3.0-or-later
"""SCIM filters (RFC 7644 §3.4.2.2): parsing, evaluation, refusal of
malformed and hostile input, sorting, and PATCH paths."""

from typing import Any, Dict, List

import pytest

from zephyrex.extensions.scim_consumer.SCIMErrors import ScimError
from zephyrex.extensions.scim_consumer.SCIMFilter import (
    MAX_FILTER_DEPTH,
    MAX_FILTER_LENGTH,
    AttrPath,
    Compare,
    Or,
    And,
    filter_resources,
    matches,
    parse_filter,
    parse_patch_path,
    sort_resources,
)

ALICE: Dict[str, Any] = {
    "id": "Id-Alice",
    "externalId": "ext-1",
    "userName": "Alice@Example.com",
    "name": {"givenName": "Alice", "familyName": "Liddell"},
    "emails": [
        {"value": "alice@example.com", "type": "work", "primary": True},
        {"value": "alice@home.example", "type": "home"},
    ],
    "active": True,
    "meta": {"lastModified": "2026-01-02T00:00:00Z"},
}
BOB: Dict[str, Any] = {
    "id": "id-bob",
    "userName": 'bob "the builder"',
    "emails": [{"value": "bob@example.org", "type": "work"}],
    "active": False,
    "meta": {"lastModified": "2025-06-01T00:00:00Z"},
}
PEOPLE = [ALICE, BOB]


def selected(text: str) -> List[str]:
    return [r["id"] for r in filter_resources(PEOPLE, text)]


def refused(text: str) -> ScimError:
    with pytest.raises(ScimError) as caught:
        parse_filter(text)
    assert caught.value.status == 400
    assert caught.value.scim_type == "invalidFilter"
    return caught.value


class TestEvaluation:
    def test_user_name_equality_ignores_case(self):
        assert selected('userName eq "alice@example.com"') == ["Id-Alice"]
        assert selected('USERNAME EQ "ALICE@EXAMPLE.COM"') == ["Id-Alice"]

    def test_ids_compare_exactly(self):
        assert selected('id eq "Id-Alice"') == ["Id-Alice"]
        assert selected('id eq "id-alice"') == []
        assert selected('externalId eq "EXT-1"') == []

    def test_string_operators(self):
        assert selected('userName co "BUILDER"') == ["id-bob"]
        assert selected('userName sw "ali"') == ["Id-Alice"]
        assert selected('userName ew ".com"') == ["Id-Alice"]
        assert selected('userName ne "alice@example.com"') == ["id-bob"]

    def test_presence_and_absence(self):
        assert selected("externalId pr") == ["Id-Alice"]
        assert selected("name.givenName pr") == ["Id-Alice"]
        assert selected("not (externalId pr)") == ["id-bob"]
        assert selected("externalId eq null") == ["id-bob"]

    def test_ordering_of_strings_and_dates(self):
        assert selected('meta.lastModified gt "2026-01-01T00:00:00Z"') == ["Id-Alice"]
        assert selected('meta.lastModified le "2025-06-01T00:00:00Z"') == ["id-bob"]
        assert selected('userName lt "b"') == ["Id-Alice"]
        assert selected('userName ge "b"') == ["id-bob"]

    def test_booleans(self):
        assert selected("active eq true") == ["Id-Alice"]
        assert selected("active eq false") == ["id-bob"]

    def test_and_binds_tighter_than_or(self):
        expression = parse_filter('a eq "1" or b eq "2" and c eq "3"')
        assert isinstance(expression, Or)
        assert isinstance(expression.right, And)
        assert selected('userName sw "bob" or active eq true and id eq "x"') == [
            "id-bob"
        ]
        assert selected(
            '(userName sw "bob" or active eq true) and id eq "Id-Alice"'
        ) == ["Id-Alice"]

    def test_multi_valued_attributes(self):
        assert selected('emails eq "alice@home.example"') == ["Id-Alice"]
        assert selected('emails.value co "example.org"') == ["id-bob"]
        assert selected('emails[type eq "home" and value co "home"]') == ["Id-Alice"]
        assert selected('emails[type eq "home"]') == ["Id-Alice"]
        assert selected('emails[not (type eq "work")]') == ["Id-Alice"]

    def test_core_schema_urns_name_core_attributes(self):
        urn = "urn:ietf:params:scim:schemas:core:2.0:User"
        assert selected(f'{urn}:userName sw "alice"') == ["Id-Alice"]
        assert selected(f'{urn}:name.familyName eq "liddell"') == ["Id-Alice"]

    def test_json_escapes_in_values(self):
        assert selected('userName eq "bob \\"the builder\\""') == ["id-bob"]
        assert selected('userName eq "bob \\u0022the builder\\u0022"') == ["id-bob"]

    def test_member_values_compare_exactly(self):
        group = {"members": [{"value": "User-1"}]}
        assert matches(parse_filter('members[value eq "User-1"]'), group)
        assert not matches(parse_filter('members[value eq "user-1"]'), group)

    def test_no_filter_selects_everything(self):
        assert selected("") == ["Id-Alice", "id-bob"]


class TestHostileInput:
    def test_an_escaped_quote_stays_inside_the_value(self):
        # The quote cannot end the string and smuggle in "or userName pr".
        text = 'userName eq "x\\" or userName pr \\""'
        expression = parse_filter(text)
        assert isinstance(expression, Compare)
        assert expression.value == 'x" or userName pr "'
        assert selected(text) == []

    def test_sql_in_a_value_is_only_a_value(self):
        assert selected('userName eq "x\'; DROP TABLE users; --"') == []
        assert selected('userName eq "%"') == []
        assert selected('userName co "_"') == []

    def test_operands_must_be_attribute_paths(self):
        refused("1 eq 1")
        refused('"a" eq "a"')
        refused('userName eq "a" or 1=1')
        refused('userName.a.b eq "x"')

    def test_malformed_filters(self):
        refused('userName eq "unterminated')
        refused("userName eq")
        refused('userName is "x"')
        refused('userName eq "x" and')
        refused('(userName eq "x"')
        refused('userName eq "x")')
        refused('userName eq "x" userName eq "y"')
        refused('emails[type eq "work"')
        refused('userName eq "\\q"')
        refused("not userName pr")
        refused("userName eq bare")

    def test_operators_must_suit_the_value(self):
        refused("active gt true")
        refused("userName co 1")
        refused("externalId gt null")

    def test_bounded_length_and_depth(self):
        refused("(" * (MAX_FILTER_DEPTH + 1) + "id pr" + ")" * (MAX_FILTER_DEPTH + 1))
        parse_filter("(" * MAX_FILTER_DEPTH + "id pr" + ")" * MAX_FILTER_DEPTH)
        refused('userName eq "' + "a" * MAX_FILTER_LENGTH + '"')


class TestSorting:
    def test_ascending_and_descending(self):
        ids = [r["id"] for r in sort_resources(PEOPLE, "userName", None)]
        assert ids == ["Id-Alice", "id-bob"]
        ids = [r["id"] for r in sort_resources(PEOPLE, "userName", "descending")]
        assert ids == ["id-bob", "Id-Alice"]

    def test_missing_values_last(self):
        ids = [r["id"] for r in sort_resources(PEOPLE, "externalId", "descending")]
        assert ids == ["Id-Alice", "id-bob"]

    def test_bad_sort_order(self):
        with pytest.raises(ScimError) as caught:
            sort_resources(PEOPLE, "userName", "sideways")
        assert caught.value.scim_type == "invalidValue"


class TestPatchPaths:
    def test_plain_and_sub_attribute(self):
        assert parse_patch_path("active").path == AttrPath(None, "active")
        assert parse_patch_path("name.givenName").path == AttrPath(
            None, "name", "givenName"
        )

    def test_value_filter_with_sub_attribute(self):
        parsed = parse_patch_path('emails[type eq "work"].value')
        assert parsed.path == AttrPath(None, "emails")
        assert parsed.sub_attribute == "value"
        assert matches(parsed.value_filter, {"type": "Work"}, "emails")

    def test_urn_prefixed(self):
        urn = "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User"
        parsed = parse_patch_path(f"{urn}:department")
        assert parsed.path == AttrPath(urn, "department")

    def test_malformed_paths(self):
        for text in ('emails[type eq "work"]value', "emails]", "", 'a["x"]', "a b"):
            with pytest.raises(ScimError) as caught:
                parse_patch_path(text)
            assert caught.value.scim_type == "invalidPath"
