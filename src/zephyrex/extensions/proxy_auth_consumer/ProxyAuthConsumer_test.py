# SPDX-License-Identifier: AGPL-3.0-or-later
"""Proxy-header sign-in end to end.

The real login route is driven through real requests from clients whose
connection address is set: one inside the trusted proxy network, one
outside it. Users, links and sessions land in the app's real database.
"""

import uuid
from typing import Any, Callable, Dict, List, Optional

import pytest
from fastapi.testclient import TestClient

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.proxy_auth_consumer.BLL_ProxyAuthConsumer import (
    UserProxyAuthLinkManager,
    UserProxyAuthLinkModel,
    validate_identity,
)
from zephyrex.extensions.proxy_auth_consumer.EXT_ProxyAuthConsumer import (
    EXT_ProxyAuthConsumer,
)
from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Auth import UserModel
from zephyrex.logic.BLL_Auth.user_team import UserTeamModel
from zephyrex.testing.factories import create_user, generate_test_email

LOGIN = "/v1/auth/proxy/login"
LINKS = "/v1/auth/proxy"
USER_HEADER = "X-Forwarded-User"
EMAIL_HEADER = "X-Forwarded-Email"
NAME_HEADER = "X-Forwarded-Name"
PROXY = "10.20.30.40"
PROXY_NETWORK = "10.20.30.0/24"
STRANGER = "198.51.100.7"


def unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def detail(response: Any) -> str:
    """The refusal's message, as the API wraps it."""
    body = response.json()["detail"]
    return str(body["message"] if isinstance(body, dict) else body)


class TestIdentityShape:
    @pytest.mark.parametrize(
        "identity", ["", "a" * 257, "alice,bob", "alice\x00", "ali\nce", "a\x7fb"]
    )
    def test_what_no_proxy_should_assert_is_refused(self, identity: str) -> None:
        with pytest.raises(Exception) as refused:
            validate_identity(identity)
        assert getattr(refused.value, "status_code", None) == 400

    @pytest.mark.parametrize(
        "identity", ["alice", "alice@example.org", "CN=Alice", "zoë", "a" * 256]
    )
    def test_ordinary_identities_pass(self, identity: str) -> None:
        validate_identity(identity)


class TestProxySignIn(ExtensionServerMixin):
    extension_class = EXT_ProxyAuthConsumer

    # -- fixtures ---------------------------------------------------------

    @pytest.fixture
    def proxied(self, server: Any, set_env: Callable[[str, str], None]) -> TestClient:
        """A client connecting from the trusted proxy."""
        set_env("PROXY_AUTH_CONSUMER_TRUSTED_PROXIES", PROXY_NETWORK)
        set_env("PROXY_AUTH_CONSUMER_TRUST_EMAIL", "false")
        set_env("REGISTRATION_MODE", "open")
        return TestClient(server.app, client=(PROXY, 44321))

    @staticmethod
    def login(client: TestClient, headers: Optional[Any] = None) -> Any:
        return client.post(LOGIN, json={}, headers=headers or {})

    @staticmethod
    def links(server: Any, identity: str) -> List[Any]:
        registry = server.app.state.model_registry
        LinkDB = UserProxyAuthLinkModel.DB(registry.DB.manager.Base)
        links: List[Any] = LinkDB.list(
            requester_id=env("ROOT_ID"),
            model_registry=registry,
            return_type="dto",
            override_dto=UserProxyAuthLinkModel,
            filters=[LinkDB.identity == identity, LinkDB.deleted_at.is_(None)],
        )
        return links

    @staticmethod
    def user(server: Any, user_id: str) -> Dict[str, Any]:
        registry = server.app.state.model_registry
        [found] = UserModel.DB(registry.DB.manager.Base).list(
            requester_id=env("ROOT_ID"), model_registry=registry, id=user_id
        )
        return dict(found)

    @staticmethod
    def root_links(server: Any) -> UserProxyAuthLinkManager:
        return UserProxyAuthLinkManager(
            model_registry=server.app.state.model_registry,
            requester_id=env("ROOT_ID"),
        )

    # -- signing in ---------------------------------------------------------

    def test_an_asserted_user_signs_in_and_gets_a_session(self, server, proxied):
        name = unique("alice")
        response = self.login(proxied, {USER_HEADER: name})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["identity"] == name
        assert body["token"] and body["session_key"]
        assert body["user"]["display_name"] == name
        cookies = response.headers.get_list("set-cookie")
        assert any(cookie.startswith("zx_session=") for cookie in cookies)
        me = server.get(
            "/v1/user", headers={"Authorization": f"Bearer {body['token']}"}
        )
        assert me.status_code == 200, me.text
        assert me.json()["user"]["id"] == body["user_id"]
        [link] = self.links(server, name)
        assert link.user_id == body["user_id"]
        assert link.last_login_at is not None

    def test_the_same_identity_returns_to_the_same_account(self, server, proxied):
        name = unique("bob")
        first = self.login(proxied, {USER_HEADER: name})
        again = self.login(proxied, {USER_HEADER: name})
        assert first.status_code == again.status_code == 200
        assert first.json()["user_id"] == again.json()["user_id"]
        assert len(self.links(server, name)) == 1

    def test_identities_match_exactly(self, proxied):
        name = unique("Carol")
        upper = self.login(proxied, {USER_HEADER: name})
        lower = self.login(proxied, {USER_HEADER: name.lower()})
        assert upper.status_code == lower.status_code == 200
        assert upper.json()["user_id"] != lower.json()["user_id"]

    def test_a_utf8_identity_is_read_as_utf8(self, server, proxied):
        name = unique("zoë")
        response = self.login(proxied, [(USER_HEADER.encode(), name.encode())])
        assert response.status_code == 200, response.text
        assert response.json()["identity"] == name
        assert len(self.links(server, name)) == 1

    def test_the_name_header_names_a_new_account(self, proxied):
        response = self.login(
            proxied, {USER_HEADER: unique("dave"), NAME_HEADER: "Dave Example"}
        )
        assert response.status_code == 200, response.text
        assert response.json()["user"]["display_name"] == "Dave Example"

    def test_the_user_header_is_configurable(self, server, proxied, set_env):
        set_env("PROXY_AUTH_CONSUMER_USER_HEADER", "Remote-User")
        name = unique("remote")
        assert self.login(proxied, {USER_HEADER: name}).status_code == 401
        response = self.login(proxied, {"Remote-User": name})
        assert response.status_code == 200, response.text
        assert response.json()["identity"] == name

    # -- trust ------------------------------------------------------------

    def test_the_header_from_a_disallowed_address_is_ignored(self, server, proxied):
        name = unique("spoofed")
        stranger = TestClient(server.app, client=(STRANGER, 50000))
        response = self.login(stranger, {USER_HEADER: name})
        assert response.status_code == 401
        assert self.links(server, name) == []

    def test_with_no_trusted_proxy_the_header_is_never_believed(
        self, server, proxied, set_env
    ):
        set_env("PROXY_AUTH_CONSUMER_TRUSTED_PROXIES", "")
        name = unique("unbelieved")
        assert self.login(proxied, {USER_HEADER: name}).status_code == 401
        assert self.links(server, name) == []

    @pytest.mark.parametrize("proxies", ["*", "10.0.0.0/8,not-an-address"])
    def test_a_malformed_proxy_list_trusts_no_one(
        self, server, proxied, set_env, proxies
    ):
        set_env("PROXY_AUTH_CONSUMER_TRUSTED_PROXIES", proxies)
        name = unique("misconfigured")
        assert self.login(proxied, {USER_HEADER: name}).status_code == 503
        assert self.links(server, name) == []

    def test_an_underscore_header_name_is_refused_as_misconfigured(
        self, proxied, set_env
    ):
        set_env("PROXY_AUTH_CONSUMER_USER_HEADER", "X_Forwarded_User")
        response = self.login(proxied, {USER_HEADER: unique("underscore")})
        assert response.status_code == 503

    def test_a_trusted_proxy_asserting_no_one_signs_no_one_in(self, proxied):
        assert self.login(proxied).status_code == 401
        assert self.login(proxied, {USER_HEADER: "   "}).status_code == 401

    def test_a_header_sent_twice_is_refused(self, server, proxied):
        """A proxy that appends rather than replaces puts the client's first."""
        victim, attacker = unique("victim"), unique("attacker")
        headers = [(USER_HEADER.encode(), victim.encode())]
        headers.append((USER_HEADER.encode(), attacker.encode()))
        response = self.login(proxied, headers)
        assert response.status_code == 400
        assert self.links(server, victim) == self.links(server, attacker) == []

    def test_two_values_joined_into_one_header_are_refused(self, server, proxied):
        victim, attacker = unique("victim"), unique("attacker")
        response = self.login(proxied, {USER_HEADER: f"{victim}, {attacker}"})
        assert response.status_code == 400

    def test_an_overlong_identity_is_refused(self, proxied):
        assert self.login(proxied, {USER_HEADER: "a" * 257}).status_code == 400

    # -- email --------------------------------------------------------------

    def test_an_untrusted_email_never_reaches_an_account(self, server, proxied):
        """Unless the proxy vouches for emails, it could assert anyone's."""
        email = generate_test_email("proxy_victim")
        victim = create_user(server, email=email)
        response = self.login(
            proxied, {USER_HEADER: unique("claimant"), EMAIL_HEADER: email}
        )
        assert response.status_code == 200, response.text
        assert response.json()["user_id"] != victim.id
        assert self.user(server, response.json()["user_id"])["email"] is None

    def test_a_trusted_email_signs_in_to_the_account_with_it(
        self, server, proxied, set_env
    ):
        set_env("PROXY_AUTH_CONSUMER_TRUST_EMAIL", "true")
        email = generate_test_email("proxy_existing")
        user = create_user(server, email=email)
        name = unique("emailed")
        response = self.login(proxied, {USER_HEADER: name, EMAIL_HEADER: email})
        assert response.status_code == 200, response.text
        assert response.json()["user_id"] == user.id
        [link] = self.links(server, name)
        assert link.user_id == user.id

    def test_a_trusted_email_gives_a_new_account_its_email(self, proxied, set_env):
        set_env("PROXY_AUTH_CONSUMER_TRUST_EMAIL", "true")
        email = generate_test_email("proxy_new")
        response = self.login(
            proxied, {USER_HEADER: unique("fresh"), EMAIL_HEADER: email}
        )
        assert response.status_code == 200, response.text
        assert response.json()["user"]["email"] == email

    def test_a_malformed_trusted_email_is_refused(self, proxied, set_env):
        set_env("PROXY_AUTH_CONSUMER_TRUST_EMAIL", "true")
        response = self.login(
            proxied, {USER_HEADER: unique("bad"), EMAIL_HEADER: "not an email"}
        )
        assert response.status_code == 400

    def test_a_link_outranks_a_later_email(self, server, proxied, set_env):
        """Once linked, an identity keeps its account whatever email follows."""
        name = unique("settled")
        first = self.login(proxied, {USER_HEADER: name})
        set_env("PROXY_AUTH_CONSUMER_TRUST_EMAIL", "true")
        other = create_user(server, email=generate_test_email("proxy_other"))
        again = self.login(proxied, {USER_HEADER: name, EMAIL_HEADER: other.email})
        assert again.status_code == 200, again.text
        assert again.json()["user_id"] == first.json()["user_id"] != other.id

    def test_no_email_reaches_root(self, server, proxied, set_env):
        """ROOT's seeded email is predictable; a trusted proxy asserting it
        must not sign in as the superuser."""
        set_env("PROXY_AUTH_CONSUMER_TRUST_EMAIL", "true")
        registry = server.app.state.model_registry
        UserDB = UserModel.DB(registry.DB.manager.Base)
        seeded = self.user(server, env("ROOT_ID"))["email"]
        # The test APP_URI has no domain, so the seeded ``root@`` is no
        # address; give ROOT the shape it has in a deployment.
        root_email = generate_test_email("proxy_root")
        UserDB.update(
            requester_id=env("ROOT_ID"),
            model_registry=registry,
            id=env("ROOT_ID"),
            new_properties={"email": root_email},
        )
        try:
            name = unique("usurper")
            response = self.login(
                proxied, {USER_HEADER: name, EMAIL_HEADER: root_email}
            )
            assert response.status_code == 403, response.text
            assert self.links(server, name) == []
        finally:
            UserDB.update(
                requester_id=env("ROOT_ID"),
                model_registry=registry,
                id=env("ROOT_ID"),
                new_properties={"email": seeded},
            )

    # -- registration -------------------------------------------------------

    @pytest.mark.parametrize("mode", ["closed", "invite"])
    def test_an_unknown_identity_is_refused_unless_registration_is_open(
        self, server, proxied, set_env, mode
    ):
        set_env("REGISTRATION_MODE", mode)
        name = unique("unregistered")
        assert self.login(proxied, {USER_HEADER: name}).status_code == 403
        assert self.links(server, name) == []

    def test_an_invited_trusted_email_gets_an_account(
        self, server, proxied, set_env, admin_a, team_a
    ):
        from zephyrex.extensions.auth_invitations.BLL_Invitations import (
            InvitationManager,
        )

        registry = server.app.state.model_registry
        email = generate_test_email("proxy_invited")
        InvitationManager(
            requester_id=admin_a.id, target_team_id=team_a.id, model_registry=registry
        ).create(team_id=team_a.id, role_id=env("USER_ROLE_ID"), email=email)
        set_env("REGISTRATION_MODE", "invite")
        set_env("PROXY_AUTH_CONSUMER_TRUST_EMAIL", "true")
        response = self.login(
            proxied, {USER_HEADER: unique("invited"), EMAIL_HEADER: email}
        )
        assert response.status_code == 200, response.text
        assert response.json()["user"]["email"] == email
        memberships = UserTeamModel.DB(registry.DB.manager.Base).list(
            requester_id=env("ROOT_ID"),
            model_registry=registry,
            user_id=response.json()["user_id"],
            team_id=team_a.id,
        )
        assert len(memberships) == 1

    def test_an_untrusted_invited_email_is_not_an_invitation(
        self, server, proxied, set_env, admin_a, team_a
    ):
        from zephyrex.extensions.auth_invitations.BLL_Invitations import (
            InvitationManager,
        )

        email = generate_test_email("proxy_uninvited")
        InvitationManager(
            requester_id=admin_a.id,
            target_team_id=team_a.id,
            model_registry=server.app.state.model_registry,
        ).create(team_id=team_a.id, role_id=env("USER_ROLE_ID"), email=email)
        set_env("REGISTRATION_MODE", "invite")
        response = self.login(
            proxied, {USER_HEADER: unique("gatecrasher"), EMAIL_HEADER: email}
        )
        assert response.status_code == 403

    # -- links --------------------------------------------------------------

    def test_a_root_linked_identity_signs_in_while_registration_is_closed(
        self, server, proxied, set_env
    ):
        set_env("REGISTRATION_MODE", "closed")
        user = create_user(server, email=generate_test_email("proxy_linked"))
        name = unique("erin")
        link = self.root_links(server).create(user_id=user.id, identity=name)
        response = self.login(proxied, {USER_HEADER: name})
        assert response.status_code == 200, response.text
        assert response.json()["user_id"] == user.id
        mine = server.get(LINKS, headers={"Authorization": f"Bearer {user.jwt}"})
        assert mine.status_code == 200, mine.text
        [listed] = mine.json()["user_proxy_auth_links"]
        assert listed["identity"] == name
        removed = server.delete(
            f"{LINKS}/{link.id}", headers={"Authorization": f"Bearer {user.jwt}"}
        )
        assert removed.status_code == 204, removed.text
        assert self.login(proxied, {USER_HEADER: name}).status_code == 403

    def test_a_disabled_account_cannot_sign_in(self, server, proxied):
        user = create_user(server, email=generate_test_email("proxy_disabled"))
        name = unique("frank")
        self.root_links(server).create(user_id=user.id, identity=name)
        registry = server.app.state.model_registry
        UserModel.DB(registry.DB.manager.Base).update(
            requester_id=env("ROOT_ID"),
            model_registry=registry,
            id=user.id,
            new_properties={"active": False},
        )
        response = self.login(proxied, {USER_HEADER: name})
        assert response.status_code == 403
        assert detail(response) == "The account is disabled"

    def test_a_deleted_account_cannot_sign_in(self, server, proxied):
        """ROOT reads include soft-deleted users; the link must not."""
        user = create_user(server, email=generate_test_email("proxy_deleted"))
        name = unique("gone")
        self.root_links(server).create(user_id=user.id, identity=name)
        registry = server.app.state.model_registry
        UserModel.DB(registry.DB.manager.Base).delete(
            requester_id=env("ROOT_ID"), model_registry=registry, id=user.id
        )
        assert self.login(proxied, {USER_HEADER: name}).status_code == 403

    @pytest.mark.parametrize("internal", ["ROOT_ID", "SYSTEM_ID", "TEMPLATE_ID"])
    def test_nothing_links_to_an_internal_account(self, server, internal):
        with pytest.raises(Exception) as refused:
            self.root_links(server).create(
                user_id=env(internal), identity=unique("internal")
            )
        assert getattr(refused.value, "status_code", None) == 403

    def test_a_user_cannot_claim_an_identity(self, server, user_b):
        claim = server.post(
            LINKS,
            json={"user_proxy_auth_link": {"identity": "x"}},
            headers={"Authorization": f"Bearer {user_b.jwt}"},
        )
        assert claim.status_code in (404, 405)
        manager = UserProxyAuthLinkManager(
            model_registry=server.app.state.model_registry, requester_id=user_b.id
        )
        with pytest.raises(Exception) as refused:
            manager.create(user_id=user_b.id, identity="x")
        assert getattr(refused.value, "status_code", None) == 403

    def test_a_user_cannot_move_a_link(self, server, user_b, admin_a):
        name = unique("anchored")
        link = self.root_links(server).create(user_id=user_b.id, identity=name)
        manager = UserProxyAuthLinkManager(
            model_registry=server.app.state.model_registry, requester_id=user_b.id
        )
        with pytest.raises(Exception) as refused:
            manager.update(str(link.id), user_id=admin_a.id)
        assert getattr(refused.value, "status_code", None) == 403
        [unchanged] = self.links(server, name)
        assert unchanged.user_id == user_b.id

    def test_an_identity_links_to_one_user(self, server, user_b, admin_a):
        name = unique("once")
        self.root_links(server).create(user_id=user_b.id, identity=name)
        with pytest.raises(Exception) as refused:
            self.root_links(server).create(user_id=admin_a.id, identity=name)
        assert getattr(refused.value, "status_code", None) == 409

    def test_users_see_only_their_own_links(self, server, user_b, admin_a):
        name = unique("private")
        self.root_links(server).create(user_id=admin_a.id, identity=name)
        theirs = server.get(LINKS, headers={"Authorization": f"Bearer {user_b.jwt}"})
        assert theirs.status_code == 200, theirs.text
        assert name not in [
            link["identity"] for link in theirs.json()["user_proxy_auth_links"]
        ]
