# SPDX-License-Identifier: AGPL-3.0-or-later
"""The forward-auth endpoint end to end: the app's real routes, sessions
from real logins, rules made through the ROOT API, each subrequest sent from
the proxy's address as Traefik/nginx/Caddy send it.

The scaffold this replaces had a rules table and no endpoint at all: no
proxy could ask anything. Each test below therefore fails on it; the ones
that also close a hole name it: proxy headers believed from anyone, an open
redirect through the return URL, rule paths bypassed by encoding or dot
segments, soft-deleted rules and memberships still deciding, a revoked
session passing, header injection through a team name, the session token
echoed, rules readable or writable by users."""

import base64
import os
import uuid
from typing import Any, Callable, Dict, Iterator, List
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from conftest import CORE_COMPANION_EXTENSIONS
from zephyrex.extensions.forward_auth_provider.BLL_ForwardAuthProvider import (
    FORWARD_AUTH_RATE_LIMIT,
    IDENTITY_HEADERS_SETTING,
    LOGIN_URL_SETTING,
    REQUIRE_RULE_SETTING,
    RETURN_HOSTS_SETTING,
    TRUSTED_PROXIES_SETTING,
)
from zephyrex.extensions.forward_auth_provider.EXT_ForwardAuthProvider import (
    EXT_ForwardAuthProvider,
)
from zephyrex.lib.Environment import env
from zephyrex.lib.InboundSecurity import (
    _rate_limit_policy,
    parse_rate_spec,
    reset_rate_limit_counts,
)
from zephyrex.lib.SessionCookies import SESSION_COOKIE
from zephyrex.logic.BLL_Auth import UserModel, UserTeamModel
from zephyrex.testing.factories import (
    TEST_PASSWORD,
    UserWithJWT,
    create_team,
    create_user,
    generate_test_email,
)

VERIFY = "/v1/auth/forward/verify"
RULES = "/v1/auth/forward/rule"
PROXY = "10.77.0.5"
PROXY_NETWORK = "10.77.0.0/24"
STRANGER = "203.0.113.9"
APP_HOST = "app.example.test"
LOGIN_URL = "https://login.example.test/sign-in?theme=dark"
IDENTITY = (
    "X-Forwarded-User=id,X-Forwarded-Email=email,"
    "X-Forwarded-Name=name,X-Forwarded-Groups=teams"
)
# What Firefox and Chrome send when navigating.
BROWSER_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"


@pytest.fixture(scope="module")
def app() -> FastAPI:
    from zephyrex.app import instance
    from zephyrex.pydantic2.sqlalchemy import prepare_test_registry

    prepare_test_registry()
    worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
    extensions = ["forward_auth_provider", "auth_session"]
    built: FastAPI = instance(
        db_prefix=f"test.forward_auth_provider.{worker}",
        extensions=",".join(
            extensions + [c for c in CORE_COMPANION_EXTENSIONS if c not in extensions]
        ),
    )
    return built


@pytest.fixture(scope="module")
def server(app: FastAPI) -> TestClient:
    return TestClient(app, base_url="https://testserver")


@pytest.fixture(scope="module")
def proxy(app: FastAPI) -> TestClient:
    """Connections from the reverse proxy."""
    return TestClient(app, base_url="https://auth.internal", client=(PROXY, 40000))


@pytest.fixture(scope="module")
def registry(app: FastAPI) -> Any:
    return app.state.model_registry


@pytest.fixture(autouse=True)
def configured(set_env: Callable[[str, str], None]) -> Iterator[None]:
    set_env(TRUSTED_PROXIES_SETTING, PROXY_NETWORK)
    set_env(IDENTITY_HEADERS_SETTING, IDENTITY)
    set_env(LOGIN_URL_SETTING, LOGIN_URL)
    set_env(RETURN_HOSTS_SETTING, f"{APP_HOST},.corp.example.test")
    set_env(REQUIRE_RULE_SETTING, "false")
    reset_rate_limit_counts()
    yield
    reset_rate_limit_counts()


def root() -> Dict[str, str]:
    return {"X-API-Key": env("ROOT_API_KEY")}


def forwarded(
    uri: str = "/", host: str = APP_HOST, proto: str = "https", method: str = "GET"
) -> Dict[str, str]:
    return {
        "X-Forwarded-Method": method,
        "X-Forwarded-Proto": proto,
        "X-Forwarded-Host": host,
        "X-Forwarded-Uri": uri,
    }


def session_cookie(server: TestClient, email: str | None) -> str:
    """The zx_session cookie a browser login sets."""
    assert email
    basic = base64.b64encode(f"{email}:{TEST_PASSWORD}".encode()).decode()
    server.cookies.clear()
    response = server.post(
        "/v1/user/authorize", headers={"Authorization": f"Basic {basic}"}
    )
    assert response.status_code == 200, response.text
    cookie = response.cookies.get(SESSION_COOKIE)
    server.cookies.clear()
    assert cookie
    return str(cookie)


def ask(
    client: TestClient,
    *,
    cookie: str | None = None,
    token: str | None = None,
    accept: str = "application/json",
    params: Dict[str, str] | None = None,
    **request: str,
) -> Any:
    headers = {**forwarded(**request), "Accept": accept}
    if cookie is not None:
        headers["Cookie"] = f"{SESSION_COOKIE}={cookie}"
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return client.get(VERIFY, headers=headers, params=params, follow_redirects=False)


@pytest.fixture(scope="module")
def member(server: TestClient) -> UserWithJWT:
    user: UserWithJWT = create_user(server, generate_test_email("fa_member"))
    return user


@pytest.fixture(scope="module")
def outsider(server: TestClient) -> UserWithJWT:
    user: UserWithJWT = create_user(server, generate_test_email("fa_outsider"))
    return user


@pytest.fixture(scope="module")
def team(server: TestClient, member: UserWithJWT) -> Any:
    return create_team(server, member.id, name=f"Ops {uuid.uuid4().hex[:6]}")


@pytest.fixture
def rule(server: TestClient) -> Iterator[Callable[..., Dict[str, Any]]]:
    """Make rules as ROOT; each is deleted after the test."""
    made: List[str] = []

    def _make(**fields: Any) -> Dict[str, Any]:
        body = {"name": f"rule-{uuid.uuid4().hex[:6]}", **fields}
        response = server.post(RULES, json={"forward_auth_rule": body}, headers=root())
        assert response.status_code == 201, response.text
        created: Dict[str, Any] = response.json()["forward_auth_rule"]
        made.append(created["id"])
        return created

    yield _make
    for rule_id in made:
        server.delete(f"{RULES}/{rule_id}", headers=root())


class TestSignedIn:
    def test_session_cookie_passes_with_the_configured_identity(
        self, server: TestClient, proxy: TestClient, member: UserWithJWT, team: Any
    ) -> None:
        cookie = session_cookie(server, member.email)
        response = ask(proxy, cookie=cookie)
        assert response.status_code == 200, response.text
        assert response.headers["X-Forwarded-User"] == member.id
        assert response.headers["X-Forwarded-Email"] == member.email
        assert response.headers["X-Forwarded-Name"] == member.display_name
        assert team.name in response.headers["X-Forwarded-Groups"].split(",")
        assert response.headers["Cache-Control"] == "no-store"
        from_browser = ask(proxy, cookie=cookie, accept=BROWSER_ACCEPT)
        assert from_browser.status_code == 200
        assert from_browser.headers["X-Forwarded-User"] == member.id

    def test_bearer_token_passes(self, proxy: TestClient, member: UserWithJWT) -> None:
        assert ask(proxy, token=member.jwt).status_code == 200

    def test_only_configured_headers_and_never_the_token(
        self,
        server: TestClient,
        proxy: TestClient,
        member: UserWithJWT,
        set_env: Callable[[str, str], None],
    ) -> None:
        set_env(IDENTITY_HEADERS_SETTING, "Remote-User=id")
        cookie = session_cookie(server, member.email)
        response = ask(proxy, cookie=cookie)
        assert response.status_code == 200
        assert response.headers["Remote-User"] == member.id
        sent = {name.lower() for name in response.headers}
        assert not sent & {
            "x-forwarded-user",
            "x-forwarded-email",
            "x-forwarded-groups",
            "set-cookie",
            "authorization",
        }
        everything = " ".join(response.headers.values()) + response.text
        assert cookie not in everything and member.jwt not in everything

    def test_team_name_cannot_inject_a_header(
        self, server: TestClient, proxy: TestClient
    ) -> None:
        user = create_user(server, generate_test_email("fa_inject"))
        create_team(server, user.id, name="Night, Shift\r\nX-Injected: 1")
        response = ask(proxy, token=user.jwt)
        assert response.status_code == 200
        assert "x-injected" not in {name.lower() for name in response.headers}
        assert response.headers["X-Forwarded-Groups"] == (
            "Night%2C Shift%0D%0AX-Injected: 1"
        )


class TestNoSession:
    def test_api_client_gets_401(self, proxy: TestClient) -> None:
        response = ask(proxy)
        assert response.status_code == 401
        assert "location" not in response.headers
        assert response.headers["WWW-Authenticate"] == "Bearer"

    def test_invalid_token_gets_401(self, proxy: TestClient) -> None:
        assert ask(proxy, token="not.a.jwt").status_code == 401

    def test_revoked_session_gets_401(
        self, server: TestClient, proxy: TestClient, outsider: UserWithJWT
    ) -> None:
        cookie = session_cookie(server, outsider.email)
        assert ask(proxy, cookie=cookie).status_code == 200
        logout = server.post(
            "/v1/user/logout", headers={"Authorization": f"Bearer {cookie}"}
        )
        assert logout.status_code == 204, logout.text
        assert ask(proxy, cookie=cookie).status_code == 401

    def test_disabled_account_gets_403(
        self, server: TestClient, proxy: TestClient, registry: Any
    ) -> None:
        user = create_user(server, generate_test_email("fa_disabled"))
        UserDB = UserModel.DB(registry.DB.manager.Base)
        UserDB.update(
            requester_id=env("ROOT_ID"),
            model_registry=registry,
            id=user.id,
            new_properties={"active": False},
        )
        assert ask(proxy, token=user.jwt).status_code == 403

    def test_browser_is_sent_to_login_returning_to_its_page(
        self, proxy: TestClient
    ) -> None:
        response = ask(proxy, accept=BROWSER_ACCEPT, uri="/reports?id=7&x=1")
        assert response.status_code == 302
        location = urlsplit(response.headers["Location"])
        assert location.netloc == "login.example.test"
        query = parse_qs(location.query)
        assert query["theme"] == ["dark"]
        assert query["rd"] == [f"https://{APP_HOST}/reports?id=7&x=1"]

    def test_subdomain_of_an_allowed_suffix_is_returned_to(
        self, proxy: TestClient
    ) -> None:
        response = ask(proxy, accept=BROWSER_ACCEPT, host="wiki.corp.example.test:8443")
        query = parse_qs(urlsplit(response.headers["Location"]).query)
        assert query["rd"] == ["https://wiki.corp.example.test:8443/"]

    def test_no_open_redirect_to_a_host_not_allowed(self, proxy: TestClient) -> None:
        for host in ("evil.example", "corp.example.test.evil.example"):
            response = ask(proxy, accept=BROWSER_ACCEPT, host=host)
            assert response.status_code == 302
            assert response.headers["Location"] == LOGIN_URL

    def test_no_open_redirect_through_the_uri(self, proxy: TestClient) -> None:
        for uri in ("https://evil.example/", "/\\evil.example", "/a b"):
            response = ask(proxy, accept=BROWSER_ACCEPT, uri=uri)
            assert response.status_code == 400, uri
            assert "location" not in response.headers
        response = ask(proxy, accept=BROWSER_ACCEPT, uri="//evil.example/x")
        query = parse_qs(urlsplit(response.headers["Location"]).query)
        assert urlsplit(query["rd"][0]).netloc == APP_HOST

    def test_a_form_post_gets_401_not_a_redirect(self, proxy: TestClient) -> None:
        assert ask(proxy, accept=BROWSER_ACCEPT, method="POST").status_code == 401

    def test_nginx_gets_401_carrying_the_location(self, proxy: TestClient) -> None:
        response = ask(
            proxy, accept=BROWSER_ACCEPT, params={"redirect_with_401": "true"}
        )
        assert response.status_code == 401
        assert response.headers["Location"].startswith(LOGIN_URL + "&rd=")

    def test_without_a_login_url_a_browser_gets_401(
        self, proxy: TestClient, set_env: Callable[[str, str], None]
    ) -> None:
        set_env(LOGIN_URL_SETTING, "")
        assert ask(proxy, accept=BROWSER_ACCEPT).status_code == 401


class TestTrustedProxy:
    def test_a_stranger_is_refused_even_with_a_session(
        self, app: FastAPI, member: UserWithJWT
    ) -> None:
        stranger = TestClient(app, client=(STRANGER, 50000))
        assert ask(stranger, token=member.jwt).status_code == 403

    def test_no_proxy_trusted_refuses_everyone(
        self,
        proxy: TestClient,
        member: UserWithJWT,
        set_env: Callable[[str, str], None],
    ) -> None:
        set_env(TRUSTED_PROXIES_SETTING, "")
        assert ask(proxy, token=member.jwt).status_code == 403

    def test_missing_or_repeated_forwarded_headers_are_400(
        self, proxy: TestClient, member: UserWithJWT
    ) -> None:
        bearer = {"Authorization": f"Bearer {member.jwt}"}
        missing = proxy.get(VERIFY, headers={**bearer, "X-Forwarded-Uri": "/"})
        assert missing.status_code == 400
        appended = proxy.get(
            VERIFY,
            headers={**bearer, **forwarded(host=f"{APP_HOST}, evil.example")},
        )
        assert appended.status_code == 400
        repeated = proxy.get(
            VERIFY,
            headers=[
                ("Authorization", f"Bearer {member.jwt}"),
                ("X-Forwarded-Host", APP_HOST),
                ("X-Forwarded-Host", "evil.example"),
                ("X-Forwarded-Uri", "/"),
            ],
        )
        assert repeated.status_code == 400

    def test_misconfigured_identity_headers_answer_503(
        self,
        proxy: TestClient,
        member: UserWithJWT,
        set_env: Callable[[str, str], None],
    ) -> None:
        set_env(IDENTITY_HEADERS_SETTING, "Authorization=id")
        assert ask(proxy, token=member.jwt).status_code == 503
        assert any(
            "Authorization cannot carry identity" in problem
            for problem in EXT_ForwardAuthProvider.validate_config()
        )

    def test_rate_limited_per_address(self) -> None:
        count, window = parse_rate_spec(FORWARD_AUTH_RATE_LIMIT)
        assert _rate_limit_policy("GET", VERIFY) == (count, window, "ip")


class TestRules:
    def test_required_team(
        self,
        proxy: TestClient,
        rule: Callable[..., Dict[str, Any]],
        member: UserWithJWT,
        outsider: UserWithJWT,
        team: Any,
    ) -> None:
        made = rule(host=APP_HOST, path_prefix="/admin", required_team_id=team.id)
        assert made["path_prefix"] == "/admin"
        assert ask(proxy, token=member.jwt, uri="/admin/users").status_code == 200
        refused = ask(proxy, token=outsider.jwt, uri="/admin/users")
        assert refused.status_code == 403
        assert ask(proxy, token=outsider.jwt, uri="/administrator").status_code == 200
        assert (
            ask(proxy, token=outsider.jwt, uri="/admin", host="other.test").status_code
            == 200
        )

    def test_encoded_and_dotted_paths_do_not_bypass(
        self,
        proxy: TestClient,
        rule: Callable[..., Dict[str, Any]],
        outsider: UserWithJWT,
        team: Any,
    ) -> None:
        rule(path_prefix="/admin", required_team_id=team.id)
        for uri in (
            "/%61dmin/x",
            "/public/../admin",
            "//admin",
            "/./admin/",
            "/admin%2Fx",
            "/admin?x=1",
            "/x/..%2Fadmin",
        ):
            assert ask(proxy, token=outsider.jwt, uri=uri).status_code == 403, uri

    def test_host_is_compared_without_case_or_port(
        self,
        proxy: TestClient,
        rule: Callable[..., Dict[str, Any]],
        outsider: UserWithJWT,
        team: Any,
    ) -> None:
        made = rule(host="Secure.Example.Test.", required_team_id=team.id)
        assert made["host"] == "secure.example.test"
        response = ask(proxy, token=outsider.jwt, host="SECURE.example.test:443")
        assert response.status_code == 403

    def test_the_most_specific_rule_decides(
        self,
        proxy: TestClient,
        rule: Callable[..., Dict[str, Any]],
        outsider: UserWithJWT,
        team: Any,
    ) -> None:
        rule(path_prefix="/docs", required_team_id=team.id)
        rule(path_prefix="/docs/public")
        assert ask(proxy, token=outsider.jwt, uri="/docs/public/a").status_code == 200
        assert ask(proxy, token=outsider.jwt, uri="/docs/private").status_code == 403

    def test_deleted_and_disabled_rules_do_not_decide(
        self,
        server: TestClient,
        proxy: TestClient,
        rule: Callable[..., Dict[str, Any]],
        outsider: UserWithJWT,
        team: Any,
    ) -> None:
        deleted = rule(path_prefix="/gone", required_team_id=team.id)
        assert ask(proxy, token=outsider.jwt, uri="/gone").status_code == 403
        assert (
            server.delete(f"{RULES}/{deleted['id']}", headers=root()).status_code == 204
        )
        assert ask(proxy, token=outsider.jwt, uri="/gone").status_code == 200
        rule(path_prefix="/off", required_team_id=team.id, enabled=False)
        assert ask(proxy, token=outsider.jwt, uri="/off").status_code == 200

    def test_a_removed_membership_stops_passing(
        self,
        server: TestClient,
        proxy: TestClient,
        registry: Any,
        rule: Callable[..., Dict[str, Any]],
    ) -> None:
        user = create_user(server, generate_test_email("fa_leaver"))
        own = create_team(server, user.id, name=f"Leavers {uuid.uuid4().hex[:6]}")
        rule(path_prefix="/leavers", required_team_id=own.id)
        assert ask(proxy, token=user.jwt, uri="/leavers").status_code == 200
        UserTeamDB = UserTeamModel.DB(registry.DB.manager.Base)
        for membership in UserTeamDB.list(
            requester_id=env("ROOT_ID"),
            model_registry=registry,
            user_id=user.id,
            team_id=own.id,
            return_type="dto",
            override_dto=UserTeamModel,
        ):
            UserTeamDB.delete(
                requester_id=env("ROOT_ID"), model_registry=registry, id=membership.id
            )
        response = ask(proxy, token=user.jwt, uri="/leavers")
        assert response.status_code == 403
        assert own.name not in response.headers.get("X-Forwarded-Groups", "")

    def test_require_rule_refuses_uncovered_pages(
        self,
        proxy: TestClient,
        rule: Callable[..., Dict[str, Any]],
        member: UserWithJWT,
        set_env: Callable[[str, str], None],
    ) -> None:
        set_env(REQUIRE_RULE_SETTING, "true")
        rule(host=APP_HOST, path_prefix="/open")
        assert ask(proxy, token=member.jwt, uri="/open/x").status_code == 200
        assert ask(proxy, token=member.jwt, uri="/elsewhere").status_code == 403


class TestRuleAdministration:
    def test_users_can_neither_read_nor_write_rules(
        self,
        server: TestClient,
        rule: Callable[..., Dict[str, Any]],
        member: UserWithJWT,
    ) -> None:
        made = rule(path_prefix="/secret-area")
        bearer = {"Authorization": f"Bearer {member.jwt}"}
        assert server.get(RULES, headers=bearer).status_code == 403
        assert server.get(f"{RULES}/{made['id']}", headers=bearer).status_code == 403
        created = server.post(
            RULES,
            json={"forward_auth_rule": {"name": "mine", "path_prefix": "/"}},
            headers=bearer,
        )
        assert created.status_code == 403
        changed = server.put(
            f"{RULES}/{made['id']}",
            json={"forward_auth_rule": {"enabled": False}},
            headers=bearer,
        )
        assert changed.status_code == 403
        assert server.delete(f"{RULES}/{made['id']}", headers=bearer).status_code == 403

    def test_malformed_rules_are_refused(self, server: TestClient) -> None:
        for fields in (
            {"path_prefix": "admin"},
            {"path_prefix": "/a?b"},
            {"host": "https://app.example.test"},
            {"host": "app.example.test/x"},
        ):
            response = server.post(
                RULES,
                json={"forward_auth_rule": {"name": "bad", **fields}},
                headers=root(),
            )
            assert response.status_code == 422, (fields, response.text)

    def test_unknown_team_is_refused(self, server: TestClient) -> None:
        response = server.post(
            RULES,
            json={
                "forward_auth_rule": {
                    "name": "ghost",
                    "required_team_id": str(uuid.uuid4()),
                }
            },
            headers=root(),
        )
        assert response.status_code == 404


class TestAbilities:
    async def test_check_access_and_list_rules(
        self,
        rule: Callable[..., Dict[str, Any]],
        member: UserWithJWT,
        outsider: UserWithJWT,
        team: Any,
    ) -> None:
        made = rule(path_prefix="/ability", required_team_id=team.id)
        listed = await EXT_ForwardAuthProvider.list_forward_auth_rules()
        assert made["id"] in {r["id"] for r in listed}
        allowed = await EXT_ForwardAuthProvider.check_forward_auth_access(
            member.id, APP_HOST, "/ability/x"
        )
        assert allowed == {
            "allowed": True,
            "reason": "A member of the required team",
            "rule_id": made["id"],
        }
        refused = await EXT_ForwardAuthProvider.check_forward_auth_access(
            outsider.id, APP_HOST, "/%61bility"
        )
        assert refused["allowed"] is False
