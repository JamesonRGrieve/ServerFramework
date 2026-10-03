# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the proxy passes up and back: credentials and forgeable identity
removed, its own identity added and signed, redirects pointed back through
it, and a path that cannot leave the upstream's base path."""

import pytest

from zephyrex.extensions.proxy_auth_provider.ProxyHeaders import (
    EMAIL_HEADER,
    FRAMEWORK_COOKIE_PREFIX,
    GROUPS_HEADER,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    USER_HEADER,
    Identity,
    UnsafePath,
    client_response_headers,
    declared_length,
    header_value,
    identity_headers,
    public_location,
    upstream_request_headers,
    upstream_target,
    verify_identity,
)
from zephyrex.lib.SessionCookies import CSRF_COOKIE, SESSION_COOKIE

SECRET = b"s" * 32
IDENTITY = Identity("u-1", "ada", "ada@example.com", "Ada Lovelace", ("t-1", "t-2"))


def raw(*pairs):
    return [(name.encode(), value.encode()) for name, value in pairs]


def as_dict(headers):
    return {name.decode().lower(): value.decode() for name, value in headers}


class TestRequestHeaders:
    def test_credentials_and_forgeable_identity_never_go_up(self):
        kept = as_dict(
            upstream_request_headers(
                raw(
                    ("Authorization", "Bearer token"),
                    ("X-API-Key", "key"),
                    ("X-CSRF-Token", "csrf"),
                    ("X-Forwarded-User", "root"),
                    ("X-Forwarded-Groups", "admins"),
                    ("Forwarded", "for=1.2.3.4"),
                    ("X-Real-IP", "1.2.3.4"),
                    ("Remote-User", "root"),
                    ("X-Remote-User", "root"),
                    ("X-Auth-Request-Email", "root@example.com"),
                    ("X-WebAuth-User", "root"),
                    ("Host", "this-server"),
                    ("Accept", "text/html"),
                )
            )
        )
        assert kept == {"accept": "text/html"}

    def test_only_this_servers_cookies_are_removed(self):
        assert SESSION_COOKIE.startswith(FRAMEWORK_COOKIE_PREFIX)
        assert CSRF_COOKIE.startswith(FRAMEWORK_COOKIE_PREFIX)
        kept = as_dict(
            upstream_request_headers(
                raw(("Cookie", f"{SESSION_COOKIE}=jwt; theme=dark; {CSRF_COOKIE}=c"))
            )
        )
        assert kept == {"cookie": "theme=dark"}
        assert upstream_request_headers(raw(("Cookie", f"{SESSION_COOKIE}=jwt"))) == []

    def test_hop_by_hop_and_connection_named_headers_stay_here(self):
        kept = as_dict(
            upstream_request_headers(
                raw(
                    ("Connection", "keep-alive, X-Secret-Hop"),
                    ("X-Secret-Hop", "1"),
                    ("Keep-Alive", "timeout=5"),
                    ("TE", "trailers"),
                    ("Upgrade", "websocket"),
                    ("Proxy-Authorization", "Basic x"),
                    ("Content-Length", "3"),
                )
            )
        )
        assert kept == {"content-length": "3"}


class TestResponseHeaders:
    def test_this_servers_cookies_cannot_be_set_by_the_upstream(self):
        kept = client_response_headers(
            raw(
                ("Set-Cookie", f"{SESSION_COOKIE}=forged; Path=/"),
                ("Set-Cookie", "app=1; Path=/"),
                ("Transfer-Encoding", "chunked"),
                ("Content-Type", "text/plain"),
            ),
            "http://up.internal/",
            "/v1/proxy/up",
        )
        assert [(n.decode().lower(), v.decode()) for n, v in kept] == [
            ("set-cookie", "app=1; Path=/"),
            ("content-type", "text/plain"),
        ]

    @pytest.mark.parametrize(
        "location, expected",
        [
            ("http://up.internal/app/login?next=1", "/v1/proxy/up/login?next=1"),
            ("/app/login", "/v1/proxy/up/login"),
            ("/app", "/v1/proxy/up/"),
            ("/elsewhere", "/elsewhere"),
            ("login", "login"),
            ("https://sso.example.com/auth", "https://sso.example.com/auth"),
            ("http://up.internal:8080/app/x", "http://up.internal:8080/app/x"),
        ],
    )
    def test_redirects_to_the_upstream_come_back_through_the_proxy(
        self, location, expected
    ):
        assert (
            public_location(location, "http://up.internal/app/", "/v1/proxy/up")
            == expected
        )


class TestIdentity:
    def test_values_with_control_characters_are_dropped(self):
        assert header_value("a\r\nX-Forwarded-User: root") is None
        assert header_value("  ") is None
        assert header_value(" Ada ") == "Ada"

    def test_unsigned_headers(self):
        sent = as_dict(identity_headers(IDENTITY, "GET", "/x", None))
        assert sent[USER_HEADER.lower()] == "u-1"
        assert sent[EMAIL_HEADER.lower()] == "ada@example.com"
        assert sent[GROUPS_HEADER.lower()] == "t-1,t-2"
        assert TIMESTAMP_HEADER.lower() not in sent

    def test_a_signature_verifies_for_its_request_only(self):
        sent = as_dict(identity_headers(IDENTITY, "GET", "/x?a=1", SECRET, now=1000))
        assert verify_identity(SECRET, "GET", "/x?a=1", sent, 60, now=1030)
        assert not verify_identity(SECRET, "DELETE", "/x?a=1", sent, 60, now=1030)
        assert not verify_identity(SECRET, "GET", "/y", sent, 60, now=1030)
        assert not verify_identity(b"t" * 32, "GET", "/x?a=1", sent, 60, now=1030)
        assert not verify_identity(SECRET, "GET", "/x?a=1", sent, 60, now=1100)

    def test_a_changed_identity_fails_the_signature(self):
        sent = as_dict(identity_headers(IDENTITY, "GET", "/x", SECRET, now=1000))
        for name in (USER_HEADER, EMAIL_HEADER, GROUPS_HEADER):
            forged = {**sent, name.lower(): "root"}
            assert not verify_identity(SECRET, "GET", "/x", forged, 60, now=1000)
        assert not verify_identity(
            SECRET, "GET", "/x", {**sent, SIGNATURE_HEADER.lower(): ""}, 60, now=1000
        )

    def test_unicode_travels_as_utf8_and_verifies(self):
        person = Identity("u-2", None, None, "Zoë Ångström", ())
        headers = identity_headers(person, "GET", "/", SECRET, now=5)
        assert (b"X-Forwarded-Name", "Zoë Ångström".encode()) in headers
        received = {n.decode(): v.decode("utf-8") for n, v in headers}
        assert verify_identity(SECRET, "GET", "/", received, 60, now=5)


class TestTarget:
    def test_the_path_and_query_go_below_the_base_path(self):
        assert upstream_target("http://up.internal/app/", "a%20b/c.json", "q=1&r") == (
            "http://up.internal/app/a%20b/c.json?q=1&r",
            "/app/a%20b/c.json?q=1&r",
        )
        assert upstream_target("https://up.internal", "", "") == (
            "https://up.internal/",
            "/",
        )

    @pytest.mark.parametrize(
        "rest",
        ["..", "a/../b", "a/%2e%2e/b", "%2E", "a%2F..%2Fb", "%2F..", "a%5cb", "a%0d"],
    )
    def test_a_path_cannot_climb_out(self, rest):
        with pytest.raises(UnsafePath):
            upstream_target("http://up.internal/app/", rest, "")

    def test_an_encoded_slash_stays_encoded(self):
        url, _ = upstream_target("http://up.internal/app/", "a%2Fb", "")
        assert url == "http://up.internal/app/a%2Fb"

    def test_declared_length(self):
        assert declared_length(raw(("Content-Length", "12"))) == 12
        assert declared_length(raw(("Content-Length", "x"))) is None
        assert declared_length([]) is None
