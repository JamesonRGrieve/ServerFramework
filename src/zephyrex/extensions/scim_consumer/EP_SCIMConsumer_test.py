# SPDX-License-Identifier: AGPL-3.0-or-later
"""The SCIM 2.0 service over HTTP, against a real app: an identity
provider's requests (Okta- and Entra-style), token refusals, one
connection's isolation from another, conditional requests and the
provisioning log."""

import json
import os
import uuid
from typing import Any, Dict, List, Optional

import pytest

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.scim_consumer.EXT_SCIMConsumer import EXT_SCIMConsumer
from zephyrex.extensions.scim_consumer.SCIMPatch import PATCH_OP_URN
from zephyrex.lib.Environment import env, refresh_settings
from zephyrex.logic.BLL_Auth import TeamModel, UserManager, UserModel, UserTeamModel
from zephyrex.pydantic2.registry import ModelRegistry
from zephyrex.testing.factories import create_user, current_if_match

# Pin the JWT audience/issuer before any token is minted (see
# conversations/EP_Conversations_test.py for why).
os.environ["JWT_AUDIENCE"] = "test-aud"
os.environ["JWT_ISSUER"] = "test-iss"
refresh_settings()

SCIM = "/v1/scim/v2"
CONNECTIONS = "/v1/scim/connection"
USER_URN = "urn:ietf:params:scim:schemas:core:2.0:User"
GROUP_URN = "urn:ietf:params:scim:schemas:core:2.0:Group"
ERROR_URN = "urn:ietf:params:scim:api:messages:2.0:Error"


def bearer(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def unique(prefix: str = "u") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def user_body(user_name: str, **extra: Any) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "schemas": [USER_URN],
        "userName": user_name,
        "name": {"givenName": "Ada", "familyName": "Lovelace"},
        "emails": [
            {"value": f"{user_name}@example.com", "type": "work", "primary": True}
        ],
        "active": True,
    }
    body.update(extra)
    return body


def patch_body(*operations: Dict[str, Any]) -> Dict[str, Any]:
    return {"schemas": [PATCH_OP_URN], "Operations": list(operations)}


def scim_error(response: Any, status: int, scim_type: Optional[str] = None) -> None:
    assert response.status_code == status, response.text
    body = response.json()
    assert body["schemas"] == [ERROR_URN]
    assert body["status"] == str(status)
    if scim_type:
        assert body["scimType"] == scim_type


def etags(response: Any) -> List[str]:
    found: List[str] = response.headers.get_list("etag")
    return found


class ScimServer(ExtensionServerMixin):
    extension_class = EXT_SCIMConsumer

    @pytest.fixture(scope="module")
    def registry(self, server) -> Any:
        return server.app.state.model_registry

    @pytest.fixture(scope="module")
    def root(self, server, registry) -> Dict[str, str]:
        token = UserManager.generate_jwt_token(
            user_id=env("ROOT_ID"), email="root@example.com", model_registry=registry
        )
        return {"Authorization": f"Bearer {token}"}

    def register(self, server, root, **settings: Any) -> Dict[str, Any]:
        response = server.post(
            f"{CONNECTIONS}/register",
            json={"name": unique("idp"), **settings},
            headers=root,
        )
        assert response.status_code == 200, response.text
        issued: Dict[str, Any] = response.json()
        return issued

    @pytest.fixture(scope="module")
    def okta(self, server, root) -> Dict[str, Any]:
        return self.register(server, root)

    @pytest.fixture(scope="module")
    def entra(self, server, root) -> Dict[str, Any]:
        return self.register(server, root, link_existing_users=True)

    def provision(self, server, connection, user_name: Optional[str] = None, **extra):
        response = server.post(
            f"{SCIM}/Users",
            json=user_body(user_name or unique(), **extra),
            headers=bearer(connection["token"]),
        )
        assert response.status_code == 201, response.text
        created: Dict[str, Any] = response.json()
        return created

    def account(self, registry, user_id: str) -> Any:
        UserDB = UserModel.DB(registry.DB.manager.Base)
        found = UserDB.list(
            requester_id=env("ROOT_ID"),
            model_registry=registry,
            filters=[UserDB.id == user_id, UserDB.deleted_at.is_(None)],
            return_type="dict",
        )
        return found[0] if found else None


class TestConnections(ScimServer):
    def test_only_root_registers(self, server, admin_a):
        response = server.post(
            f"{CONNECTIONS}/register",
            json={"name": "sneaky"},
            headers=bearer(admin_a.jwt),
        )
        assert response.status_code == 403, response.text

    def test_the_token_is_shown_once_and_stored_hashed(
        self, server, root, registry, okta
    ):
        assert okta["token"].startswith("scim_")
        assert okta["base_path"] == SCIM
        listed = server.get(CONNECTIONS, headers=root)
        assert listed.status_code == 200, listed.text
        assert okta["token"] not in listed.text
        assert "token_hash" not in listed.text
        from zephyrex.extensions.scim_consumer.BLL_SCIMConsumer import (
            ScimConnectionModel,
            token_digest,
        )

        ConnectionDB = ScimConnectionModel.DB(registry.DB.manager.Base)
        rows = ConnectionDB.list(
            requester_id=env("ROOT_ID"),
            model_registry=registry,
            filters=[ConnectionDB.id == okta["id"]],
            return_type="db",
        )
        assert rows[0].token_hash == token_digest(okta["token"])

    def test_other_users_cannot_see_or_change_connections(
        self, server, admin_a, okta, root
    ):
        listed = server.get(CONNECTIONS, headers=bearer(admin_a.jwt))
        assert okta["id"] not in listed.text
        # They name the connection's current version: the refusal is for who
        # they are, not for a missing one.
        path = f"{CONNECTIONS}/{okta['id']}"
        changed = server.put(
            path,
            json={"scim_connection": {"is_enabled": False}},
            headers={**bearer(admin_a.jwt), **current_if_match(server, path, root)},
        )
        assert changed.status_code in (403, 404), changed.text
        rotated = server.post(
            f"{CONNECTIONS}/{okta['id']}/rotate", json={}, headers=bearer(admin_a.jwt)
        )
        assert rotated.status_code == 403, rotated.text


class TestAuthentication(ScimServer):
    def test_no_token(self, server):
        response = server.get(f"{SCIM}/Users")
        scim_error(response, 401)
        assert response.headers["www-authenticate"].startswith("Bearer")

    def test_wrong_tokens(self, server, admin_a, okta):
        for headers in (
            bearer("scim_" + "x" * 43),
            bearer(okta["token"] + "x"),
            {"Authorization": f"Basic {okta['token']}"},
            bearer(admin_a.jwt),
        ):
            scim_error(server.get(f"{SCIM}/Users", headers=headers), 401)

    def test_a_rotated_token_is_revoked(self, server, root):
        connection = self.register(server, root)
        rotated = server.post(
            f"{CONNECTIONS}/{connection['id']}/rotate", json={}, headers=root
        )
        assert rotated.status_code == 200, rotated.text
        scim_error(
            server.get(f"{SCIM}/Users", headers=bearer(connection["token"])), 401
        )
        fresh = server.get(f"{SCIM}/Users", headers=bearer(rotated.json()["token"]))
        assert fresh.status_code == 200, fresh.text

    def test_a_disabled_or_deleted_connection_is_refused(self, server, root):
        connection = self.register(server, root)
        headers = bearer(connection["token"])
        assert server.get(f"{SCIM}/Users", headers=headers).status_code == 200
        path = f"{CONNECTIONS}/{connection['id']}"
        disabled = server.put(
            path,
            json={"scim_connection": {"is_enabled": False}},
            headers={**root, **current_if_match(server, path, root)},
        )
        assert disabled.status_code == 200, disabled.text
        scim_error(server.get(f"{SCIM}/Users", headers=headers), 401)

        other = self.register(server, root)
        other_path = f"{CONNECTIONS}/{other['id']}"
        deleted = server.delete(
            other_path, headers={**root, **current_if_match(server, other_path, root)}
        )
        assert deleted.status_code == 204, deleted.text
        scim_error(server.get(f"{SCIM}/Users", headers=bearer(other["token"])), 401)


class TestDiscovery(ScimServer):
    def test_service_provider_config(self, server, okta):
        response = server.get(
            f"{SCIM}/ServiceProviderConfig", headers=bearer(okta["token"])
        )
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("application/scim+json")
        config = response.json()
        assert config["patch"]["supported"] is True
        assert config["etag"]["supported"] is True
        assert config["filter"]["supported"] is True
        assert config["bulk"]["supported"] is False
        assert config["authenticationSchemes"][0]["type"] == "oauthbearertoken"

    def test_resource_types_and_schemas(self, server, okta):
        types = server.get(
            f"{SCIM}/ResourceTypes", headers=bearer(okta["token"])
        ).json()
        assert {t["endpoint"] for t in types["Resources"]} == {"/Users", "/Groups"}
        found = server.get(f"{SCIM}/Schemas", headers=bearer(okta["token"])).json()
        assert {s["id"] for s in found["Resources"]} == {USER_URN, GROUP_URN}

    def test_discovery_needs_a_token(self, server):
        for path in ("ServiceProviderConfig", "ResourceTypes", "Schemas"):
            scim_error(server.get(f"{SCIM}/{path}"), 401)

    def test_scim_media_type_requests(self, server, okta):
        headers = {
            **bearer(okta["token"]),
            "Content-Type": "application/scim+json",
            "Accept": "application/scim+json",
        }
        response = server.post(
            f"{SCIM}/Users", content=json.dumps(user_body(unique())), headers=headers
        )
        assert response.status_code == 201, response.text


class TestUsers(ScimServer):
    def test_create(self, server, registry, okta):
        user_name = unique()
        response = server.post(
            f"{SCIM}/Users",
            json=user_body(
                user_name, externalId="00u-okta-1", timezone="America/Toronto"
            ),
            headers=bearer(okta["token"]),
        )
        assert response.status_code == 201, response.text
        created = response.json()
        assert created["userName"] == user_name
        assert created["externalId"] == "00u-okta-1"
        assert created["active"] is True
        assert response.headers["location"].endswith(f"{SCIM}/Users/{created['id']}")
        assert created["meta"]["version"] in etags(response)
        account = self.account(registry, created["id"])
        assert account["username"] == user_name
        assert account["email"] == f"{user_name}@example.com"
        assert (account["first_name"], account["last_name"]) == ("Ada", "Lovelace")
        assert account["timezone"] == "America/Toronto"

    def test_get(self, server, okta):
        created = self.provision(server, okta)
        response = server.get(
            f"{SCIM}/Users/{created['id']}", headers=bearer(okta["token"])
        )
        assert response.status_code == 200, response.text
        assert response.json() == created
        assert created["meta"]["version"] in etags(response)

    def test_a_second_push_of_the_same_user_conflicts(self, server, okta):
        created = self.provision(server, okta)
        again = server.post(
            f"{SCIM}/Users",
            json=user_body(created["userName"].upper()),
            headers=bearer(okta["token"]),
        )
        scim_error(again, 409, "uniqueness")

    def test_an_existing_account_is_not_adopted_unless_configured(
        self, server, okta, entra
    ):
        local = create_user(server)
        body = user_body(
            local.username, emails=[{"value": local.email, "primary": True}]
        )
        scim_error(
            server.post(f"{SCIM}/Users", json=body, headers=bearer(okta["token"])),
            409,
            "uniqueness",
        )
        adopted = server.post(
            f"{SCIM}/Users", json=body, headers=bearer(entra["token"])
        )
        assert adopted.status_code == 201, adopted.text
        assert adopted.json()["id"] == local.id

    def test_internal_accounts_are_never_adopted(self, server, registry, entra):
        # The test app's root email ("root@" with no APP_URI) is not an
        # address an identity provider could push, so root is matched by a
        # username given to it here.
        user_name = unique("root")
        UserManager(requester_id=env("ROOT_ID"), model_registry=registry).update(
            id=env("ROOT_ID"), username=user_name
        )
        response = server.post(
            f"{SCIM}/Users", json=user_body(user_name), headers=bearer(entra["token"])
        )
        scim_error(response, 409, "uniqueness")
        assert self.account(registry, env("ROOT_ID"))["username"] == user_name

    def test_a_link_only_connection_creates_no_account(self, server, root):
        connection = self.register(server, root, auto_create_users=False)
        response = server.post(
            f"{SCIM}/Users",
            json=user_body(unique()),
            headers=bearer(connection["token"]),
        )
        scim_error(response, 403)

    def test_invalid_users(self, server, okta):
        headers = bearer(okta["token"])
        scim_error(
            server.post(f"{SCIM}/Users", json={"schemas": [USER_URN]}, headers=headers),
            400,
            "invalidValue",
        )
        scim_error(
            server.post(
                f"{SCIM}/Users",
                json=user_body("x", emails=[{"value": "not-an-address"}]),
                headers=headers,
            ),
            400,
            "invalidValue",
        )

    def test_replace_honours_if_match(self, server, okta):
        created = self.provision(server, okta)
        url = f"{SCIM}/Users/{created['id']}"
        body = user_body(created["userName"], displayName="Countess")
        stale = server.put(
            url, json=body, headers={**bearer(okta["token"]), "If-Match": 'W/"stale"'}
        )
        scim_error(stale, 412)
        fresh = server.put(
            url,
            json=body,
            headers={**bearer(okta["token"]), "If-Match": created["meta"]["version"]},
        )
        assert fresh.status_code == 200, fresh.text
        assert fresh.json()["displayName"] == "Countess"
        assert fresh.json()["meta"]["version"] != created["meta"]["version"]

    def test_if_none_match(self, server, okta):
        created = self.provision(server, okta)
        response = server.get(
            f"{SCIM}/Users/{created['id']}",
            headers={
                **bearer(okta["token"]),
                "If-None-Match": created["meta"]["version"],
            },
        )
        assert response.status_code == 304, response.text

    def test_a_user_name_taken_by_another_account_conflicts(self, server, okta):
        first, second = self.provision(server, okta), self.provision(server, okta)
        response = server.put(
            f"{SCIM}/Users/{second['id']}",
            json=user_body(first["userName"]),
            headers=bearer(okta["token"]),
        )
        scim_error(response, 409, "uniqueness")

    def test_okta_deactivation_signs_the_user_out(self, server, registry, okta):
        created = self.provision(server, okta)
        token = UserManager.generate_jwt_token(
            user_id=created["id"],
            email=created["emails"][0]["value"],
            model_registry=registry,
        )
        assert server.get("/v1/user", headers=bearer(token)).status_code == 200
        response = server.patch(
            f"{SCIM}/Users/{created['id']}",
            json=patch_body({"op": "replace", "value": {"active": False}}),
            headers=bearer(okta["token"]),
        )
        assert response.status_code == 200, response.text
        assert response.json()["active"] is False
        assert self.account(registry, created["id"])["active"] is False
        # Refused: the framework answers an inactive account 401 or 403
        # depending on which check meets it first.
        assert server.get("/v1/user", headers=bearer(token)).status_code in (401, 403)

    def test_entra_patch(self, server, registry, okta):
        created = self.provision(server, okta)
        response = server.patch(
            f"{SCIM}/Users/{created['id']}",
            json=patch_body(
                {"op": "Replace", "path": "displayName", "value": "Ada L."},
                {
                    "op": "Replace",
                    "path": 'emails[type eq "work"].value',
                    "value": f"moved-{created['userName']}@example.com",
                },
                {"op": "Add", "path": "name.givenName", "value": "Augusta"},
                {"op": "Replace", "path": "active", "value": "False"},
            ),
            headers=bearer(okta["token"]),
        )
        assert response.status_code == 200, response.text
        account = self.account(registry, created["id"])
        assert account["display_name"] == "Ada L."
        assert account["email"] == f"moved-{created['userName']}@example.com"
        assert account["first_name"] == "Augusta"
        assert account["active"] is False

    def test_patch_cannot_touch_read_only_attributes(self, server, okta):
        created = self.provision(server, okta)
        response = server.patch(
            f"{SCIM}/Users/{created['id']}",
            json=patch_body({"op": "replace", "path": "id", "value": "mine"}),
            headers=bearer(okta["token"]),
        )
        scim_error(response, 400, "mutability")

    def test_delete_deactivates_and_a_later_push_relinks(self, server, registry, okta):
        created = self.provision(server, okta)
        url = f"{SCIM}/Users/{created['id']}"
        deleted = server.delete(url, headers=bearer(okta["token"]))
        assert deleted.status_code == 204, deleted.text
        assert deleted.content == b""
        scim_error(server.get(url, headers=bearer(okta["token"])), 404)
        assert self.account(registry, created["id"])["active"] is False
        back = server.post(
            f"{SCIM}/Users",
            json=user_body(created["userName"]),
            headers=bearer(okta["token"]),
        )
        assert back.status_code == 201, back.text
        assert back.json()["id"] == created["id"]
        assert back.json()["active"] is True

    def test_delete_on_deprovision_deletes_the_account(self, server, registry, root):
        connection = self.register(server, root, delete_on_deprovision=True)
        created = self.provision(server, connection)
        deleted = server.delete(
            f"{SCIM}/Users/{created['id']}", headers=bearer(connection["token"])
        )
        assert deleted.status_code == 204, deleted.text
        assert self.account(registry, created["id"]) is None


class TestIsolation(ScimServer):
    def test_a_connection_sees_only_its_own(self, server, root, okta):
        other = self.register(server, root)
        mine = self.provision(server, okta)
        theirs = bearer(other["token"])
        url = f"{SCIM}/Users/{mine['id']}"
        scim_error(server.get(url, headers=theirs), 404)
        scim_error(server.put(url, json=user_body("hijack"), headers=theirs), 404)
        scim_error(
            server.patch(
                url,
                json=patch_body({"op": "replace", "path": "active", "value": False}),
                headers=theirs,
            ),
            404,
        )
        scim_error(server.delete(url, headers=theirs), 404)
        listed = server.get(f"{SCIM}/Users", headers=theirs).json()
        assert mine["id"] not in [r["id"] for r in listed["Resources"]]
        group = server.post(
            f"{SCIM}/Groups",
            json={"displayName": unique("g"), "members": [{"value": mine["id"]}]},
            headers=theirs,
        )
        scim_error(group, 400, "invalidValue")

    def test_local_users_are_not_listed(self, server, admin_a, okta):
        listed = server.get(
            f"{SCIM}/Users",
            params={"filter": f'id eq "{admin_a.id}"'},
            headers=bearer(okta["token"]),
        ).json()
        assert listed["totalResults"] == 0
        scim_error(
            server.get(f"{SCIM}/Users/{admin_a.id}", headers=bearer(okta["token"])), 404
        )


class TestListing(ScimServer):
    @pytest.fixture(scope="class")
    def batch(self, server, okta) -> str:
        prefix = unique("page")
        for index in range(5):
            self.provision(server, okta, f"{prefix}-{index}")
        return prefix

    def list(self, server, okta, **params: Any) -> Dict[str, Any]:
        response = server.get(
            f"{SCIM}/Users", params=params, headers=bearer(okta["token"])
        )
        assert response.status_code == 200, response.text
        listed: Dict[str, Any] = response.json()
        return listed

    def test_filter_and_pagination(self, server, okta, batch):
        page = self.list(
            server,
            okta,
            filter=f'userName sw "{batch}"',
            sortBy="userName",
            startIndex=3,
            count=2,
        )
        assert page["totalResults"] == 5
        assert page["startIndex"] == 3
        assert page["itemsPerPage"] == 2
        assert [r["userName"] for r in page["Resources"]] == [
            f"{batch}-2",
            f"{batch}-3",
        ]
        last = self.list(
            server, okta, filter=f'userName sw "{batch}"', startIndex=5, count=10
        )
        assert last["itemsPerPage"] == 1
        empty = self.list(server, okta, filter=f'userName sw "{batch}"', count=0)
        assert (empty["totalResults"], empty["Resources"]) == (5, [])

    def test_sort_descending(self, server, okta, batch):
        page = self.list(
            server,
            okta,
            filter=f'userName sw "{batch}"',
            sortBy="userName",
            sortOrder="descending",
        )
        assert [r["userName"] for r in page["Resources"]][:2] == [
            f"{batch}-4",
            f"{batch}-3",
        ]

    def test_the_lookup_identity_providers_make(self, server, okta, batch):
        page = self.list(server, okta, filter=f'userName eq "{batch.upper()}-1"')
        assert [r["userName"] for r in page["Resources"]] == [f"{batch}-1"]

    def test_quoted_values_and_injection_attempts(self, server, okta, batch):
        quoted = self.provision(
            server, okta, f'o"{batch}', emails=[{"value": f"o-{batch}@example.com"}]
        )
        page = self.list(server, okta, filter=f'userName eq "o\\"{batch}"')
        assert [r["id"] for r in page["Resources"]] == [quoted["id"]]
        for hostile in (
            f'userName eq "x\\" or userName sw \\"{batch}"',
            "userName eq \"' OR '1'='1\"",
            'userName co "%"',
        ):
            assert self.list(server, okta, filter=hostile)["totalResults"] == 0

    def test_malformed_filters(self, server, okta):
        for broken in (
            'userName eq "x',
            "userName eq",
            'userName eq "x" or 1=1',
            "(((",
        ):
            response = server.get(
                f"{SCIM}/Users",
                params={"filter": broken},
                headers=bearer(okta["token"]),
            )
            scim_error(response, 400, "invalidFilter")

    def test_attribute_selection(self, server, okta, batch):
        page = self.list(
            server, okta, filter=f'userName eq "{batch}-0"', attributes="userName"
        )
        assert set(page["Resources"][0]) == {"schemas", "id", "meta", "userName"}


class TestGroups(ScimServer):
    def members(self, registry, team_id: str) -> List[str]:
        MemberDB = UserTeamModel.DB(registry.DB.manager.Base)
        rows = MemberDB.list(
            requester_id=env("ROOT_ID"),
            model_registry=registry,
            filters=[MemberDB.team_id == team_id, MemberDB.deleted_at.is_(None)],
            return_type="dict",
        )
        return sorted(row["user_id"] for row in rows)

    def test_group_lifecycle(self, server, registry, okta):
        headers = bearer(okta["token"])
        ada, bob, cy = (self.provision(server, okta) for _ in range(3))
        created = server.post(
            f"{SCIM}/Groups",
            json={
                "schemas": [GROUP_URN],
                "displayName": unique("Engineering"),
                "externalId": "grp-1",
                "members": [{"value": ada["id"]}, {"value": bob["id"]}],
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        group = created.json()
        team_id = group["id"]
        assert self.members(registry, team_id) == sorted([ada["id"], bob["id"]])
        user = server.get(f"{SCIM}/Users/{ada['id']}", headers=headers).json()
        assert [g["value"] for g in user["groups"]] == [team_id]

        # Entra: add and remove members by value.
        patched = server.patch(
            f"{SCIM}/Groups/{team_id}",
            json=patch_body(
                {"op": "Add", "path": "members", "value": [{"value": cy["id"]}]},
                {"op": "Remove", "path": "members", "value": [{"value": ada["id"]}]},
            ),
            headers=headers,
        )
        assert patched.status_code == 200, patched.text
        assert self.members(registry, team_id) == sorted([bob["id"], cy["id"]])

        # Okta: rename, and remove a member by filter.
        renamed = server.patch(
            f"{SCIM}/Groups/{team_id}",
            json=patch_body(
                {"op": "replace", "value": {"displayName": group["displayName"] + "!"}},
                {"op": "remove", "path": f'members[value eq "{bob["id"]}"]'},
            ),
            headers=headers,
        )
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["displayName"] == group["displayName"] + "!"
        assert self.members(registry, team_id) == [cy["id"]]

        TeamDB = TeamModel.DB(registry.DB.manager.Base)
        team = TeamDB.list(
            requester_id=env("ROOT_ID"),
            model_registry=registry,
            filters=[TeamDB.id == team_id, TeamDB.deleted_at.is_(None)],
            return_type="dict",
        )
        assert team[0]["name"] == group["displayName"] + "!"

        listed = server.get(
            f"{SCIM}/Groups",
            params={"filter": 'externalId eq "grp-1"', "excludedAttributes": "members"},
            headers=headers,
        ).json()
        assert [g["id"] for g in listed["Resources"]] == [team_id]
        assert "members" not in listed["Resources"][0]

        deleted = server.delete(f"{SCIM}/Groups/{team_id}", headers=headers)
        assert deleted.status_code == 204, deleted.text
        scim_error(server.get(f"{SCIM}/Groups/{team_id}", headers=headers), 404)
        assert (
            TeamDB.list(
                requester_id=env("ROOT_ID"),
                model_registry=registry,
                filters=[TeamDB.id == team_id, TeamDB.deleted_at.is_(None)],
                return_type="dict",
            )
            == []
        )

    def test_members_added_locally_are_left_alone(
        self, server, registry, okta, admin_a
    ):
        headers = bearer(okta["token"])
        ada = self.provision(server, okta)
        group = server.post(
            f"{SCIM}/Groups",
            json={"displayName": unique("g"), "members": [{"value": ada["id"]}]},
            headers=headers,
        ).json()
        from zephyrex.logic.BLL_Auth import UserTeamManager

        UserTeamManager(requester_id=env("ROOT_ID"), model_registry=registry).create(
            team_id=group["id"], user_id=admin_a.id, role_id=env("USER_ROLE_ID")
        )
        replaced = server.put(
            f"{SCIM}/Groups/{group['id']}",
            json={"displayName": group["displayName"], "members": []},
            headers=headers,
        )
        assert replaced.status_code == 200, replaced.text
        assert replaced.json()["members"] == []
        assert self.members(registry, group["id"]) == [admin_a.id]

    def test_duplicate_group_names_conflict(self, server, okta):
        headers = bearer(okta["token"])
        name = unique("g")
        first = server.post(
            f"{SCIM}/Groups", json={"displayName": name}, headers=headers
        )
        assert first.status_code == 201, first.text
        again = server.post(
            f"{SCIM}/Groups", json={"displayName": name.upper()}, headers=headers
        )
        scim_error(again, 409, "uniqueness")


class TestLog(ScimServer):
    def test_changes_are_logged(self, server, root, okta):
        created = self.provision(server, okta)
        server.post(
            f"{SCIM}/Users",
            json=user_body(created["userName"]),
            headers=bearer(okta["token"]),
        )
        response = server.get("/v1/scim/log", headers=root)
        assert response.status_code == 200, response.text
        entries = [
            e
            for e in response.json()["scim_provisioning_logs"]
            if e["scim_connection_id"] == okta["id"]
        ]
        statuses = {(e["operation"], e["http_status"], e["status"]) for e in entries}
        assert ("create", 201, "success") in statuses
        assert ("create", 409, "error") in statuses


class TestAbilities(ScimServer):
    @pytest.fixture(autouse=True)
    def attached(self, registry, monkeypatch) -> None:
        monkeypatch.setattr(
            ModelRegistry, "attached", classmethod(lambda cls: registry)
        )

    async def test_connections_and_log(self, server, okta):
        self.provision(server, okta)
        connections = await EXT_SCIMConsumer.list_scim_connections(env("ROOT_ID"))
        assert okta["id"] in {c["id"] for c in connections}
        assert all("token_hash" not in c for c in connections)
        log = await EXT_SCIMConsumer.scim_provisioning_log(
            env("ROOT_ID"), okta["id"], 5
        )
        assert log and log[0]["operation"] == "create"

    async def test_others_see_no_connections(self, admin_a, okta):
        connections = await EXT_SCIMConsumer.list_scim_connections(admin_a.id)
        assert okta["id"] not in {c["id"] for c in connections}
