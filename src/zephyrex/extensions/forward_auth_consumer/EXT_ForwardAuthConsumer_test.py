# SPDX-License-Identifier: AGPL-3.0-or-later
"""The forward_auth_consumer extension's declarations and pure helpers."""

import dataclasses
from typing import Any, Dict, List, Optional

import pytest

from zephyrex.extensions.ExternalErrors import PermanentExternalError
from zephyrex.extensions.forward_auth_consumer.BLL_ForwardAuthConsumer import (
    ForwardAuthIdentityManager,
    ForwardAuthIdentityModel,
)
from zephyrex.extensions.forward_auth_consumer.EXT_ForwardAuthConsumer import (
    EXT_ForwardAuthConsumer,
)
from zephyrex.extensions.forward_auth_consumer.PRV_ForwardAuthVerifier import (
    OriginalRequest,
    PRV_ForwardAuthVerifier,
    Verifier,
    forwardable_headers,
    header_values,
    name_list,
    parse_flag,
    parse_timeout,
    selected_cookies,
)


def _verifier(**overrides: Any) -> Verifier:
    base = Verifier(
        instance_name="authelia",
        url="https://auth.example.com/api/authz/forward-auth",
        cookies=("authelia_session",),
        headers=frozenset(),
        user_header="Remote-User",
        email_header="Remote-Email",
        name_header="Remote-Name",
        trusted_for_email=False,
        timeout_seconds=5.0,
    )
    return dataclasses.replace(base, **overrides)


def _original(
    cookie: Optional[str] = None, headers: Optional[Dict[str, str]] = None
) -> OriginalRequest:
    return OriginalRequest(
        cookie_header=cookie,
        headers=headers or {},
        client_host="203.0.113.7",
        url="http://app.example.com/v1/auth/forward-auth/login?x=1",
        method="GET",
    )


def _answer(*pairs: str) -> Dict[str, List[str]]:
    return header_values([(pairs[i], pairs[i + 1]) for i in range(0, len(pairs), 2)])


class TestDeclarations:
    def test_metadata(self):
        assert EXT_ForwardAuthConsumer.name == "forward_auth_consumer"
        assert EXT_ForwardAuthConsumer.version == "2.0.0"
        assert EXT_ForwardAuthConsumer.get_abilities() == {
            "forward_auth_linked_identities"
        }

    def test_its_provider_and_no_third_party_requirements(self):
        assert EXT_ForwardAuthConsumer.providers == [PRV_ForwardAuthVerifier]
        assert EXT_ForwardAuthConsumer.pip_requirements() == []

    def test_every_setting_reads_from_the_environment_too(self):
        for setting in PRV_ForwardAuthVerifier.instance_settings:
            assert setting.env in EXT_ForwardAuthConsumer._env

    def test_the_link_model_and_its_routes(self):
        fields = set(ForwardAuthIdentityModel.model_fields)
        assert {"provider_instance_id", "identity", "user_id", "last_login_at"} <= (
            fields
        )
        assert {r.value for r in ForwardAuthIdentityManager.routes_to_register} == {
            "get",
            "list",
            "search",
            "delete",
        }


class TestSettings:
    def test_name_lists_keep_order_once(self):
        assert name_list("a, b c,,a") == ("a", "b", "c")
        assert name_list(None) == ()

    @pytest.mark.parametrize(
        "header",
        [
            "Cookie",
            "Host",
            "X-Forwarded-User",
            "X-Original-URL",
            "Forwarded",
            "X/Y",
            "X:Y",
        ],
    )
    def test_reserved_or_invalid_headers_cannot_be_forwarded(self, header):
        with pytest.raises(PermanentExternalError):
            forwardable_headers(header)

    def test_credential_headers_can_be_forwarded(self):
        assert forwardable_headers("Authorization, Proxy-Authorization") == {
            "authorization",
            "proxy-authorization",
        }

    @pytest.mark.parametrize(
        "value, flag", [("true", True), ("YES", True), ("0", False), ("", False)]
    )
    def test_flags(self, value, flag):
        assert parse_flag(value, "trusted_for_email") is flag

    def test_a_flag_that_is_neither_is_refused(self):
        with pytest.raises(PermanentExternalError):
            parse_flag("maybe", "trusted_for_email")

    @pytest.mark.parametrize("value", ["0", "-1", "31", "soon"])
    def test_timeouts_out_of_range_are_refused(self, value):
        with pytest.raises(PermanentExternalError):
            parse_timeout(value)

    def test_the_default_timeout(self):
        assert parse_timeout(None) == 5.0


class TestSubrequest:
    def test_only_named_cookies_are_kept_verbatim(self):
        header = "zx_session=ours; authelia_session=a=b; Authelia_Session=x"
        assert selected_cookies(header, ("authelia_session",)) == (
            "authelia_session=a=b"
        )

    def test_only_named_headers_and_the_url_description_are_sent(self):
        verifier = _verifier(headers=frozenset({"authorization"}))
        sent = verifier.subrequest_headers(
            _original(
                cookie="authelia_session=s; other=o",
                headers={"Authorization": "Bearer t", "X-Api-Key": "k"},
            )
        )
        assert sent == {
            "Authorization": "Bearer t",
            "Cookie": "authelia_session=s",
            "X-Forwarded-Method": "GET",
            "X-Forwarded-Proto": "http",
            "X-Forwarded-Host": "app.example.com",
            "X-Forwarded-Uri": "/v1/auth/forward-auth/login?x=1",
            "X-Original-URL": "http://app.example.com/v1/auth/forward-auth/login?x=1",
            "X-Original-Method": "GET",
            "X-Forwarded-For": "203.0.113.7",
        }

    def test_a_request_without_the_credentials_carries_none(self):
        verifier = _verifier()
        assert not verifier.carries_credentials(_original(cookie="other=1"))
        assert verifier.carries_credentials(_original(cookie="authelia_session=s"))


class TestIdentity:
    def test_the_identity_email_and_name(self):
        verified = _verifier().identity_from(
            _answer(
                "Remote-User", "alice", "Remote-Email", "a@x.org", "Remote-Name", "A"
            )
        )
        assert (verified.identity, verified.email, verified.display_name) == (
            "alice",
            "a@x.org",
            "A",
        )

    @pytest.mark.parametrize(
        "answer",
        [
            _answer(),
            _answer("Remote-User", ""),
            _answer("Remote-User", "a\x00b"),
            _answer("Remote-User", "x" * 257),
            _answer("Remote-User", "a", "remote-user", "b"),
        ],
    )
    def test_no_single_usable_identity_fails_closed(self, answer):
        with pytest.raises(PermanentExternalError):
            _verifier().identity_from(answer)

    def test_a_malformed_email_is_dropped(self):
        verified = _verifier().identity_from(
            _answer("Remote-User", "alice", "Remote-Email", "not an email")
        )
        assert verified.identity == "alice" and verified.email is None
