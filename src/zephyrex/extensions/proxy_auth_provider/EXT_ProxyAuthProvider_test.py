# SPDX-License-Identifier: AGPL-3.0-or-later
"""The proxy against a real upstream on loopback, in a real app: who gets
through, what the upstream receives (identity, never credentials), what
comes back, and the bounds on bodies, time and destinations."""

import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, Iterator, List, Tuple

import pytest

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.proxy_auth_provider.BLL_ProxyAuthProvider import (
    ResponseTooLarge,
)
from zephyrex.extensions.proxy_auth_provider.EXT_ProxyAuthProvider import (
    EXT_ProxyAuthProvider,
)
from zephyrex.extensions.proxy_auth_provider.PRV_Upstream import (
    PRV_Upstream_ProxyAuthProvider,
    UpstreamMisconfigured,
    base_url,
    byte_count,
    positive_number,
    team_ids,
)
from zephyrex.extensions.proxy_auth_provider.ProxyHeaders import verify_identity
from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Providers import (
    ProviderInstanceManager,
    ProviderInstanceSettingManager,
    ProviderManager,
)

SECRET = "a-shared-secret-of-at-least-32-bytes!"
Answer = Tuple[int, Dict[str, str], List[bytes], bool]


@dataclass(frozen=True)
class Received:
    method: str
    path: str
    headers: Dict[str, str]
    raw_headers: List[Tuple[str, str]]
    body: bytes


@dataclass
class Upstream:
    host: str
    routes: Dict[str, Callable[[Received], Answer]] = field(default_factory=dict)
    received: List[Received] = field(default_factory=list)

    @property
    def base_url(self) -> str:
        return f"http://{self.host}"


def read_body(handler: BaseHTTPRequestHandler) -> bytes:
    if handler.headers.get("Transfer-Encoding", "").lower() == "chunked":
        body = b""
        while True:
            size = int(handler.rfile.readline().strip(), 16)
            if size == 0:
                handler.rfile.readline()
                return body
            body += handler.rfile.read(size)
            handler.rfile.readline()
    length = int(handler.headers.get("Content-Length") or 0)
    return handler.rfile.read(length) if length else b""


def handler_for(upstream: Upstream) -> type:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def answer(self) -> None:
            request = Received(
                self.command,
                self.path,
                {k.lower(): v for k, v in self.headers.items()},
                [(k.lower(), v) for k, v in self.headers.items()],
                read_body(self),
            )
            upstream.received.append(request)
            route = upstream.routes.get(self.path.split("?")[0])
            status, headers, chunks, chunked = (
                route(request) if route else (404, {}, [b"no such page"], False)
            )
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            if chunked:
                self.send_header("Transfer-Encoding", "chunked")
            else:
                self.send_header("Content-Length", str(sum(map(len, chunks))))
            self.end_headers()
            try:
                for chunk in chunks:
                    if chunked:
                        self.wfile.write(
                            f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n"
                        )
                    else:
                        self.wfile.write(chunk)
                if chunked:
                    self.wfile.write(b"0\r\n\r\n")
            except (BrokenPipeError, ConnectionResetError):
                pass

        do_GET = do_DELETE = answer

        def log_message(self, *args: object) -> None:
            pass

    return Handler


def plain(
    body: bytes = b"ok", status: int = 200, **headers: str
) -> Callable[[Received], Answer]:
    return lambda _: (status, dict(headers), [body], False)


def auth(user: Any) -> Dict[str, str]:
    return {"Authorization": f"Bearer {user.jwt}"}


@pytest.fixture(scope="module")
def upstream_server() -> Iterator[Upstream]:
    upstream = Upstream("")
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(upstream))
    upstream.host = f"127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield upstream
    server.shutdown()
    server.server_close()


@pytest.fixture
def upstream(upstream_server: Upstream, monkeypatch: pytest.MonkeyPatch) -> Upstream:
    """The loopback upstream, allowed egress for this test, with no routes
    and nothing received yet."""
    monkeypatch.setenv("EGRESS_ALLOWED_HOSTS", upstream_server.host)
    upstream_server.routes.clear()
    upstream_server.received.clear()
    return upstream_server


class TestSettings:
    def test_a_base_url_is_http_with_a_host_and_nothing_else(self):
        assert base_url(" https://up.internal/app/ ") == "https://up.internal/app/"
        for bad in ("", "ftp://up", "http://", "http://u:p@up/", "http://up/?a=1"):
            with pytest.raises(UpstreamMisconfigured):
                base_url(bad)

    def test_numbers_and_teams(self):
        assert positive_number("2.5", "t", 10) == 2.5
        for bad in ("0", "-1", "nan", "inf", "11", "x"):
            with pytest.raises(UpstreamMisconfigured):
                positive_number(bad, "t", 10)
        assert byte_count("10", "b") == 10
        for bad in ("0", "-1", "1.5", ""):
            with pytest.raises(UpstreamMisconfigured):
                byte_count(bad, "b")
        assert team_ids(" a, b c ") == frozenset({"a", "b", "c"})


class TestProxy(ExtensionServerMixin):
    extension_class = EXT_ProxyAuthProvider

    @pytest.fixture
    def declare(self, server):
        """Declare an upstream as ROOT; returns its name and id."""
        registry = server.app.state.model_registry
        root = env("ROOT_ID")

        def _declare(url: str, **settings: str) -> Tuple[str, str]:
            provider = ProviderManager(model_registry=registry, requester_id=root).get(
                name=PRV_Upstream_ProxyAuthProvider.name
            )
            name = f"up-{uuid.uuid4().hex[:12]}"
            created = ProviderInstanceManager(
                model_registry=registry, requester_id=root
            ).create(name=name, provider_id=provider.id, scope="root")
            values = ProviderInstanceSettingManager(
                model_registry=registry, requester_id=root
            )
            for key, value in {"url": url, **settings}.items():
                values.create(provider_instance_id=created.id, key=key, value=value)
            return name, str(created.id)

        return _declare

    # Who gets through.

    def test_identity_goes_up_and_credentials_never_do(
        self, server, declare, upstream, user_b, team_b
    ):
        upstream.routes["/app/page"] = plain(b"hello", **{"Content-Type": "text/plain"})
        name, _ = declare(upstream.base_url + "/app/")
        response = server.get(
            f"/v1/proxy/{name}/page?x=1",
            headers={
                **auth(user_b),
                "X-Forwarded-User": "root",
                "X-Forwarded-Groups": "admins",
                "Remote-User": "root",
                "X-Real-IP": "10.0.0.1",
                "Cookie": "theme=dark",
            },
        )
        assert response.status_code == 200, response.text
        assert response.content == b"hello"
        [seen] = upstream.received
        assert (seen.method, seen.path) == ("GET", "/app/page?x=1")
        assert seen.headers["x-forwarded-user"] == str(user_b.id)
        assert seen.headers["x-forwarded-email"] == user_b.email
        assert seen.headers["x-forwarded-groups"] == str(team_b.id)
        assert seen.headers["x-forwarded-prefix"] == f"/v1/proxy/{name}"
        assert [v for k, v in seen.raw_headers if k == "x-forwarded-user"] == [
            str(user_b.id)
        ]
        assert "authorization" not in seen.headers
        assert "remote-user" not in seen.headers
        assert "x-real-ip" not in seen.headers
        assert seen.headers["cookie"] == "theme=dark"
        assert "x-forwarded-auth-signature" not in seen.headers

    def test_the_session_cookie_signs_in_and_stays_here(
        self, server, declare, upstream, user_b
    ):
        upstream.routes["/"] = plain()
        name, _ = declare(upstream.base_url)
        server.cookies.clear()
        response = server.get(
            f"/v1/proxy/{name}/", headers={"Cookie": f"zx_session={user_b.jwt}; a=b"}
        )
        assert response.status_code == 200, response.text
        [seen] = upstream.received
        assert seen.headers["cookie"] == "a=b"
        assert "authorization" not in seen.headers
        assert user_b.jwt not in str(seen.raw_headers)

    def test_without_a_session_nothing_is_sent(self, server, declare, upstream):
        upstream.routes["/"] = plain()
        name, _ = declare(upstream.base_url)
        server.cookies.clear()
        assert server.get(f"/v1/proxy/{name}/").status_code == 401
        bogus = {"Authorization": "Bearer not-a-token"}
        assert server.get(f"/v1/proxy/{name}/", headers=bogus).status_code == 401
        assert upstream.received == []

    def test_only_a_declared_upstream_exists(self, server, upstream, user_b, admin_a):
        upstream.routes["/"] = plain()
        assert server.get("/v1/proxy/nothing/", headers=auth(user_b)).status_code == 404
        provider_id = (
            ProviderManager(
                model_registry=server.app.state.model_registry,
                requester_id=env("ROOT_ID"),
            )
            .get(name=PRV_Upstream_ProxyAuthProvider.name)
            .id
        )
        name = f"mine-{uuid.uuid4().hex[:8]}"
        created = server.post(
            "/v1/provider/instance",
            json={
                "provider_instance": {
                    "name": name,
                    "provider_id": provider_id,
                    "created_by_user_id": env("ROOT_ID"),
                }
            },
            headers=auth(admin_a),
        )
        assert created.status_code == 201, created.text
        instance_id = created.json()["provider_instance"]["id"]
        ProviderInstanceSettingManager(
            model_registry=server.app.state.model_registry,
            requester_id=env("ROOT_ID"),
        ).create(provider_instance_id=instance_id, key="url", value=upstream.base_url)
        # A user's own instance never makes the proxy a way into the network.
        assert (
            server.get(f"/v1/proxy/{name}/", headers=auth(admin_a)).status_code == 404
        )
        assert upstream.received == []

    def test_a_disabled_or_deleted_upstream_is_gone(
        self, server, declare, upstream, user_b
    ):
        upstream.routes["/"] = plain()
        registry = server.app.state.model_registry
        instances = ProviderInstanceManager(
            model_registry=registry, requester_id=env("ROOT_ID")
        )
        disabled, disabled_id = declare(upstream.base_url)
        instances.update(id=disabled_id, enabled=False)
        deleted, deleted_id = declare(upstream.base_url)
        instances.delete(id=deleted_id)
        for name in (disabled, deleted):
            assert (
                server.get(f"/v1/proxy/{name}/", headers=auth(user_b)).status_code
                == 404
            )
        assert upstream.received == []

    def test_an_upstream_naming_teams_admits_only_their_members(
        self, server, declare, upstream, user_b, team_b, admin_a
    ):
        upstream.routes["/"] = plain()
        name, _ = declare(upstream.base_url, allowed_teams=f"other, {team_b.id}")
        assert server.get(f"/v1/proxy/{name}/", headers=auth(user_b)).status_code == 200
        assert (
            server.get(f"/v1/proxy/{name}/", headers=auth(admin_a)).status_code == 403
        )
        assert len(upstream.received) == 1

    def test_a_misconfigured_upstream_is_a_bad_gateway(
        self, server, declare, upstream, user_b
    ):
        name, _ = declare("ftp://" + upstream.host)
        assert server.get(f"/v1/proxy/{name}/", headers=auth(user_b)).status_code == 502
        short, _ = declare(upstream.base_url, signing_secret="short")
        assert (
            server.get(f"/v1/proxy/{short}/", headers=auth(user_b)).status_code == 502
        )
        assert upstream.received == []

    # What the upstream receives.

    def test_the_identity_is_signed_for_this_request(
        self, server, declare, upstream, user_b
    ):
        # The path reaches the upstream encoded as the client sent it.
        upstream.routes["/app/a%20b/data.json"] = plain(b"{}")
        name, _ = declare(upstream.base_url + "/app", signing_secret=SECRET)
        response = server.get(
            f"/v1/proxy/{name}/a%20b/data.json?q=1", headers=auth(user_b)
        )
        assert response.status_code == 200, response.text
        [seen] = upstream.received
        assert seen.path == "/app/a%20b/data.json?q=1"
        assert verify_identity(SECRET.encode(), "GET", seen.path, seen.headers, 60)
        assert not verify_identity(
            SECRET.encode(), "DELETE", seen.path, seen.headers, 60
        )

    def test_a_path_cannot_leave_the_base_path(self, server, declare, upstream, user_b):
        name, _ = declare(upstream.base_url + "/app/")
        for path in ("a/%2e%2e/%2e%2e/admin", "a%2F..%2F..%2Fadmin"):
            response = server.get(f"/v1/proxy/{name}/{path}", headers=auth(user_b))
            assert response.status_code == 400, path
        assert upstream.received == []

    def test_a_delete_streams_its_body_up(self, server, declare, upstream, user_b):
        upstream.routes["/items/7"] = plain(b"", status=204)
        name, _ = declare(upstream.base_url)

        def body() -> Iterator[bytes]:
            yield b"first,"
            yield b"second"

        response = server.request(
            "DELETE", f"/v1/proxy/{name}/items/7", content=body(), headers=auth(user_b)
        )
        assert response.status_code == 204, response.text
        [seen] = upstream.received
        assert (seen.method, seen.body) == ("DELETE", b"first,second")

    # What comes back.

    def test_any_media_type_is_relayed(self, server, declare, upstream, user_b):
        """The upstream's media types are its own: an Accept naming none of
        this API's formats, or a path ending .xml, is relayed untouched."""
        upstream.routes["/app/logo.xml"] = plain(
            b"<svg/>", **{"Content-Type": "image/svg+xml"}
        )
        name, _ = declare(upstream.base_url + "/app/")
        answer = server.get(
            f"/v1/proxy/{name}/logo.xml",
            headers={**auth(user_b), "Accept": "image/svg+xml"},
        )
        assert (answer.status_code, answer.content) == (200, b"<svg/>")
        assert answer.headers["content-type"] == "image/svg+xml"

    def test_status_headers_and_redirects_come_back(
        self, server, declare, upstream, user_b
    ):
        upstream.routes["/app/missing"] = plain(b"gone", status=404)
        upstream.routes["/app/old"] = lambda _: (
            302,
            {
                "Location": upstream.base_url + "/app/new?x=1",
                "Set-Cookie": "zx_session=forged",
            },
            [b""],
            False,
        )
        upstream.routes["/app/cookie"] = plain(b"", **{"Set-Cookie": "app=1; Path=/"})
        name, _ = declare(upstream.base_url + "/app/")
        missing = server.get(f"/v1/proxy/{name}/missing", headers=auth(user_b))
        assert (missing.status_code, missing.content) == (404, b"gone")
        moved = server.get(
            f"/v1/proxy/{name}/old", headers=auth(user_b), follow_redirects=False
        )
        assert moved.status_code == 302
        assert moved.headers["location"] == f"/v1/proxy/{name}/new?x=1"
        assert "zx_session" not in moved.headers.get("set-cookie", "")
        cookie = server.get(f"/v1/proxy/{name}/cookie", headers=auth(user_b))
        assert cookie.headers["set-cookie"] == "app=1; Path=/"

    # Bounds.

    def test_a_declared_body_over_a_cap_is_refused(
        self, server, declare, upstream, user_b
    ):
        upstream.routes["/big"] = plain(b"x" * 2048)
        upstream.routes["/del"] = plain(b"")
        name, _ = declare(
            upstream.base_url, max_response_bytes="1024", max_request_bytes="8"
        )
        assert (
            server.get(f"/v1/proxy/{name}/big", headers=auth(user_b)).status_code == 502
        )
        refused = server.request(
            "DELETE",
            f"/v1/proxy/{name}/del",
            content=b"0123456789",
            headers=auth(user_b),
        )
        assert refused.status_code == 413
        assert [r.path for r in upstream.received] == ["/big"]

    def test_a_streamed_request_body_over_the_cap_is_refused(
        self, server, declare, upstream, user_b
    ):
        upstream.routes["/del"] = plain(b"")
        name, _ = declare(upstream.base_url, max_request_bytes="8")

        def body() -> Iterator[bytes]:
            yield b"01234"
            yield b"56789"

        response = server.request(
            "DELETE", f"/v1/proxy/{name}/del", content=body(), headers=auth(user_b)
        )
        assert response.status_code == 413

    def test_a_streamed_answer_over_the_cap_is_cut_off(
        self, server, declare, upstream, user_b
    ):
        upstream.routes["/del"] = lambda _: (200, {}, [b"x" * 600, b"y" * 600], True)
        name, _ = declare(upstream.base_url, max_response_bytes="1000")
        with pytest.raises(ResponseTooLarge):
            server.request("DELETE", f"/v1/proxy/{name}/del", headers=auth(user_b))

    def test_a_slow_upstream_times_out(self, server, declare, upstream, user_b):
        def slow(_: Received) -> Answer:
            time.sleep(3)
            return 200, {}, [b"late"], False

        upstream.routes["/slow"] = slow
        name, _ = declare(upstream.base_url, timeout_seconds="0.5")
        response = server.get(f"/v1/proxy/{name}/slow", headers=auth(user_b))
        assert response.status_code == 504

    def test_an_address_the_egress_guard_refuses_is_never_reached(
        self, server, declare, upstream, user_b, monkeypatch
    ):
        upstream.routes["/"] = plain()
        monkeypatch.delenv("EGRESS_ALLOWED_HOSTS")
        name, _ = declare(upstream.base_url)
        assert server.get(f"/v1/proxy/{name}/", headers=auth(user_b)).status_code == 502
        assert upstream.received == []

    def test_an_unreachable_upstream_is_a_bad_gateway(
        self, server, declare, user_b, monkeypatch
    ):
        monkeypatch.setenv("EGRESS_ALLOWED_HOSTS", "127.0.0.1:9")
        name, _ = declare("http://127.0.0.1:9")
        assert server.get(f"/v1/proxy/{name}/", headers=auth(user_b)).status_code == 502

    def test_only_get_and_delete_are_proxied(self, server, declare, upstream, user_b):
        name, _ = declare(upstream.base_url)
        for method in ("POST", "PUT", "PATCH"):
            response = server.request(
                method, f"/v1/proxy/{name}/x", json={}, headers=auth(user_b)
            )
            assert response.status_code == 405, method
        assert upstream.received == []
