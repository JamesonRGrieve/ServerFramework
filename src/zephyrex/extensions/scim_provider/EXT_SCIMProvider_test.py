# SPDX-License-Identifier: AGPL-3.0-or-later
"""SCIM provisioning out of the framework, against a real SCIM service
provider on loopback (``SCIMTestServer``) that refuses anything the
protocol does not allow.

Covered: filter literals quoted as JSON strings (an injected ``"`` cannot
widen a lookup to another account), safe resource paths, remote change
detection; a new user looked up then created with a spec-correct
request; updates PATCHed with If-Match, or PUT without either when the
target says it supports neither; an unchanged user not rewritten; a 412
reread and retried, a 404 recreated, a 409 linked to the existing user of
that userName; a 429 waited out per Retry-After and a long one surfaced;
refused tokens (401 and 403) as AuthExternalError; the SSRF guard and a
missing setting; removed users deactivated or (when configured) deleted;
teams as groups whose membership changes are add/remove operations; a
team scope; and a reconcile that pages through the target and repairs
drift."""

import uuid
from typing import Any, Callable, Dict, Iterator, List, Optional

import pytest

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.ExternalErrors import (
    AuthExternalError,
    InvalidInputExternalError,
    PermanentExternalError,
    RateLimitExternalError,
)
from zephyrex.extensions.scim_provider.BLL_SCIMProvider import (
    ScimLinkManager,
    ScimSyncLogManager,
)
from zephyrex.extensions.scim_provider.EXT_SCIMProvider import EXT_SCIMProvider
from zephyrex.extensions.scim_provider.PRV_SCIM import (
    PRV_SCIM_SCIMProvider,
    RemoteResource,
    ServiceProviderConfig,
    equality_filter,
    resource_path,
    scim_string,
)
from zephyrex.extensions.scim_provider.SCIMSync import (
    RemoteIndex,
    group_differs,
    live,
    mark_pending,
    member_ids,
    user_differs,
)
from zephyrex.extensions.scim_provider.SCIMTestServer import (
    SCIMServiceProvider,
    parse_filter,
)
from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Auth import (
    TeamManager,
    UserCredentialManager,
    UserManager,
    UserModel,
    UserTeamManager,
)
from zephyrex.logic.BLL_Providers import (
    ProviderInstanceManager,
    ProviderInstanceModel,
    ProviderInstanceSettingManager,
    ProviderManager,
)

TOKEN = "scim-test-bearer-token"
PASSWORD = "testpassword1"
ROOT = env("ROOT_ID")


class Live(ExtensionServerMixin):
    """Tests against the extension's app (with the core companions its
    permission checks read)."""

    extension_class = EXT_SCIMProvider

    @pytest.fixture
    def registry(self, server: Any) -> Any:
        return server.app.state.model_registry

    @pytest.fixture
    def provider_instance(self, registry: Any) -> Callable[..., ProviderInstanceModel]:
        """A root SCIM target with settings, made as the conftest's
        ``provider_instance`` makes one, in this app."""

        def _create(
            provider_cls: Any,
            *,
            api_key: Optional[str] = None,
            settings: Optional[Dict[str, str]] = None,
        ) -> ProviderInstanceModel:
            provider = ProviderManager(model_registry=registry, requester_id=ROOT).get(
                name=provider_cls.name
            )
            instance = ProviderInstanceModel.model_validate(
                ProviderInstanceManager(
                    model_registry=registry, requester_id=ROOT
                ).create(
                    name=f"{provider_cls.name}_{uuid.uuid4().hex}",
                    provider_id=provider.id,
                    api_key=api_key,
                    scope="root",
                ),
                from_attributes=True,
            )
            values = ProviderInstanceSettingManager(
                model_registry=registry, requester_id=ROOT
            )
            for key, value in (settings or {}).items():
                values.create(provider_instance_id=instance.id, key=key, value=value)
            return instance

        return _create


@pytest.fixture
def scim_server(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Callable[..., SCIMServiceProvider]]:
    """Start real SCIM service providers, allowed egress for this test."""
    started: List[Any] = []
    hosts: List[str] = []

    def _start(**options: Any) -> SCIMServiceProvider:
        provider = SCIMServiceProvider(TOKEN, **options)
        started.append(provider.serve())
        hosts.append(provider.host)
        monkeypatch.setenv("EGRESS_ALLOWED_HOSTS", ",".join(hosts))
        return provider

    yield _start
    for server in started:
        server.shutdown()
        server.server_close()


@pytest.fixture
def target(provider_instance: Any) -> Callable[..., ProviderInstanceModel]:
    def _make(
        provider: SCIMServiceProvider, token: str = TOKEN, **settings: str
    ) -> ProviderInstanceModel:
        instance: ProviderInstanceModel = provider_instance(
            PRV_SCIM_SCIMProvider,
            api_key=token,
            settings={"base_url": provider.base_url, **settings},
        )
        return instance

    return _make


def local_user(registry: Any, username: str = "", first: str = "Ada") -> Any:
    """A user as registration makes one: the row, then its credentials."""
    email = f"scim_{uuid.uuid4().hex[:12]}@example.com"
    user = UserModel.DB(registry.DB.manager.Base).create(
        requester_id=env("SYSTEM_ID"),
        model_registry=registry,
        return_type="dto",
        override_dto=UserModel,
        email=email,
        username=username or email.split("@")[0],
        first_name=first,
        last_name="Lovelace",
    )
    UserCredentialManager(requester_id=user.id, model_registry=registry).create(
        user_id=user.id, password=PASSWORD
    )
    return user


def link(registry: Any, instance: ProviderInstanceModel, local_id: str) -> Any:
    found = live(
        ScimLinkManager(model_registry=registry, requester_id=ROOT),
        provider_instance_id=instance.id,
        local_id=local_id,
    )
    assert len(found) == 1
    return found[0]


def logged(registry: Any, instance: ProviderInstanceModel) -> List[Any]:
    rows = ScimSyncLogManager(model_registry=registry, requester_id=ROOT).list(
        provider_instance_id=instance.id
    )
    return sorted(rows, key=lambda row: row.created_at)


def team_with(registry: Any, owner: Any, *members: Any) -> Any:
    team = TeamManager(model_registry=registry, requester_id=owner.id).create(
        name=f"Team {uuid.uuid4().hex[:8]}"
    )
    memberships = UserTeamManager(model_registry=registry, requester_id=ROOT)
    for member in members:
        memberships.create(
            team_id=team.id, user_id=member.id, role_id=env("USER_ROLE_ID")
        )
    return team


def membership(registry: Any, team: Any, user: Any) -> Any:
    found = UserTeamManager(model_registry=registry, requester_id=ROOT).list(
        team_id=team.id, user_id=user.id
    )
    return found[0]


def remote(provider: SCIMServiceProvider, user: Any) -> Dict[str, Any]:
    [found] = provider.by_name("Users", user.email)
    return found


class TestProtocolPieces:
    def test_a_filter_literal_is_a_json_string(self):
        assert scim_string('a"b\\c') == '"a\\"b\\\\c"'
        assert equality_filter("userName", 'x" or userName pr "') == (
            'userName eq "x\\" or userName pr \\""'
        )
        # The quoted literal is one token to a real filter parser.
        predicate = parse_filter(equality_filter("userName", 'x" or userName pr "'))
        assert not predicate({"userName": "someone"})
        assert predicate({"userName": 'x" or userName pr "'})

    def test_an_id_is_one_path_segment(self):
        assert resource_path("User") == "Users"
        assert resource_path("Group", "a/b?c") == "Groups/a%2Fb%3Fc"
        for bad in ("", ".", ".."):
            with pytest.raises(InvalidInputExternalError):
                resource_path("User", bad)

    def test_page_size_follows_the_targets_limit(self):
        assert ServiceProviderConfig().page_size == 100
        limited = ServiceProviderConfig.model_validate(
            {"filter": {"supported": True, "maxResults": 25}}
        )
        assert limited.page_size == 25 and not limited.etag.supported

    def test_what_counts_as_a_difference(self):
        desired = {
            "userName": "Ada@Example.com",
            "externalId": "1",
            "displayName": "Ada",
            "active": True,
            "name": {"givenName": "Ada", "familyName": "L"},
            "emails": [{"value": "ada@example.com", "primary": True}],
        }
        same = {**desired, "userName": "ada@example.com", "meta": {"version": "1"}}
        assert not user_differs(desired, same)
        assert user_differs(desired, {**same, "active": False})
        assert user_differs(desired, {**same, "displayName": "Someone"})
        group = {"displayName": "Ops", "externalId": "t", "members": [{"value": "u1"}]}
        assert not group_differs(group, {**group, "members": [{"value": "u1"}]})
        assert group_differs(group, {**group, "members": []})
        assert not group_differs(group, {"displayName": "Ops", "externalId": "t"})
        assert member_ids({"displayName": "x"}) is None

    def test_a_remote_index_matches_by_external_id_then_name(self):
        ours = RemoteResource("r1", None, {"userName": "a@x", "externalId": "local-1"})
        theirs = RemoteResource("r2", None, {"userName": "B@x"})
        index = RemoteIndex.of("userName", [ours, theirs])
        assert index.match("local-1", "other@x") is ours
        assert index.match("local-2", "b@X") is theirs
        assert index.match("local-3", "c@x") is None


class TestUsers(Live):
    async def test_a_new_user_is_looked_up_then_created(
        self, registry, scim_server, target
    ):
        provider = scim_server()
        instance = target(provider)
        user = local_user(registry)
        assert link(registry, instance, user.id).pending

        summary = await EXT_SCIMProvider.push(registry, instance.id)

        assert summary["users"]["created"] == 1
        created = remote(provider, user)
        assert created["externalId"] == user.id and created["active"] is True
        assert created["name"] == {
            "givenName": "Ada",
            "familyName": "Lovelace",
            "formatted": "Ada Lovelace",
        }
        assert created["emails"] == [
            {"value": user.email, "type": "work", "primary": True}
        ]
        assert provider.requests[0].path.endswith("/ServiceProviderConfig")
        lookup = provider.requests[1]
        assert lookup.method == "GET" and lookup.path.endswith("/Users")
        assert lookup.query["filter"] == f'userName eq "{user.email}"'
        [post] = provider.writes()
        assert post.method == "POST"
        assert post.headers["content-type"] == "application/scim+json"
        assert post.headers["authorization"] == f"Bearer {TOKEN}"
        linked = link(registry, instance, user.id)
        assert linked.remote_id == created["id"] and not linked.pending
        assert linked.remote_etag == created["meta"]["version"]
        assert linked.status == "synced"
        assert [(r.operation, r.status) for r in logged(registry, instance)] == [
            ("create", "success")
        ]
        assert provider.violations == []

    async def test_an_update_is_a_patch_with_if_match(
        self, registry, scim_server, target
    ):
        provider = scim_server()
        instance = target(provider)
        user = local_user(registry)
        await EXT_SCIMProvider.push(registry, instance.id)
        version = link(registry, instance, user.id).remote_etag

        UserManager(model_registry=registry, requester_id=ROOT).update(
            user.id, first_name="Augusta"
        )
        assert link(registry, instance, user.id).pending
        summary = await EXT_SCIMProvider.push(registry, instance.id)

        assert summary["users"]["updated"] == 1
        patch = provider.writes()[-1]
        assert patch.method == "PATCH" and patch.headers["if-match"] == version
        assert patch.body["schemas"] == [
            "urn:ietf:params:scim:api:messages:2.0:PatchOp"
        ]
        [operation] = patch.body["Operations"]
        assert operation["op"] == "replace" and "path" not in operation
        assert operation["value"]["name"]["givenName"] == "Augusta"
        assert remote(provider, user)["name"]["givenName"] == "Augusta"
        assert link(registry, instance, user.id).remote_etag != version
        assert provider.violations == []

    async def test_put_without_if_match_when_the_target_supports_neither(
        self, registry, scim_server, target
    ):
        provider = scim_server(patch=False, etag=False)
        instance = target(provider)
        user = local_user(registry)
        await EXT_SCIMProvider.push(registry, instance.id)
        UserManager(model_registry=registry, requester_id=ROOT).update(
            user.id, last_name="Byron"
        )
        await EXT_SCIMProvider.push(registry, instance.id)

        put = provider.writes()[-1]
        assert put.method == "PUT" and "if-match" not in put.headers
        assert put.body["id"] == remote(provider, user)["id"]
        assert remote(provider, user)["name"]["familyName"] == "Byron"
        assert provider.violations == []

    async def test_an_unchanged_user_is_not_rewritten(
        self, registry, scim_server, target
    ):
        provider = scim_server()
        instance = target(provider)
        user = local_user(registry)
        await EXT_SCIMProvider.push(registry, instance.id)
        mark_pending(registry, "User", [user.id])

        summary = await EXT_SCIMProvider.push(registry, instance.id)

        assert summary["users"]["unchanged"] == 1
        assert [w.method for w in provider.writes()] == ["POST"]
        assert not link(registry, instance, user.id).pending

    async def test_a_patch_answered_with_no_content_keeps_the_new_version(
        self, registry, scim_server, target
    ):
        provider = scim_server(patch_answers_no_content=True)
        instance = target(provider)
        user = local_user(registry)
        await EXT_SCIMProvider.push(registry, instance.id)
        for first in ("Grace", "Hedy"):
            UserManager(model_registry=registry, requester_id=ROOT).update(
                user.id, first_name=first
            )
            await EXT_SCIMProvider.push(registry, instance.id)

        assert remote(provider, user)["name"]["givenName"] == "Hedy"
        assert [w.method for w in provider.writes()] == ["POST", "PATCH", "PATCH"]
        assert provider.violations == []

    async def test_a_stale_version_is_reread_and_retried(
        self, registry, scim_server, target
    ):
        provider = scim_server()
        instance = target(provider)
        user = local_user(registry)
        await EXT_SCIMProvider.push(registry, instance.id)
        stale = link(registry, instance, user.id).remote_etag
        provider.edit("Users", remote(provider, user)["id"], title="Countess")
        fresh = remote(provider, user)["meta"]["version"]

        UserManager(model_registry=registry, requester_id=ROOT).update(
            user.id, first_name="Augusta"
        )
        summary = await EXT_SCIMProvider.push(registry, instance.id)

        assert summary["users"]["updated"] == 1
        patches = [w for w in provider.writes() if w.method == "PATCH"]
        assert [p.headers["if-match"] for p in patches] == [stale, fresh]
        assert remote(provider, user)["name"]["givenName"] == "Augusta"

    async def test_a_user_gone_from_the_target_is_created_again(
        self, registry, scim_server, target
    ):
        provider = scim_server()
        instance = target(provider)
        user = local_user(registry)
        await EXT_SCIMProvider.push(registry, instance.id)
        first_id = remote(provider, user)["id"]
        provider.drop("Users", first_id)

        UserManager(model_registry=registry, requester_id=ROOT).update(
            user.id, first_name="Augusta"
        )
        summary = await EXT_SCIMProvider.push(registry, instance.id)

        assert summary["users"]["created"] == 1
        again = remote(provider, user)
        assert again["id"] != first_id
        assert link(registry, instance, user.id).remote_id == again["id"]

    async def test_an_existing_remote_user_is_linked_not_duplicated(
        self, registry, scim_server, target
    ):
        provider = scim_server()
        instance = target(provider)
        user = local_user(registry)
        existing = provider.seed(
            "Users", userName=user.email.upper(), externalId="okta-1", active=True
        )

        summary = await EXT_SCIMProvider.push(registry, instance.id)

        assert summary["users"]["linked"] == 1
        assert not [w for w in provider.writes() if w.method == "POST"]
        [only] = provider.by_name("Users", user.email)
        assert only["id"] == existing["id"] and only["externalId"] == user.id
        assert [r.operation for r in logged(registry, instance)] == ["link", "patch"]

    async def test_a_409_links_the_user_who_holds_the_user_name(
        self, registry, scim_server, target
    ):
        """The user appears on the target between the lookup and the
        create (another admin, another IdP): the 409 links it."""
        provider = scim_server()
        instance = target(provider)
        user = local_user(registry)
        existing = provider.seed("Users", userName=user.email, active=True)
        provider.fail(
            "GET",
            "Users",
            200,
            body={"schemas": [], "totalResults": 0, "Resources": []},
        )

        summary = await EXT_SCIMProvider.push(registry, instance.id)

        assert summary["users"]["linked"] == 1
        assert len(provider.by_name("Users", user.email)) == 1
        assert link(registry, instance, user.id).remote_id == existing["id"]
        assert [w.method for w in provider.writes()] == ["POST", "PATCH"]

    async def test_an_injected_quote_cannot_widen_the_lookup(
        self, registry, scim_server, target
    ):
        """A username that would close the literal and add ``or userName
        eq "victim"`` stays one literal: the victim's account is not
        linked to (and so not taken over by) this user."""
        provider = scim_server()
        instance = target(provider, user_name="username")
        victim = provider.seed("Users", userName="victim", active=True)
        user = local_user(registry, username='nobody" or userName eq "victim')

        await EXT_SCIMProvider.push(registry, instance.id)

        lookup = next(r for r in provider.requests if "filter" in r.query)
        assert lookup.query["filter"] == (
            'userName eq "nobody\\" or userName eq \\"victim"'
        )
        assert link(registry, instance, user.id).remote_id != victim["id"]
        assert provider.resources["Users"][victim["id"]]["userName"] == "victim"
        assert "externalId" not in provider.resources["Users"][victim["id"]]
        assert provider.violations == []

    async def test_a_removed_user_is_deactivated(self, registry, scim_server, target):
        provider = scim_server()
        instance = target(provider)
        user = local_user(registry)
        await EXT_SCIMProvider.push(registry, instance.id)

        UserManager(model_registry=registry, requester_id=user.id).delete()
        summary = await EXT_SCIMProvider.push(registry, instance.id)

        assert summary["users"]["deactivated"] == 1
        patch = provider.writes()[-1]
        assert patch.body["Operations"] == [
            {"op": "replace", "path": "active", "value": False}
        ]
        assert remote(provider, user)["active"] is False
        assert link(registry, instance, user.id).status == "deactivated"

    async def test_a_removed_user_is_deleted_when_configured(
        self, registry, scim_server, target
    ):
        provider = scim_server()
        instance = target(provider, delete_mode="delete")
        user = local_user(registry)
        await EXT_SCIMProvider.push(registry, instance.id)
        version = remote(provider, user)["meta"]["version"]

        UserManager(model_registry=registry, requester_id=user.id).delete()
        summary = await EXT_SCIMProvider.push(registry, instance.id)

        assert summary["users"]["deleted"] == 1
        delete = provider.writes()[-1]
        assert delete.method == "DELETE" and delete.headers["if-match"] == version
        assert provider.by_name("Users", user.email) == []
        gone = link(registry, instance, user.id)
        assert gone.status == "deleted" and gone.remote_id is None

    async def test_a_deactivated_user_without_patch_is_put_inactive(
        self, registry, scim_server, target
    ):
        provider = scim_server(patch=False)
        instance = target(provider)
        user = local_user(registry)
        await EXT_SCIMProvider.push(registry, instance.id)

        UserManager(model_registry=registry, requester_id=user.id).delete()
        await EXT_SCIMProvider.push(registry, instance.id)

        put = provider.writes()[-1]
        assert put.method == "PUT" and put.body["active"] is False
        assert put.body["userName"] == user.email
        assert remote(provider, user)["active"] is False
        assert provider.violations == []


class TestRefusals(Live):
    async def test_a_429_is_waited_out_per_retry_after(
        self, registry, scim_server, target
    ):
        provider = scim_server()
        instance = target(provider)
        user = local_user(registry)
        provider.fail("POST", "Users", 429, {"Retry-After": "1"})

        summary = await EXT_SCIMProvider.push(registry, instance.id)

        assert summary["users"]["created"] == 1
        first, second = [r for r in provider.requests if r.method == "POST"]
        assert second.at - first.at >= 1.0
        assert remote(provider, user)["externalId"] == user.id

    async def test_a_long_retry_after_is_surfaced_and_left_pending(
        self, registry, scim_server, target
    ):
        provider = scim_server()
        instance = target(provider)
        user = local_user(registry)
        provider.fail("POST", "Users", 429, {"Retry-After": "3600"})

        with pytest.raises(RateLimitExternalError) as raised:
            await EXT_SCIMProvider.push(registry, instance.id)

        assert raised.value.retry_after_seconds == 3600
        waiting = link(registry, instance, user.id)
        assert waiting.pending and waiting.status == "error"
        [entry] = logged(registry, instance)
        assert (entry.operation, entry.status) == ("push", "error")

    async def test_a_refused_token_is_an_auth_error(
        self, registry, scim_server, target
    ):
        provider = scim_server()
        instance = target(provider, token="not-the-token")
        user = local_user(registry)

        with pytest.raises(AuthExternalError):
            await EXT_SCIMProvider.push(registry, instance.id)

        assert link(registry, instance, user.id).pending
        assert provider.writes() == []

    async def test_a_forbidden_write_ends_the_run(self, registry, scim_server, target):
        provider = scim_server()
        instance = target(provider)
        first, second = local_user(registry), local_user(registry)
        provider.fail("POST", "Users", 403)

        with pytest.raises(AuthExternalError):
            await EXT_SCIMProvider.push(registry, instance.id)

        assert len([w for w in provider.writes() if w.method == "POST"]) == 1
        assert link(registry, instance, first.id).pending
        assert link(registry, instance, second.id).pending

    async def test_a_refused_write_is_noted_and_the_run_goes_on(
        self, registry, scim_server, target
    ):
        provider = scim_server()
        instance = target(provider)
        first, second = local_user(registry), local_user(registry)
        provider.fail("POST", "Users", 400, times=1)

        summary = await EXT_SCIMProvider.push(registry, instance.id)

        assert summary["users"]["failed"] == 1 and summary["users"]["created"] == 1
        failed = [
            link(registry, instance, u.id)
            for u in (first, second)
            if link(registry, instance, u.id).status == "error"
        ]
        assert len(failed) == 1 and failed[0].pending

    async def test_loopback_needs_an_egress_allowance(
        self, registry, scim_server, target, monkeypatch
    ):
        provider = scim_server()
        instance = target(provider)
        monkeypatch.delenv("EGRESS_ALLOWED_HOSTS")

        with pytest.raises(InvalidInputExternalError) as raised:
            await EXT_SCIMProvider.push(registry, instance.id)

        assert "SSRF" in str(raised.value)
        assert provider.requests == []

    async def test_a_target_without_a_base_url(self, registry, provider_instance):
        instance = provider_instance(PRV_SCIM_SCIMProvider, api_key=TOKEN)

        with pytest.raises(PermanentExternalError):
            await EXT_SCIMProvider.push(registry, instance.id)

    async def test_an_unknown_target(self, registry):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as raised:
            await EXT_SCIMProvider.push(registry, "no-such-target")
        assert raised.value.status_code == 404


class TestGroupsAndScope(Live):
    async def test_teams_become_groups_and_membership_is_patched(
        self, registry, scim_server, target
    ):
        provider = scim_server()
        owner, member = local_user(registry), local_user(registry, first="Grace")
        team = team_with(registry, owner, member)
        outsider = local_user(registry)
        instance = target(provider, team_id=team.id)

        summary = await EXT_SCIMProvider.reconcile(registry, instance.id)

        assert summary["users"]["created"] == 2
        assert provider.by_name("Users", outsider.email) == []
        [group] = provider.by_name("Groups", team.name)
        ids = {remote(provider, u)["id"] for u in (owner, member)}
        assert {m["value"] for m in group["members"]} == ids
        assert group["externalId"] == team.id

        UserTeamManager(model_registry=registry, requester_id=ROOT).delete(
            membership(registry, team, member).id
        )
        await EXT_SCIMProvider.push(registry, instance.id)

        member_id = remote(provider, member)["id"]
        assert remote(provider, member)["active"] is False
        group_patch = [w for w in provider.writes() if "/Groups/" in w.path][-1]
        assert {
            "op": "remove",
            "path": f'members[value eq "{member_id}"]',
        } in group_patch.body["Operations"]
        [group] = provider.by_name("Groups", team.name)
        assert {m["value"] for m in group["members"]} == {remote(provider, owner)["id"]}

        UserTeamManager(model_registry=registry, requester_id=ROOT).create(
            team_id=team.id, user_id=member.id, role_id=env("USER_ROLE_ID")
        )
        await EXT_SCIMProvider.push(registry, instance.id)

        assert remote(provider, member)["active"] is True
        group_patch = [w for w in provider.writes() if "/Groups/" in w.path][-1]
        assert {
            "op": "add",
            "path": "members",
            "value": [{"value": member_id}],
        } in group_patch.body["Operations"]
        assert provider.violations == []

    async def test_a_deleted_team_empties_its_group(
        self, registry, scim_server, target
    ):
        provider = scim_server()
        owner = local_user(registry)
        team = team_with(registry, owner)
        instance = target(provider)
        await EXT_SCIMProvider.reconcile(registry, instance.id)
        assert provider.by_name("Groups", team.name)[0]["members"]

        TeamManager(model_registry=registry, requester_id=ROOT).delete(team.id)
        summary = await EXT_SCIMProvider.push(registry, instance.id)

        assert summary["groups"]["deactivated"] == 1
        assert provider.by_name("Groups", team.name)[0]["members"] == []

    async def test_a_reconcile_pages_through_the_target_and_repairs_drift(
        self, registry, scim_server, target
    ):
        provider = scim_server(page_size=2)
        owner, a, b = (local_user(registry) for _ in range(3))
        team = team_with(registry, owner, a, b)
        for n in range(4):
            provider.seed("Users", userName=f"elsewhere{n}@example.com", active=True)
        instance = target(provider, team_id=team.id, push_groups="false")
        await EXT_SCIMProvider.reconcile(registry, instance.id)
        provider.edit("Users", remote(provider, a)["id"], displayName="Tampered")
        before = len(provider.requests)

        summary = await EXT_SCIMProvider.reconcile(registry, instance.id)

        listing = [
            r
            for r in provider.requests[before:]
            if r.method == "GET" and r.path.endswith("/Users")
        ]
        assert [r.query["startIndex"] for r in listing] == ["1", "3", "5", "7"]
        assert {r.query["count"] for r in listing} == {"2"}
        assert summary["users"] == {**summary["users"], "updated": 1, "unchanged": 2}
        assert remote(provider, a)["displayName"] == "Ada Lovelace"
        assert not provider.by_name("Groups", team.name)
        assert len(provider.resources["Users"]) == 7
