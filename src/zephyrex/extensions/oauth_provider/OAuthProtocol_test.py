# SPDX-License-Identifier: AGPL-3.0-or-later
"""The wire rules: redirect URIs, PKCE, scopes, client credentials."""

import base64
import hashlib

import pytest

from zephyrex.extensions.oauth_provider.OAuthProtocol import (
    APPLICATION_NATIVE,
    APPLICATION_WEB,
    OAuthError,
    digest,
    digest_matches,
    half_hash,
    is_s256_challenge,
    json_list,
    mint_secret,
    parse_basic_credentials,
    parse_scope,
    pkce_verifies,
    redirect_uri_registered,
    s256,
    string_parameters,
    validate_redirect_uri,
    with_query,
)

VERIFIER = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
# RFC 7636 Appendix B.
CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


class TestRedirectURIs:
    @pytest.mark.parametrize(
        "uri",
        [
            "https://app.example.com/cb",
            "https://app.example.com:8443/cb?tenant=a",
        ],
    )
    def test_https_uris_register(self, uri):
        assert validate_redirect_uri(uri, APPLICATION_WEB) == uri

    @pytest.mark.parametrize(
        "uri",
        [
            "http://app.example.com/cb",
            "http://127.0.0.1/cb",
            "https://app.example.com/cb#fragment",
            "https://user:pw@app.example.com/cb",
            "https://*.example.com/cb",
            "/relative/cb",
            "javascript:alert(1)",
            "",
        ],
    )
    def test_a_web_client_cannot_register(self, uri):
        """Hole: neither replaced server checked a redirect URI's scheme, so
        plain-http and fragment URIs registered and leaked codes."""
        with pytest.raises(ValueError):
            validate_redirect_uri(uri, APPLICATION_WEB)

    @pytest.mark.parametrize("uri", ["http://127.0.0.1/cb", "http://[::1]:8080/cb"])
    def test_a_native_client_may_use_http_loopback(self, uri):
        assert validate_redirect_uri(uri, APPLICATION_NATIVE) == uri

    def test_a_native_client_may_not_use_http_localhost_names(self):
        with pytest.raises(ValueError):
            validate_redirect_uri("http://localhost/cb", APPLICATION_NATIVE)

    def test_matching_is_exact(self):
        registered = ["https://app.example.com/cb"]
        assert redirect_uri_registered(registered, "https://app.example.com/cb")
        for near_miss in (
            "https://app.example.com/cb/",
            "https://app.example.com/cb?x=1",
            "https://app.example.com/CB",
            "https://app.example.com:443/cb",
            "https://evil.example.com/cb",
        ):
            assert not redirect_uri_registered(registered, near_miss)

    def test_a_loopback_redirect_matches_on_any_port(self):
        registered = ["http://127.0.0.1/cb"]
        assert redirect_uri_registered(registered, "http://127.0.0.1:51004/cb")
        assert not redirect_uri_registered(registered, "http://127.0.0.1:51004/other")
        assert not redirect_uri_registered(registered, "http://127.0.0.2:51004/cb")

    def test_with_query_keeps_the_existing_query(self):
        assert (
            with_query("https://a.example/cb?tenant=x", {"code": "c d", "state": None})
            == "https://a.example/cb?tenant=x&code=c+d"
        )


class TestPKCE:
    def test_rfc_7636_example(self):
        assert s256(VERIFIER) == CHALLENGE
        assert pkce_verifies(VERIFIER, CHALLENGE)

    def test_wrong_or_missing_verifier(self):
        assert not pkce_verifies("x" * 43, CHALLENGE)
        assert not pkce_verifies(None, CHALLENGE)
        assert not pkce_verifies("", CHALLENGE)

    def test_a_plain_verifier_equal_to_the_challenge_fails(self):
        """Hole: both replaced servers accepted ``plain``, where the verifier
        is the challenge itself."""
        assert not pkce_verifies(CHALLENGE, CHALLENGE)

    def test_verifier_length_and_alphabet(self):
        assert not pkce_verifies("a" * 42, s256("a" * 42))
        assert not pkce_verifies("a" * 129, s256("a" * 129))
        assert not pkce_verifies("a" * 42 + "!", s256("a" * 42 + "!"))

    def test_challenge_shape(self):
        assert is_s256_challenge(CHALLENGE)
        assert not is_s256_challenge("short")
        assert not is_s256_challenge(CHALLENGE + "=")


class TestScopes:
    def test_tokens_in_order_once(self):
        assert parse_scope("openid email openid") == ["openid", "email"]
        assert parse_scope(None) == []

    @pytest.mark.parametrize("raw", ["openid  email", " openid", 'a"b', "a\\b"])
    def test_malformed_scope(self, raw):
        with pytest.raises(OAuthError) as caught:
            parse_scope(raw)
        assert caught.value.error == "invalid_scope"


class TestCredentials:
    def test_secrets_are_long_random_and_prefixed(self):
        first, second = mint_secret("p_"), mint_secret("p_")
        assert first.startswith("p_") and first != second
        assert len(first) >= 43

    def test_digest_is_sha256_and_compares(self):
        assert digest("abc") == hashlib.sha256(b"abc").hexdigest()
        assert digest_matches("abc", digest("abc"))
        assert not digest_matches("abd", digest("abc"))

    def test_basic_credentials_are_form_decoded(self):
        raw = base64.b64encode(b"my%20client:p%3Ass+word").decode()
        assert parse_basic_credentials(f"Basic {raw}") == ("my client", "p:ss word")

    def test_non_basic_header_is_not_credentials(self):
        assert parse_basic_credentials("Bearer abc") is None
        assert parse_basic_credentials(None) is None

    @pytest.mark.parametrize("value", ["!!!", base64.b64encode(b"no-colon").decode()])
    def test_malformed_basic_credentials(self, value):
        with pytest.raises(OAuthError) as caught:
            parse_basic_credentials(f"Basic {value}")
        assert caught.value.error == "invalid_client"
        assert caught.value.status_code == 401


class TestParameters:
    def test_strings_only_and_empty_is_absent(self):
        assert string_parameters({"a": "1", "b": "", "c": None}) == {"a": "1"}

    def test_non_string_is_invalid_request(self):
        with pytest.raises(OAuthError) as caught:
            string_parameters({"code": ["a", "b"]})
        assert caught.value.error == "invalid_request"

    def test_overlong_is_invalid_request(self):
        with pytest.raises(OAuthError):
            string_parameters({"state": "x" * 4096})

    def test_half_hash(self):
        full = hashlib.sha256(b"token").digest()
        expected = base64.urlsafe_b64encode(full[:16]).rstrip(b"=").decode()
        assert half_hash("token") == expected

    def test_json_list(self):
        assert json_list('["a", "b"]') == ["a", "b"]
        assert json_list(None) == []
        with pytest.raises(ValueError):
            json_list('{"a": 1}')
