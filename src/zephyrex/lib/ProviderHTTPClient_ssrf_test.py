"""Tests for the H-2 SSRF guard in ``ProviderHTTPClient``.

The guard refuses outbound HTTP requests to internal/loopback/link-local
addresses (incl. cloud IMDS at 169.254.169.254) before any auth header,
rate token, or pool slot is spent. Operators expand the allowlist via
``EGRESS_ALLOWED_HOSTS``; setting ``DISABLE_SSRF_GUARD=1`` bypasses the
guard entirely (documented escape hatch for tests / proxy-fronted
deployments).
"""

from __future__ import annotations

import pytest


def _validator():
    from zephyrex.lib.ProviderHTTPClient import (
        SSRFGuardError,
        validate_outbound_url,
    )

    return SSRFGuardError, validate_outbound_url


def test_ssrf_blocks_loopback_literal():
    SSRFGuardError, validate = _validator()
    with pytest.raises(SSRFGuardError):
        validate("http://127.0.0.1/secret")


def test_ssrf_blocks_imds_literal():
    """The cloud-provider IMDS endpoint is the canonical SSRF target."""
    SSRFGuardError, validate = _validator()
    with pytest.raises(SSRFGuardError):
        validate("http://169.254.169.254/latest/meta-data/")


def test_ssrf_blocks_rfc1918():
    SSRFGuardError, validate = _validator()
    for url in (
        "http://10.1.2.3/",
        "http://172.16.0.1/",
        "http://192.168.1.1/",
    ):
        with pytest.raises(SSRFGuardError):
            validate(url)


def test_ssrf_blocks_ipv6_loopback():
    SSRFGuardError, validate = _validator()
    with pytest.raises(SSRFGuardError):
        validate("http://[::1]/secret")


def test_ssrf_blocks_unsupported_scheme():
    SSRFGuardError, validate = _validator()
    with pytest.raises(SSRFGuardError):
        validate("file:///etc/passwd")
    with pytest.raises(SSRFGuardError):
        validate("gopher://1.2.3.4/_smtp")


def test_ssrf_blocks_missing_host():
    SSRFGuardError, validate = _validator()
    with pytest.raises(SSRFGuardError):
        validate("http:///no-host")


def test_ssrf_allows_public_literal():
    _, validate = _validator()
    # 8.8.8.8 is public and not in any blocked range.
    validate("https://8.8.8.8/dns-query")


def test_ssrf_allowlist_via_env(monkeypatch):
    """``EGRESS_ALLOWED_HOSTS`` accepts a private host when a deployment
    legitimately needs to talk to a known internal service."""
    SSRFGuardError, validate = _validator()
    with pytest.raises(SSRFGuardError):
        validate("http://10.5.5.5/")

    monkeypatch.setenv("EGRESS_ALLOWED_HOSTS", "10.5.5.5")
    validate("http://10.5.5.5/")


def test_ssrf_disabled_via_kill_switch(monkeypatch):
    _, validate = _validator()
    monkeypatch.setenv("DISABLE_SSRF_GUARD", "1")
    # Even literal loopback is allowed when the guard is disabled — the
    # documented escape hatch for tests and proxy-fronted deployments.
    validate("http://127.0.0.1/")
