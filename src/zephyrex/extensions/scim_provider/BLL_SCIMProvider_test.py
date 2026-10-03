# SPDX-License-Identifier: AGPL-3.0-or-later
"""SCIM links and the sync log over the API: read-only but for search and
the reconcile route; a target is reconciled only by someone who can see
it, and gives the service provider only what its owner can see; links
are visible with their target and no further. With the app running, a
framework change reaches a target on its own (the hooks' push)."""

import uuid
from typing import Any, Callable, Dict, Iterator, List

import pytest
from fastapi.testclient import TestClient

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.scim_provider.EXT_SCIMProvider import EXT_SCIMProvider
from zephyrex.extensions.scim_provider.PRV_SCIM import PRV_SCIM_SCIMProvider
from zephyrex.extensions.scim_provider.SCIMTestServer import SCIMServiceProvider
from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Auth import UserCredentialManager, UserManager, UserModel
from zephyrex.testing.factories import (
    add_user_to_team,
    create_user,
    generate_test_email,
)
from zephyrex.logic.BLL_Providers import (
    ProviderInstanceManager,
    ProviderInstanceSettingManager,
    ProviderManager,
)

TOKEN = "scim-route-test-token"
ROOT = env("ROOT_ID")
PUSH_WAIT_SECONDS = 30


def auth(user: Any) -> Dict[str, str]:
    return {"Authorization": f"Bearer {user.jwt}"}


@pytest.fixture
def scim_server(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Callable[..., SCIMServiceProvider]]:
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


def base_url(registry: Any, instance_id: str, url: str) -> None:
    ProviderInstanceSettingManager(model_registry=registry, requester_id=ROOT).create(
        provider_instance_id=instance_id, key="base_url", value=url
    )


class TestRoutes(ExtensionServerMixin):
    extension_class = EXT_SCIMProvider

    @pytest.fixture
    def users_target(self, server, admin_a, scim_server) -> Any:
        """A target admin_a owns, made over the API."""
        provider = scim_server()
        providers = server.get("/v1/provider", headers=auth(admin_a)).json()[
            "providers"
        ]
        scim = next(p for p in providers if p["name"] == PRV_SCIM_SCIMProvider.name)
        response = server.post(
            "/v1/provider/instance",
            json={
                "provider_instance": {
                    "name": f"scim-{uuid.uuid4().hex}",
                    "provider_id": scim["id"],
                    "api_key": TOKEN,
                }
            },
            headers=auth(admin_a),
        )
        assert response.status_code == 201, response.text
        instance = response.json()["provider_instance"]
        base_url(server.app.state.model_registry, instance["id"], provider.base_url)
        return provider, instance

    def test_links_and_the_log_are_read_only(self, server):
        paths = server.app.openapi()["paths"]
        writes = {
            (method, path)
            for path, operations in paths.items()
            if path.startswith("/v1/scim_")
            for method in operations
            if method != "get"
        }
        assert writes == {
            ("post", "/v1/scim_link/search"),
            ("post", "/v1/scim_link/reconcile"),
            ("post", "/v1/scim_sync_log/search"),
        }

    def test_a_target_gets_only_what_its_owner_can_see(
        self, server, admin_a, admin_b, team_a, users_target
    ):
        """A user's target gets the owner and the members of the owner's
        teams, and no one else: not a user the framework lets the owner
        read (any user can read a user record), who shares no team."""
        provider, instance = users_target
        teammate = create_user(server, email=generate_test_email("teammate"))
        add_user_to_team(server, teammate.id, team_a.id, env("USER_ROLE_ID"))
        hidden = f"hidden_{uuid.uuid4().hex[:10]}@example.com"
        made = UserModel.DB(server.app.state.model_registry.DB.manager.Base).create(
            requester_id=ROOT,
            model_registry=server.app.state.model_registry,
            return_type="dto",
            override_dto=UserModel,
            email=hidden,
            username=hidden.split("@")[0],
        )
        response = server.post(
            "/v1/scim_link/reconcile",
            json={"provider_instance_id": instance["id"]},
            headers=auth(admin_a),
        )

        assert response.status_code == 200, response.text
        assert response.json()["users"]["created"] >= 1
        assert provider.by_name("Users", admin_a.email)
        assert provider.by_name("Users", teammate.email)
        assert provider.by_name("Users", admin_b.email) == []
        assert provider.by_name("Users", hidden) == []
        assert provider.by_name("Groups", team_a.name)
        # The framework itself lets admin_a read that user; the target
        # still does not get it.
        assert UserManager(
            model_registry=server.app.state.model_registry, requester_id=admin_a.id
        ).get(id=made.id)
        assert provider.violations == []

        mine = server.get("/v1/scim_link", headers=auth(admin_a)).json()["scim_links"]
        assert {
            row["local_id"]
            for row in mine
            if row["provider_instance_id"] == instance["id"]
        } >= {admin_a.id}
        theirs = server.get("/v1/scim_link", headers=auth(admin_b)).json()["scim_links"]
        assert not [r for r in theirs if r["provider_instance_id"] == instance["id"]]
        log = server.get("/v1/scim_sync_log", headers=auth(admin_b)).json()
        assert not [
            r
            for r in log["scim_sync_logs"]
            if r["provider_instance_id"] == instance["id"]
        ]

    def test_a_scope_outside_the_owners_teams_gives_nothing(
        self, server, admin_a, admin_b, team_b, users_target
    ):
        provider, instance = users_target
        ProviderInstanceSettingManager(
            model_registry=server.app.state.model_registry, requester_id=ROOT
        ).create(provider_instance_id=instance["id"], key="team_id", value=team_b.id)

        response = server.post(
            "/v1/scim_link/reconcile",
            json={"provider_instance_id": instance["id"]},
            headers=auth(admin_a),
        )

        assert response.status_code == 200, response.text
        assert provider.by_name("Users", admin_b.email) == []
        assert provider.by_name("Groups", team_b.name) == []
        assert provider.writes() == []

    def test_another_user_cannot_reconcile_the_target(
        self, server, admin_b, users_target
    ):
        provider, instance = users_target
        response = server.post(
            "/v1/scim_link/reconcile",
            json={"provider_instance_id": instance["id"]},
            headers=auth(admin_b),
        )
        assert response.status_code == 404, response.text
        assert provider.requests == []

    def test_reconcile_needs_a_token(self, server, users_target):
        _, instance = users_target
        response = server.post(
            "/v1/scim_link/reconcile", json={"provider_instance_id": instance["id"]}
        )
        assert response.status_code == 401

    def test_a_change_reaches_the_target_while_the_app_runs(self, server, scim_server):
        registry = server.app.state.model_registry
        provider = scim_server()
        scim = ProviderManager(model_registry=registry, requester_id=ROOT).get(
            name=PRV_SCIM_SCIMProvider.name
        )
        instance = ProviderInstanceManager(
            model_registry=registry, requester_id=ROOT
        ).create(
            name=f"scim-{uuid.uuid4().hex}",
            provider_id=scim.id,
            api_key=TOKEN,
            scope="root",
        )
        base_url(registry, instance.id, provider.base_url)
        email = f"live_{uuid.uuid4().hex[:10]}@example.com"

        with TestClient(server.app):
            user = UserModel.DB(registry.DB.manager.Base).create(
                requester_id=env("SYSTEM_ID"),
                model_registry=registry,
                return_type="dto",
                override_dto=UserModel,
                email=email,
                username=email.split("@")[0],
            )
            UserCredentialManager(requester_id=user.id, model_registry=registry).create(
                user_id=user.id, password="testpassword1"
            )
            for push in list(EXT_SCIMProvider._pushes):
                push.result(timeout=PUSH_WAIT_SECONDS)

        [created] = provider.by_name("Users", email)
        assert created["externalId"] == user.id
        assert provider.violations == []
