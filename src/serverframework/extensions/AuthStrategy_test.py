"""Unit tests for pluggable AuthStrategy primitives (Item 10)."""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone

import pytest

from serverframework.extensions.AuthStrategy import (
    APIKeyAuth,
    AWSSigV4Auth,
    AuthStrategy,
    AuthStrategyRegistry,
    BasicAuth,
    JWTBearerAuth,
    MTLSAuth,
    OAuth2Auth,
)


@pytest.mark.unit
def test_api_key_auth_default_header():
    s = APIKeyAuth("k123")
    assert s.headers_for() == {"Authorization": "Bearer k123"}
    assert s.params_for() == {}
    assert s.body_modifier(None, {"a": 1}) == {"a": 1}
    s.refresh_if_needed()  # no-op


@pytest.mark.unit
def test_api_key_auth_custom_header():
    s = APIKeyAuth("k", header_name="X-Api-Key", header_prefix="")
    assert s.headers_for() == {"X-Api-Key": "k"}


@pytest.mark.unit
def test_oauth2_auth_headers_and_update():
    s = OAuth2Auth(access_token="t1")
    assert s.headers_for() == {"Authorization": "Bearer t1"}
    s.update_token("t2")
    assert s.headers_for() == {"Authorization": "Bearer t2"}


@pytest.mark.unit
def test_oauth2_refresh_skips_when_not_expired():
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    s = OAuth2Auth(access_token="t", expires_at=future)
    s.refresh_if_needed()
    assert s.headers_for() == {"Authorization": "Bearer t"}


@pytest.mark.unit
def test_jwt_bearer_auth():
    s = JWTBearerAuth("jwt-token")
    assert s.headers_for() == {"Authorization": "Bearer jwt-token"}


@pytest.mark.unit
def test_basic_auth_encoding():
    s = BasicAuth("alice", "pw")
    h = s.headers_for()["Authorization"]
    assert h.startswith("Basic ")
    decoded = base64.b64decode(h[len("Basic "):]).decode()
    assert decoded == "alice:pw"


@pytest.mark.unit
def test_mtls_auth_carries_paths():
    s = MTLSAuth("/c.pem", "/k.pem")
    assert s.cert_path == "/c.pem"
    assert s.key_path == "/k.pem"
    assert s.headers_for() == {}


@pytest.mark.unit
def test_aws_sigv4_returns_amz_headers():
    s = AWSSigV4Auth("AKID", "SECRET", "us-east-1", "s3")
    headers = s.headers_for()
    assert "X-Amz-Date" in headers
    assert "Authorization" in headers
    assert "AWS4-HMAC-SHA256" in headers["Authorization"]


@pytest.mark.unit
def test_registry_built_in_strategies():
    names = AuthStrategyRegistry.names()
    for required in ("api_key", "oauth2", "jwt_bearer", "basic", "mtls", "aws_sigv4"):
        assert required in names


@pytest.mark.unit
def test_registry_get_constructs_strategy():
    s = AuthStrategyRegistry.get("api_key", {"api_key": "z"})
    assert isinstance(s, APIKeyAuth)
    assert s.headers_for() == {"Authorization": "Bearer z"}


@pytest.mark.unit
def test_registry_unknown_raises():
    with pytest.raises(KeyError):
        AuthStrategyRegistry.get("does_not_exist", {})


@pytest.mark.unit
def test_registry_register_custom_strategy():
    class Custom(AuthStrategy):
        def headers_for(self, requester_id=None):
            return {"X-Custom": "1"}
        def params_for(self, requester_id=None):
            return {}
        def body_modifier(self, requester_id, body):
            return body
        def refresh_if_needed(self):
            return None

    AuthStrategyRegistry.register("custom_test", lambda payload: Custom())
    try:
        s = AuthStrategyRegistry.get("custom_test", {})
        assert s.headers_for() == {"X-Custom": "1"}
    finally:
        AuthStrategyRegistry.unregister("custom_test")
