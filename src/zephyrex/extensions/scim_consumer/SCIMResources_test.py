# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading SCIM Users and Groups into the framework's fields, rendering
them back, versions, paging and attribute selection."""

from datetime import datetime, timezone

import pytest

from zephyrex.extensions.scim_consumer.SCIMErrors import ScimError
from zephyrex.extensions.scim_consumer.SCIMResources import (
    MAX_PAGE_SIZE,
    Reference,
    etag_matches,
    group_resource,
    list_response,
    page_bounds,
    parse_group,
    parse_user,
    project,
    user_resource,
    version_of,
)

ROW = {
    "id": "u-1",
    "username": "jdoe",
    "email": "jdoe@example.com",
    "first_name": "Jane",
    "last_name": "Doe",
    "display_name": None,
    "active": True,
    "language": "en",
    "timezone": None,
    "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
    "updated_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
}


def invalid(payload) -> ScimError:
    with pytest.raises(ScimError) as caught:
        parse_user(payload)
    assert caught.value.status == 400
    return caught.value


class TestParseUser:
    def test_the_fields(self):
        fields = parse_user(
            {
                "userName": " jdoe ",
                "name": {"givenName": "Jane", "familyName": "Doe"},
                "emails": [
                    {"value": "home@example.com", "type": "home"},
                    {"value": "work@example.com", "primary": "True"},
                ],
                "active": "false",
                "locale": "en-CA",
                "externalId": "00u1",
            }
        )
        assert fields.username == "jdoe"
        assert fields.email == "work@example.com"
        assert (fields.first_name, fields.last_name) == ("Jane", "Doe")
        assert fields.active is False
        assert fields.language == "en-CA"
        assert fields.external_id == "00u1"

    def test_first_email_when_none_is_primary(self):
        fields = parse_user({"userName": "x", "emails": [{"value": "a@example.com"}]})
        assert fields.email == "a@example.com"

    def test_a_user_name_that_is_an_address_stands_in(self):
        fields = parse_user({"userName": "jane@example.com"})
        assert fields.email == "jane@example.com"
        assert fields.active is True

    def test_refusals(self):
        assert invalid({}).scim_type == "invalidValue"
        assert invalid({"userName": "x"}).scim_type == "invalidValue"
        assert invalid({"userName": 7}).scim_type == "invalidValue"
        assert invalid({"userName": "a@b.c", "active": "maybe"}).scim_type == (
            "invalidValue"
        )
        assert invalid({"userName": "a@b.c", "emails": "a@b.c"}).scim_type == (
            "invalidValue"
        )
        assert invalid(["not", "an", "object"]).scim_type == "invalidSyntax"


class TestParseGroup:
    def test_members(self):
        fields = parse_group(
            {"displayName": "Eng", "members": [{"value": "u-1"}, {"value": "u-1"}]}
        )
        assert fields.member_ids == ("u-1",)

    def test_groups_do_not_nest(self):
        with pytest.raises(ScimError):
            parse_group(
                {"displayName": "Eng", "members": [{"value": "g", "type": "Group"}]}
            )
        with pytest.raises(ScimError):
            parse_group({"members": []})


class TestRendering:
    def test_user(self):
        team = Reference("g-1", "Eng", "https://h/v1/scim/v2/Groups/g-1")
        resource = user_resource(ROW, "00u1", [team], "https://h/v1/scim/v2/Users/u-1")
        assert resource["userName"] == "jdoe"
        assert resource["name"]["formatted"] == "Jane Doe"
        assert resource["emails"][0] == {
            "value": "jdoe@example.com",
            "type": "work",
            "primary": True,
        }
        assert resource["groups"][0]["value"] == "g-1"
        assert resource["meta"]["created"] == "2026-01-01T00:00:00Z"
        assert resource["meta"]["lastModified"] == "2026-01-02T00:00:00Z"
        assert resource["meta"]["version"] == version_of(resource)
        # The resource reads back into the fields it was rendered from.
        assert parse_user(resource).columns() == {
            k: ROW[k] for k in parse_user(resource).columns()
        }

    def test_version_follows_content_not_meta(self):
        first = user_resource(ROW, None, [], "l")
        moved = user_resource(
            {**ROW, "updated_at": datetime.now(timezone.utc)}, None, [], "l"
        )
        changed = user_resource({**ROW, "first_name": "Janet"}, None, [], "l")
        assert first["meta"]["version"] == moved["meta"]["version"]
        assert first["meta"]["version"] != changed["meta"]["version"]

    def test_group(self):
        member = Reference("u-1", "Jane", "https://h/v1/scim/v2/Users/u-1")
        resource = group_resource({"id": "g-1", "name": "Eng"}, None, [member], "l")
        assert resource["members"] == [
            {"value": "u-1", "$ref": member.location, "display": "Jane", "type": "User"}
        ]


class TestProtocolHelpers:
    def test_etags(self):
        assert etag_matches('W/"abc"', 'W/"abc"')
        assert etag_matches('"abc"', 'W/"abc"')
        assert etag_matches('W/"x", W/"abc"', 'W/"abc"')
        assert etag_matches("*", 'W/"abc"')
        assert not etag_matches('W/"abd"', 'W/"abc"')
        assert not etag_matches(None, 'W/"abc"')

    def test_page_bounds(self):
        assert page_bounds(None, None) == (1, 100)
        assert page_bounds("0", "-5") == (1, 0)
        assert page_bounds("3", "100000") == (3, MAX_PAGE_SIZE)
        with pytest.raises(ScimError):
            page_bounds("one", None)

    def test_list_response_pages(self):
        page = list_response([{"id": str(i)} for i in range(5)], 2, 2)
        assert page["totalResults"] == 5
        assert page["itemsPerPage"] == 2
        assert [r["id"] for r in page["Resources"]] == ["1", "2"]
        assert list_response([{"id": "a"}], 1, 0)["Resources"] == []

    def test_projection(self):
        resource = user_resource(ROW, None, [], "l")
        assert set(project(resource, "userName,name.givenName", None)) == {
            "schemas",
            "id",
            "meta",
            "userName",
            "name",
        }
        assert "emails" not in project(resource, None, "emails")
        assert "id" in project(resource, None, "id")
