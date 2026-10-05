# SPDX-License-Identifier: AGPL-3.0-or-later
"""Forward-auth sign-in end to end, against a real local verifier.

The verifier is a real HTTP server on loopback (``local_http_server``)
answering as Authelia's ``/api/authz/forward-auth`` does: 200 with
``Remote-User``/``Remote-Email``/``Remote-Name`` for a known session
cookie, 401 otherwise. The sign-in route of a real app calls it over the
network through the framework's guarded HTTP client.
"""

import socket
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

import pytest

from conftest import create_user, generate_test_email
from conftest_http import ReceivedRequest
from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.forward_auth_consumer.BLL_ForwardAuthConsumer import (
    ForwardAuthIdentityManager,
    ForwardAuthIdentityModel,
)
from zephyrex.extensions.forward_auth_consumer.EXT_ForwardAuthConsumer import (
    EXT_ForwardAuthConsumer,
)
from zephyrex.extensions.forward_auth_consumer.PRV_ForwardAuthVerifier import (
    PRV_ForwardAuthVerifier,
)
from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Auth import UserModel
from zephyrex.logic.BLL_Providers import (
    ProviderInstanceManager,
    ProviderInstanceModel,
    ProviderInstanceSettingManager,
    ProviderManager,
)
from zephyrex.testing.factories import (
    INTERNAL_ACCOUNTS,
    if_match_of,
    internal_account_email,
)

LOGIN_PATH = "/v1/auth/forward-auth/login"
LINKS_PATH = "/v1/auth/forward-auth"
VERIFY_PATH = "/api/authz/forward-auth"
SESSION_COOKIE = "authelia_session"
SLOW_VERIFIER_SECONDS = 3.0
SHORT_TIMEOUT_SECONDS = "0.3"
# The app database outlives a run; fresh identities keep runs apart.
RUN = uuid.uuid4().hex[:8]

Answer = Tuple[int, Dict[str, str], bytes]


def name(user: str) -> str:
    return f"{user}-{RUN}"


@dataclass
class Session:
    user: str
    email: Optional[str] = None
    display_name: Optional[str] = None


@dataclass
class FakeAuthelia:
    """Authelia's forward-auth answers, by session cookie. A real server
    calls it; ``sessions`` maps a cookie value to whom it signs in."""

    sessions: Dict[str, Session] = field(default_factory=dict)

    def login(self, user: str, **details: Optional[str]) -> str:
        cookie = uuid.uuid4().hex
        self.sessions[cookie] = Session(user=name(user), **details)
        return cookie

    def __call__(self, request: ReceivedRequest) -> Answer:
        cookies = dict(
            pair.strip().split("=", 1)
            for pair in request.headers.get("cookie", "").split(";")
            if "=" in pair
        )
        session = self.sessions.get(cookies.get(SESSION_COOKIE, ""))
        if session is None:
            return 401, {}, b"Unauthorized"
        headers = {"Remote-User": session.user}
        if session.email:
            headers["Remote-Email"] = session.email
        if session.display_name:
            headers["Remote-Name"] = session.display_name
        return 200, headers, b""


def browser(cookie: str, **headers: str) -> Dict[str, str]:
    return {"Cookie": f"{SESSION_COOKIE}={cookie}", **headers}


def _closed_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class TestForwardAuthSignIn(ExtensionServerMixin):
    extension_class = EXT_ForwardAuthConsumer

    @pytest.fixture
    def verifier_instance(self, server: Any) -> Iterator[Callable[..., Any]]:
        """Create forward_auth_verifier instances (as ROOT unless another
        creator is named); every one is removed after the test, so each test
        sees only its own."""
        registry = server.app.state.model_registry
        root_id = env("ROOT_ID")
        created: List[str] = []

        def _create(settings: Dict[str, str], creator_id: Optional[str] = None) -> Any:
            creator = creator_id or root_id
            provider = ProviderManager(
                model_registry=registry, requester_id=root_id
            ).get(name=PRV_ForwardAuthVerifier.name)
            instance = ProviderInstanceManager(
                model_registry=registry, requester_id=creator
            ).create(
                name=f"forward_auth_{uuid.uuid4().hex}",
                provider_id=provider.id,
                scope="root" if creator == root_id else "user",
            )
            created.append(str(instance.id))
            setting_manager = ProviderInstanceSettingManager(
                model_registry=registry, requester_id=creator
            )
            for key, value in settings.items():
                setting_manager.create(
                    provider_instance_id=instance.id, key=key, value=value
                )
            return instance

        yield _create
        InstanceDB = ProviderInstanceModel.DB(registry.DB.manager.Base)
        live = {
            str(instance.id)
            for instance in InstanceDB.list(
                requester_id=root_id,
                model_registry=registry,
                return_type="dto",
                override_dto=ProviderInstanceModel,
                filters=[InstanceDB.id.in_(created), InstanceDB.deleted_at.is_(None)],
            )
        }
        manager = ProviderInstanceManager(model_registry=registry, requester_id=root_id)
        for instance_id in live:
            manager.delete(id=instance_id)

    @pytest.fixture
    def authelia(self) -> FakeAuthelia:
        return FakeAuthelia()

    @pytest.fixture
    def verifier(self, local_http_server, authelia) -> Any:
        return local_http_server({VERIFY_PATH: authelia})

    @pytest.fixture
    def service(self, verifier_instance, verifier) -> Any:
        """The usual configuration: the session cookie, Authelia's headers."""
        return verifier_instance(
            {
                "verify_url": verifier.base_url + VERIFY_PATH,
                "forward_cookies": SESSION_COOKIE,
            }
        )

    @staticmethod
    def links(server: Any, identity: str) -> List[Any]:
        """The live links of ``identity`` (ROOT's reads include deleted rows)."""
        registry = server.app.state.model_registry
        LinkDB = ForwardAuthIdentityModel.DB(registry.DB.manager.Base)
        links: List[Any] = LinkDB.list(
            requester_id=env("ROOT_ID"),
            model_registry=registry,
            return_type="dto",
            override_dto=ForwardAuthIdentityModel,
            filters=[LinkDB.identity == identity, LinkDB.deleted_at.is_(None)],
        )
        return links

    @staticmethod
    def root_links(server: Any) -> ForwardAuthIdentityManager:
        return ForwardAuthIdentityManager(
            model_registry=server.app.state.model_registry,
            requester_id=env("ROOT_ID"),
        )

    # -- signing in -------------------------------------------------------

    def test_without_a_verifier_sign_in_is_unavailable(self, server, authelia):
        cookie = authelia.login("alice")
        assert server.get(LOGIN_PATH, headers=browser(cookie)).status_code == 503

    def test_first_sign_in_creates_the_user_and_issues_a_session(
        self, server, authelia, service, set_env
    ):
        set_env("REGISTRATION_MODE", "open")
        cookie = authelia.login("alice", display_name="Alice Example")
        response = server.get(LOGIN_PATH, headers=browser(cookie))
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["identity"] == name("alice")
        assert body["provider_instance_id"] == str(service.id)
        assert body["token"] and body["session_key"]
        assert body["user"]["display_name"] == "Alice Example"
        cookies = response.headers.get_list("set-cookie")
        assert any(cookie.startswith("zx_session=") for cookie in cookies)
        me = server.get(
            "/v1/user", headers={"Authorization": f"Bearer {body['token']}"}
        )
        assert me.status_code == 200, me.text
        assert me.json()["user"]["id"] == body["user_id"]
        [link] = self.links(server, name("alice"))
        assert link.user_id == body["user_id"]
        assert link.provider_instance_id == str(service.id)
        assert link.last_login_at is not None

    def test_the_identity_signs_in_as_the_same_user_again(
        self, server, authelia, service, set_env
    ):
        set_env("REGISTRATION_MODE", "open")
        first = server.get(LOGIN_PATH, headers=browser(authelia.login("bob")))
        second = server.get(LOGIN_PATH, headers=browser(authelia.login("bob")))
        assert first.status_code == second.status_code == 200
        assert first.json()["user_id"] == second.json()["user_id"]
        assert len(self.links(server, name("bob"))) == 1

    def test_the_verifier_sees_only_the_named_credentials(
        self, server, authelia, verifier, service, set_env
    ):
        """Nothing of the browser's but the named cookie reaches the
        verifier: not its other cookies, not its Authorization, not this
        app's own session cookie."""
        set_env("REGISTRATION_MODE", "open")
        cookie = authelia.login("carol")
        response = server.get(
            f"{LOGIN_PATH}?next=/home",
            headers={
                "Cookie": f"zx_session=ours; {SESSION_COOKIE}={cookie}; other=1",
                "Authorization": "Bearer someone-elses",
                "X-Api-Key": "private",
            },
        )
        assert response.status_code == 200, response.text
        [sent] = verifier.requests
        assert sent.method == "GET" and sent.path == VERIFY_PATH
        assert sent.headers["cookie"] == f"{SESSION_COOKIE}={cookie}"
        assert "authorization" not in sent.headers
        assert "x-api-key" not in sent.headers
        # The URL being reached, as Authelia's access rules match it.
        assert sent.headers["x-forwarded-method"] == "GET"
        assert sent.headers["x-forwarded-proto"] == "http"
        assert sent.headers["x-forwarded-host"] == "testserver"
        assert sent.headers["x-forwarded-uri"] == f"{LOGIN_PATH}?next=/home"
        assert sent.headers["x-original-url"].endswith(f"{LOGIN_PATH}?next=/home")

    def test_named_headers_are_forwarded(
        self, server, verifier_instance, local_http_server, set_env
    ):
        """oauth2-proxy style: a bearer token in Authorization, the user in
        X-Auth-Request-User."""
        set_env("REGISTRATION_MODE", "open")
        token = uuid.uuid4().hex
        identity = name("dave")

        def oauth2_proxy(request: ReceivedRequest) -> Answer:
            if request.headers.get("authorization") != f"Bearer {token}":
                return 401, {}, b""
            return 202, {"X-Auth-Request-User": identity}, b""

        proxy = local_http_server({"/oauth2/auth": oauth2_proxy})
        verifier_instance(
            {
                "verify_url": proxy.base_url + "/oauth2/auth",
                "forward_headers": "Authorization",
                "user_header": "X-Auth-Request-User",
            }
        )
        refused = server.get(LOGIN_PATH, headers={"Authorization": "Bearer wrong"})
        assert refused.status_code == 401
        signed_in = server.get(LOGIN_PATH, headers={"Authorization": f"Bearer {token}"})
        assert signed_in.status_code == 200, signed_in.text
        assert signed_in.json()["identity"] == identity

    def test_the_original_url_setting_describes_the_reached_url(
        self, server, authelia, verifier, verifier_instance, set_env
    ):
        set_env("REGISTRATION_MODE", "open")
        verifier_instance(
            {
                "verify_url": verifier.base_url + VERIFY_PATH,
                "forward_cookies": SESSION_COOKIE,
                "original_url": "https://app.example.com/sign-in",
            }
        )
        response = server.get(LOGIN_PATH, headers=browser(authelia.login("erin")))
        assert response.status_code == 200, response.text
        [sent] = verifier.requests
        assert sent.headers["x-forwarded-proto"] == "https"
        assert sent.headers["x-forwarded-host"] == "app.example.com"
        assert sent.headers["x-forwarded-uri"] == "/sign-in"

    # -- the identity comes from the verifier only ------------------------

    def test_a_client_sent_identity_header_is_ignored(
        self, server, authelia, verifier, service, set_env
    ):
        """The classic forward-auth hole: believing Remote-User from the
        browser. Without a session the browser gets nothing, and with one
        it is who the verifier says."""
        set_env("REGISTRATION_MODE", "open")
        spoofed = {"Remote-User": name("root"), "X-Forwarded-User": name("root")}
        assert server.get(LOGIN_PATH, headers=spoofed).status_code == 401
        cookie = authelia.login("frank")
        response = server.get(LOGIN_PATH, headers=browser(cookie, **spoofed))
        assert response.status_code == 200, response.text
        assert response.json()["identity"] == name("frank")
        assert self.links(server, name("root")) == []
        for sent in verifier.requests:
            assert "remote-user" not in sent.headers
            assert "x-forwarded-user" not in sent.headers

    def test_without_the_named_cookie_the_verifier_is_not_asked(
        self, server, verifier, service
    ):
        response = server.get(LOGIN_PATH, headers={"Cookie": "unrelated=1"})
        assert response.status_code == 401
        assert verifier.requests == []

    # -- denials and failures --------------------------------------------

    @pytest.mark.parametrize(
        "answer",
        [
            (401, {}, b""),
            (403, {"Remote-User": "would-be"}, b""),
            (302, {"Location": "https://auth.example.com/?rd=x"}, b""),
        ],
    )
    def test_a_denial_signs_no_one_in(
        self, server, verifier_instance, local_http_server, set_env, answer
    ):
        set_env("REGISTRATION_MODE", "open")
        denying = local_http_server({VERIFY_PATH: answer})
        verifier_instance(
            {
                "verify_url": denying.base_url + VERIFY_PATH,
                "forward_cookies": SESSION_COOKIE,
            }
        )
        response = server.get(LOGIN_PATH, headers=browser("anything"))
        assert response.status_code == 401
        assert "token" not in response.json()
        assert self.links(server, "would-be") == []

    @pytest.mark.parametrize(
        "answer",
        [
            (500, {"Remote-User": "would-be"}, b""),
            (404, {"Remote-User": "would-be"}, b""),
            (200, {}, b""),
            (200, {"Remote-User": " "}, b""),
            (200, {"Remote-User": "would-be\x7fhidden"}, b""),
        ],
    )
    def test_a_verifier_failure_fails_closed(
        self, server, verifier_instance, local_http_server, set_env, answer
    ):
        set_env("REGISTRATION_MODE", "open")
        failing = local_http_server({VERIFY_PATH: answer})
        verifier_instance(
            {
                "verify_url": failing.base_url + VERIFY_PATH,
                "forward_cookies": SESSION_COOKIE,
            }
        )
        response = server.get(LOGIN_PATH, headers=browser("anything"))
        assert response.status_code == 502
        assert "token" not in response.json()
        assert self.links(server, "would-be") == []

    def test_a_repeated_identity_header_fails_closed(
        self, server, verifier_instance, local_http_server, set_env
    ):
        """Two Remote-User values: which one is meant is unknowable."""
        set_env("REGISTRATION_MODE", "open")

        def twice(request: ReceivedRequest) -> Answer:
            # http.server sends each header given; a list cannot repeat a
            # key, so the second value rides in a differently-cased name.
            return 200, {"Remote-User": "first", "remote-user": "second"}, b""

        doubled = local_http_server({VERIFY_PATH: twice})
        verifier_instance(
            {
                "verify_url": doubled.base_url + VERIFY_PATH,
                "forward_cookies": SESSION_COOKIE,
            }
        )
        response = server.get(LOGIN_PATH, headers=browser("anything"))
        assert response.status_code == 502
        assert self.links(server, "first") == self.links(server, "second") == []

    def test_a_verifier_timeout_fails_closed(
        self, server, verifier_instance, local_http_server, set_env
    ):
        set_env("REGISTRATION_MODE", "open")

        def slow(request: ReceivedRequest) -> Answer:
            time.sleep(SLOW_VERIFIER_SECONDS)
            return 200, {"Remote-User": "too-late"}, b""

        sleepy = local_http_server({VERIFY_PATH: slow})
        verifier_instance(
            {
                "verify_url": sleepy.base_url + VERIFY_PATH,
                "forward_cookies": SESSION_COOKIE,
                "timeout_seconds": SHORT_TIMEOUT_SECONDS,
            }
        )
        started = time.monotonic()
        response = server.get(LOGIN_PATH, headers=browser("anything"))
        assert response.status_code == 502
        assert time.monotonic() - started < SLOW_VERIFIER_SECONDS
        assert self.links(server, "too-late") == []

    def test_an_unreachable_verifier_fails_closed(
        self, server, verifier_instance, set_env
    ):
        set_env("REGISTRATION_MODE", "open")
        host = f"127.0.0.1:{_closed_port()}"
        set_env("EGRESS_ALLOWED_HOSTS", host)
        verifier_instance(
            {
                "verify_url": f"http://{host}{VERIFY_PATH}",
                "forward_cookies": SESSION_COOKIE,
            }
        )
        assert server.get(LOGIN_PATH, headers=browser("anything")).status_code == 502

    def test_the_ssrf_guard_applies_to_the_verifier(
        self, server, authelia, verifier_instance, local_http_server, set_env
    ):
        """A loopback verifier not in EGRESS_ALLOWED_HOSTS is refused."""
        set_env("REGISTRATION_MODE", "open")
        set_env("EGRESS_ALLOWED_HOSTS", "")
        hidden = local_http_server({VERIFY_PATH: authelia}, allow=False)
        verifier_instance(
            {
                "verify_url": hidden.base_url + VERIFY_PATH,
                "forward_cookies": SESSION_COOKIE,
            }
        )
        response = server.get(LOGIN_PATH, headers=browser(authelia.login("grace")))
        assert response.status_code == 502
        assert hidden.requests == []

    def test_one_browser_denied_does_not_lock_out_the_next(
        self, server, authelia, service, set_env
    ):
        """A denial is one browser's missing session; it puts the verifier
        in no cooldown."""
        set_env("REGISTRATION_MODE", "open")
        for _ in range(3):
            assert server.get(LOGIN_PATH, headers=browser("stale")).status_code == 401
        response = server.get(LOGIN_PATH, headers=browser(authelia.login("heidi")))
        assert response.status_code == 200, response.text

    # -- which verifiers are trusted -------------------------------------

    def test_a_verifier_a_user_configured_is_not_trusted(
        self, server, authelia, verifier, verifier_instance, user_b, set_env
    ):
        """Whoever runs the verifier says who everyone is, so only an
        operator's instance may sign anyone in."""
        set_env("REGISTRATION_MODE", "open")
        verifier_instance(
            {
                "verify_url": verifier.base_url + VERIFY_PATH,
                "forward_cookies": SESSION_COOKIE,
            },
            creator_id=user_b.id,
        )
        response = server.get(LOGIN_PATH, headers=browser(authelia.login("ivan")))
        assert response.status_code == 503
        assert verifier.requests == []

    def test_a_deleted_verifier_signs_no_one_in(
        self, server, authelia, service, set_env
    ):
        """ROOT's reads include deleted rows, so deletion must be checked."""
        set_env("REGISTRATION_MODE", "open")
        cookie = authelia.login("judy")
        assert server.get(LOGIN_PATH, headers=browser(cookie)).status_code == 200
        ProviderInstanceManager(
            model_registry=server.app.state.model_registry,
            requester_id=env("ROOT_ID"),
        ).delete(id=service.id)
        assert server.get(LOGIN_PATH, headers=browser(cookie)).status_code == 503

    def test_a_disabled_verifier_signs_no_one_in(
        self, server, authelia, service, set_env
    ):
        set_env("REGISTRATION_MODE", "open")
        registry = server.app.state.model_registry
        ProviderInstanceModel.DB(registry.DB.manager.Base).update(
            requester_id=env("ROOT_ID"),
            model_registry=registry,
            id=service.id,
            new_properties={"enabled": False},
        )
        response = server.get(LOGIN_PATH, headers=browser(authelia.login("judy")))
        assert response.status_code == 503

    def test_a_misconfigured_verifier_fails_closed(
        self, server, authelia, verifier, verifier_instance, set_env
    ):
        """Forwarding the whole Cookie header would hand the verifier this
        app's own session; the instance is refused, not half-used."""
        set_env("REGISTRATION_MODE", "open")
        verifier_instance(
            {
                "verify_url": verifier.base_url + VERIFY_PATH,
                "forward_headers": "Cookie",
            }
        )
        response = server.get(LOGIN_PATH, headers=browser(authelia.login("judy")))
        assert response.status_code == 503
        assert verifier.requests == []

    def test_several_verifiers_are_chosen_by_name(
        self, server, authelia, verifier, verifier_instance, set_env
    ):
        """Each verifier vouches for its own names: the same name from two
        is two people, and neither verifier sees the other's sign-ins."""
        set_env("REGISTRATION_MODE", "open")
        settings = {
            "verify_url": verifier.base_url + VERIFY_PATH,
            "forward_cookies": SESSION_COOKIE,
        }
        first = verifier_instance(settings)
        second = verifier_instance(settings)
        cookie = authelia.login("mallory")
        assert server.get(LOGIN_PATH, headers=browser(cookie)).status_code == 400
        unknown = server.get(f"{LOGIN_PATH}?instance=nope", headers=browser(cookie))
        assert unknown.status_code == 404
        assert verifier.requests == []
        one = server.get(f"{LOGIN_PATH}?instance={first.name}", headers=browser(cookie))
        two = server.get(
            f"{LOGIN_PATH}?instance={second.name}", headers=browser(cookie)
        )
        assert one.status_code == two.status_code == 200
        assert one.json()["provider_instance_id"] == str(first.id)
        assert two.json()["provider_instance_id"] == str(second.id)
        assert one.json()["user_id"] != two.json()["user_id"]
        assert len(self.links(server, name("mallory"))) == 2

    # -- registration mode and linking -----------------------------------

    @pytest.mark.parametrize("mode", ["closed", "invite"])
    def test_an_unknown_identity_is_refused_unless_registration_is_open(
        self, server, authelia, service, set_env, mode
    ):
        set_env("REGISTRATION_MODE", mode)
        cookie = authelia.login("niaj", email=generate_test_email("fa_niaj"))
        assert server.get(LOGIN_PATH, headers=browser(cookie)).status_code == 403
        assert self.links(server, name("niaj")) == []

    def test_an_untrusted_email_never_reaches_an_existing_account(
        self, server, authelia, service, set_env
    ):
        """Without trusted_for_email the verifier's email is a claim: it
        neither links the account that has it nor is copied to a new one."""
        set_env("REGISTRATION_MODE", "open")
        email = generate_test_email("fa_victim")
        victim = create_user(server, email=email)
        cookie = authelia.login("olivia", email=email)
        response = server.get(LOGIN_PATH, headers=browser(cookie))
        assert response.status_code == 200, response.text
        assert response.json()["user_id"] != victim.id
        assert response.json()["user"].get("email") in (None, "")

    def test_a_trusted_email_links_the_account_that_has_it(
        self, server, authelia, verifier, verifier_instance, set_env
    ):
        """Even while registration is closed, and whatever the case of the
        address the verifier sends (registration stores it normalized)."""
        set_env("REGISTRATION_MODE", "closed")
        email = generate_test_email("fa_peggy")
        user = create_user(server, email=email)
        verifier_instance(
            {
                "verify_url": verifier.base_url + VERIFY_PATH,
                "forward_cookies": SESSION_COOKIE,
                "trusted_for_email": "true",
            }
        )
        cookie = authelia.login("peggy", email=email.upper())
        response = server.get(LOGIN_PATH, headers=browser(cookie))
        assert response.status_code == 200, response.text
        assert response.json()["user_id"] == user.id
        [link] = self.links(server, name("peggy"))
        assert link.user_id == user.id

    @pytest.mark.parametrize("internal", INTERNAL_ACCOUNTS)
    def test_no_trusted_email_reaches_an_internal_account(
        self, server, authelia, verifier, verifier_instance, set_env, internal
    ):
        """ROOT's seeded email is predictable; a verifier trusted for emails
        asserting it must not sign in as the superuser."""
        set_env("REGISTRATION_MODE", "open")
        verifier_instance(
            {
                "verify_url": verifier.base_url + VERIFY_PATH,
                "forward_cookies": SESSION_COOKIE,
                "trusted_for_email": "true",
            }
        )
        registry = server.app.state.model_registry
        with internal_account_email(registry, env(internal)) as email:
            cookie = authelia.login(f"usurper-{internal}", email=email)
            response = server.get(LOGIN_PATH, headers=browser(cookie))
        assert response.status_code == 403, response.text
        assert self.links(server, name(f"usurper-{internal}")) == []

    @pytest.mark.parametrize("internal", INTERNAL_ACCOUNTS)
    def test_nothing_links_to_an_internal_account(self, server, service, internal):
        with pytest.raises(Exception) as refused:
            self.root_links(server).create(
                user_id=env(internal),
                provider_instance_id=str(service.id),
                identity=f"internal-{uuid.uuid4().hex[:8]}",
            )
        assert getattr(refused.value, "status_code", None) == 403

    def test_an_identity_linked_to_root_signs_no_one_in(
        self, server, authelia, service
    ):
        """A link to ROOT written beneath the manager (by an older version,
        or directly) still issues no session."""
        registry = server.app.state.model_registry
        ForwardAuthIdentityModel.DB(registry.DB.manager.Base).create(
            requester_id=env("ROOT_ID"),
            model_registry=registry,
            provider_instance_id=str(service.id),
            identity=name("rooted"),
            user_id=env("ROOT_ID"),
        )
        response = server.get(LOGIN_PATH, headers=browser(authelia.login("rooted")))
        assert response.status_code == 403, response.text

    def test_an_identity_root_linked_signs_in_while_registration_is_closed(
        self, server, authelia, service, set_env
    ):
        set_env("REGISTRATION_MODE", "closed")
        user = create_user(server, email=generate_test_email("fa_rupert"))
        link = self.root_links(server).create(
            user_id=user.id,
            provider_instance_id=str(service.id),
            identity=name("rupert"),
        )
        cookie = authelia.login("rupert")
        response = server.get(LOGIN_PATH, headers=browser(cookie))
        assert response.status_code == 200, response.text
        assert response.json()["user_id"] == user.id
        # The link is the user's: they see it, and may remove it.
        mine = server.get(LINKS_PATH, headers={"Authorization": f"Bearer {user.jwt}"})
        assert mine.status_code == 200, mine.text
        assert [i["identity"] for i in mine.json()["forward_auth_identities"]] == [
            name("rupert")
        ]
        # The sign-in stamped the link; the removal names the version listed.
        (listed,) = mine.json()["forward_auth_identities"]
        removed = server.delete(
            f"{LINKS_PATH}/{link.id}",
            headers={"Authorization": f"Bearer {user.jwt}", **if_match_of(listed)},
        )
        assert removed.status_code == 204, removed.text
        assert server.get(LOGIN_PATH, headers=browser(cookie)).status_code == 403

    def test_a_disabled_account_cannot_sign_in(
        self, server, authelia, service, set_env
    ):
        set_env("REGISTRATION_MODE", "closed")
        user = create_user(server, email=generate_test_email("fa_disabled"))
        self.root_links(server).create(
            user_id=user.id,
            provider_instance_id=str(service.id),
            identity=name("sybil"),
        )
        registry = server.app.state.model_registry
        UserModel.DB(registry.DB.manager.Base).update(
            requester_id=env("ROOT_ID"),
            model_registry=registry,
            id=user.id,
            new_properties={"active": False},
        )
        response = server.get(LOGIN_PATH, headers=browser(authelia.login("sybil")))
        assert response.status_code == 403

    def test_a_user_cannot_claim_an_identity(self, server, service, user_b):
        """Claiming another's identity would take over their sign-in."""
        claim = server.post(
            LINKS_PATH,
            json={
                "forward_auth_identity": {
                    "provider_instance_id": str(service.id),
                    "identity": name("trent"),
                }
            },
            headers={"Authorization": f"Bearer {user_b.jwt}"},
        )
        assert claim.status_code in (404, 405)
        manager = ForwardAuthIdentityManager(
            model_registry=server.app.state.model_registry, requester_id=user_b.id
        )
        with pytest.raises(Exception) as refused:
            manager.create(
                user_id=user_b.id,
                provider_instance_id=str(service.id),
                identity=name("trent"),
            )
        assert getattr(refused.value, "status_code", None) == 403

    def test_an_identity_links_to_one_user(self, server, service, user_b, admin_a):
        identity = f"linked-once-{uuid.uuid4().hex[:8]}"
        self.root_links(server).create(
            user_id=user_b.id, provider_instance_id=str(service.id), identity=identity
        )
        with pytest.raises(Exception) as refused:
            self.root_links(server).create(
                user_id=admin_a.id,
                provider_instance_id=str(service.id),
                identity=identity,
            )
        assert getattr(refused.value, "status_code", None) == 409

    def test_a_link_names_a_live_operator_verifier(self, server, user_b):
        with pytest.raises(Exception) as refused:
            self.root_links(server).create(
                user_id=user_b.id,
                provider_instance_id=str(uuid.uuid4()),
                identity=f"nowhere-{uuid.uuid4().hex[:8]}",
            )
        assert getattr(refused.value, "status_code", None) == 404

    def test_a_link_never_moves_to_another_user(self, server, service, user_b, admin_a):
        identity = f"fixed-{uuid.uuid4().hex[:8]}"
        link = self.root_links(server).create(
            user_id=user_b.id, provider_instance_id=str(service.id), identity=identity
        )
        with pytest.raises(Exception) as refused:
            self.root_links(server).update(id=link.id, user_id=admin_a.id)
        assert getattr(refused.value, "status_code", None) == 403
        [kept] = self.links(server, identity)
        assert kept.user_id == user_b.id

    def test_users_see_only_their_own_links(self, server, service, user_b, admin_a):
        identity = f"private-{uuid.uuid4().hex[:8]}"
        self.root_links(server).create(
            user_id=admin_a.id, provider_instance_id=str(service.id), identity=identity
        )
        theirs = server.get(
            LINKS_PATH, headers={"Authorization": f"Bearer {user_b.jwt}"}
        )
        assert theirs.status_code == 200, theirs.text
        assert identity not in [
            i["identity"] for i in theirs.json()["forward_auth_identities"]
        ]

    async def test_the_ability_lists_the_requesters_links(
        self, server, service, user_b
    ):
        identity = f"ability-{uuid.uuid4().hex[:8]}"
        self.root_links(server).create(
            user_id=user_b.id, provider_instance_id=str(service.id), identity=identity
        )
        listed = await EXT_ForwardAuthConsumer.forward_auth_linked_identities(
            requester_id=user_b.id
        )
        assert identity in [link["identity"] for link in listed]
