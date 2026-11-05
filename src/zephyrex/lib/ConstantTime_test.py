# SPDX-License-Identifier: AGPL-3.0-or-later
"""#283: secret and signature checks compared client-supplied ``str`` values
with ``hmac.compare_digest``, which raises ``TypeError`` for non-ASCII text.
A header byte >= 0x80 (Starlette decodes headers as latin-1), a cursor or a
claim then turned a refusal into a 500. Each check now refuses it."""

import time

import pytest

from zephyrex.extensions.email.PRV_ProxmoxMailGateway_EMail import (
    ProxmoxMailGatewayProvider,
)
from zephyrex.extensions.email.PRV_SMTP2Go_EMail import Smtp2goProvider
from zephyrex.extensions.email.PRV_Stalwart_EMail import StalwartProvider
from zephyrex.extensions.oauth_provider.OAuthProtocol import pkce_verifies, s256
from zephyrex.extensions.Paginators import (
    InvalidPaginationError,
    decode_token,
    encode_token,
    query_hash,
)
from zephyrex.extensions.proxy_auth_provider.ProxyHeaders import (
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    verify_identity,
)
from zephyrex.lib.ConstantTime import secure_equals
from zephyrex.lib.SessionCookies import (
    CSRF_COOKIE,
    CSRF_HEADER,
    SessionCookieMiddleware,
)

NON_ASCII = "caféÿ"


class TestSecureEquals:
    @pytest.mark.parametrize(
        "left,right,equal",
        [
            ("abc", "abc", True),
            ("abc", "abd", False),
            (NON_ASCII, NON_ASCII, True),
            ("abc", NON_ASCII, False),
            (NON_ASCII, "abc", False),
            (b"abc", "abc", True),
            ("", "", True),
            ("", NON_ASCII, False),
            ("\ud800", "x", False),
        ],
    )
    def test_compares_without_raising(self, left, right, equal):
        assert secure_equals(left, right) is equal


class TestSitesRefuseNonASCII:
    def test_csrf_header(self):
        present = {
            b"cookie": f"{CSRF_COOKIE}=token".encode(),
            CSRF_HEADER.encode(): NON_ASCII.encode("latin-1"),
        }
        assert SessionCookieMiddleware._csrf_matches(present) is False

    def test_smtp2go_bearer(self, monkeypatch):
        monkeypatch.setenv("SMTP2GO_WEBHOOK_SECRET", "sek")
        headers = {"Authorization": f"Bearer {NON_ASCII}"}
        assert Smtp2goProvider.verify_signature(headers, b"{}") is False

    @pytest.mark.parametrize(
        "provider,env_name,header",
        [
            (
                ProxmoxMailGatewayProvider,
                "PMG_WEBHOOK_SECRET",
                "X-PMG-Signature",
            ),
            (StalwartProvider, "STALWART_WEBHOOK_SECRET", "X-Stalwart-Signature"),
        ],
    )
    def test_webhook_signatures(self, monkeypatch, provider, env_name, header):
        monkeypatch.setenv(env_name, "sek")
        assert provider.verify_signature({header: NON_ASCII}, b"{}") is False
        sha_prefixed = {header: "sha256=" + NON_ASCII}
        assert provider.verify_signature(sha_prefixed, b"{}") is False

    def test_forward_auth_signature(self):
        now = int(time.time())
        headers = {TIMESTAMP_HEADER: str(now), SIGNATURE_HEADER: NON_ASCII}
        assert not verify_identity(b"s" * 32, "GET", "/x", headers, 60, now=now)

    def test_pagination_signature(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET", "x" * 32)
        token = encode_token("cursor", 10, query_hash({}))
        body = token.rsplit(".", 1)[0]
        with pytest.raises(InvalidPaginationError):
            decode_token(f"{body}.{NON_ASCII}")

    def test_pkce_challenge(self):
        verifier = "v" * 43
        assert pkce_verifies(verifier, s256(verifier)) is True
        assert pkce_verifies(verifier, NON_ASCII) is False
