# SPDX-License-Identifier: AGPL-3.0-or-later
"""SCIM PATCH (RFC 7644 §3.5.2), with the request bodies Okta and
Microsoft Entra ID document sending."""

import copy
from typing import Any, Dict, List

import pytest

from zephyrex.extensions.scim_consumer.SCIMErrors import ScimError
from zephyrex.extensions.scim_consumer.SCIMPatch import PATCH_OP_URN, apply_patch
from zephyrex.extensions.scim_consumer.SCIMResources import (
    GROUP_READ_ONLY,
    USER_READ_ONLY,
    parse_group,
    parse_user,
)

USER: Dict[str, Any] = {
    "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
    "id": "u-1",
    "userName": "jdoe",
    "name": {"givenName": "Jane", "familyName": "Doe"},
    "emails": [{"value": "jdoe@example.com", "type": "work", "primary": True}],
    "active": True,
    "meta": {"resourceType": "User"},
}
GROUP: Dict[str, Any] = {
    "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"],
    "id": "g-1",
    "displayName": "Engineering",
    "members": [{"value": "u-1"}, {"value": "u-2"}],
}


def patch(resource: Dict[str, Any], *operations: Dict[str, Any]) -> Dict[str, Any]:
    read_only = GROUP_READ_ONLY if "members" in resource else USER_READ_ONLY
    return apply_patch(
        resource, {"schemas": [PATCH_OP_URN], "Operations": list(operations)}, read_only
    )


def members(resource: Dict[str, Any]) -> List[str]:
    return list(parse_group(resource).member_ids)


def refused(resource: Dict[str, Any], body: Dict[str, Any]) -> ScimError:
    with pytest.raises(ScimError) as caught:
        apply_patch(resource, body, USER_READ_ONLY)
    assert caught.value.status == 400
    return caught.value


class TestOkta:
    """developer.okta.com/docs/api/openapi/okta-scim/guides/scim-20"""

    def test_deactivate_with_a_pathless_replace(self):
        patched = patch(USER, {"op": "replace", "value": {"active": False}})
        assert parse_user(patched).active is False

    def test_group_rename(self):
        patched = patch(GROUP, {"op": "replace", "value": {"displayName": "Eng"}})
        assert parse_group(patched).display_name == "Eng"

    def test_add_and_remove_members(self):
        added = patch(
            GROUP,
            {
                "op": "add",
                "path": "members",
                "value": [{"value": "u-3", "display": "c@example.com"}],
            },
        )
        assert members(added) == ["u-1", "u-2", "u-3"]
        removed = patch(added, {"op": "remove", "path": 'members[value eq "u-2"]'})
        assert members(removed) == ["u-1", "u-3"]

    def test_adding_a_member_twice_keeps_one(self):
        added = patch(
            GROUP, {"op": "add", "path": "members", "value": [{"value": "u-1"}]}
        )
        assert members(added) == ["u-1", "u-2"]


class TestEntra:
    """learn.microsoft.com/entra/identity/app-provisioning/use-scim-to-provision-users-and-groups"""

    def test_capitalised_ops_and_string_booleans(self):
        patched = patch(USER, {"op": "Replace", "path": "active", "value": "False"})
        assert parse_user(patched).active is False

    def test_add_a_work_address_no_element_holds(self):
        resource = copy.deepcopy(USER)
        resource.pop("emails")
        patched = patch(
            resource,
            {
                "op": "Add",
                "path": 'emails[type eq "work"].value',
                "value": "jane@example.com",
            },
        )
        assert patched["emails"] == [{"type": "work", "value": "jane@example.com"}]
        assert parse_user(patched).email == "jane@example.com"

    def test_replace_the_work_address(self):
        patched = patch(
            USER,
            {
                "op": "Replace",
                "path": 'emails[type eq "work"].value',
                "value": "new@example.com",
            },
        )
        assert parse_user(patched).email == "new@example.com"

    def test_replace_name_parts_by_path_and_by_dotted_key(self):
        patched = patch(
            USER,
            {"op": "Replace", "path": "name.givenName", "value": "Janet"},
            {"op": "Replace", "value": {"name.familyName": "Roe", "displayName": "JR"}},
        )
        fields = parse_user(patched)
        assert (fields.first_name, fields.last_name, fields.display_name) == (
            "Janet",
            "Roe",
            "JR",
        )

    def test_remove_members_by_value(self):
        patched = patch(
            GROUP,
            {"op": "Remove", "path": "members", "value": [{"value": "u-1"}]},
        )
        assert members(patched) == ["u-2"]

    def test_enterprise_extension_attributes_are_accepted(self):
        urn = "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User"
        patched = patch(
            USER,
            {"op": "Add", "path": f"{urn}:department", "value": "R&D"},
            {"op": "Replace", "value": {urn: {"employeeNumber": "7"}}},
        )
        assert patched[urn] == {"department": "R&D", "employeeNumber": "7"}
        assert parse_user(patched).username == "jdoe"


class TestSemantics:
    def test_the_original_is_untouched(self):
        before = copy.deepcopy(USER)
        patch(USER, {"op": "replace", "path": "userName", "value": "other"})
        assert USER == before

    def test_replace_of_a_complex_attribute_keeps_unnamed_parts(self):
        patched = patch(
            USER, {"op": "replace", "path": "name", "value": {"givenName": "J"}}
        )
        assert patched["name"] == {"givenName": "J", "familyName": "Doe"}

    def test_remove_an_attribute(self):
        patched = patch(USER, {"op": "remove", "path": "name.familyName"})
        assert patched["name"] == {"givenName": "Jane"}
        assert (
            parse_user(patch(USER, {"op": "remove", "path": "name"})).first_name is None
        )

    def test_replacing_all_members(self):
        patched = patch(
            GROUP, {"op": "replace", "path": "members", "value": [{"value": "u-9"}]}
        )
        assert members(patched) == ["u-9"]

    def test_removing_the_user_name_leaves_an_invalid_user(self):
        patched = patch(USER, {"op": "remove", "path": "userName"})
        with pytest.raises(ScimError) as caught:
            parse_user(patched)
        assert caught.value.scim_type == "invalidValue"


class TestRefusals:
    def body(self, *operations: Any) -> Dict[str, Any]:
        return {"schemas": [PATCH_OP_URN], "Operations": list(operations)}

    def test_read_only_attributes(self):
        for path in ("id", "meta", "groups"):
            error = refused(
                USER, self.body({"op": "replace", "path": path, "value": "x"})
            )
            assert error.scim_type == "mutability"

    def test_the_body_must_be_a_patch_op(self):
        assert refused(USER, {"Operations": []}).scim_type == "invalidSyntax"
        assert refused(USER, self.body()).scim_type == "invalidSyntax"
        assert refused(USER, self.body({"op": "merge"})).scim_type == "invalidSyntax"
        assert refused(USER, self.body("replace")).scim_type == "invalidSyntax"

    def test_remove_needs_a_path(self):
        assert refused(USER, self.body({"op": "remove"})).scim_type == "noTarget"

    def test_a_filter_matching_nothing_it_cannot_create(self):
        error = refused(
            USER,
            self.body(
                {
                    "op": "replace",
                    "path": 'emails[value co "nowhere"].type',
                    "value": "home",
                }
            ),
        )
        assert error.scim_type == "noTarget"

    def test_values_are_required(self):
        error = refused(USER, self.body({"op": "add", "path": "displayName"}))
        assert error.scim_type == "invalidValue"
        error = refused(USER, self.body({"op": "add", "value": "not an object"}))
        assert error.scim_type == "invalidValue"

    def test_a_filter_on_a_single_valued_attribute(self):
        error = refused(
            USER,
            self.body(
                {"op": "replace", "path": 'userName[value eq "x"]', "value": "y"}
            ),
        )
        assert error.scim_type == "invalidValue"
