"""Unit tests for the credential vault primitives (Item 32, Item 50)."""

from __future__ import annotations

import logging
import os
from typing import Iterator

import pytest

from lib.Credentials import (
    CredentialRef,
    RedactingFilter,
    Secret,
    SecretValue,
    _CACHE,
    cache_bust_on_auth_rejection,
    clear_bad,
    invalidate,
    is_marked_bad,
    mark_bad,
    redact,
    register_secret,
    validate_environment_credentials,
)


@pytest.fixture(autouse=True)
def _reset_cache() -> Iterator[None]:
    _CACHE.reset()
    yield
    _CACHE.reset()


@pytest.fixture
def isolated_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    for k in list(os.environ.keys()):
        if k.startswith("CRED_TEST_") or k in ("APP_ENV", "FRAMEWORK_FERNET_KEY"):
            monkeypatch.delenv(k, raising=False)
    yield monkeypatch


@pytest.mark.unit
def test_secret_str_redacts_in_repr():
    s = Secret("supersecret")
    assert "supersecret" not in repr(s)
    assert "supersecret" not in str(s)
    assert s.get_secret_value() == "supersecret"


@pytest.mark.unit
def test_secret_value_generic_wrapper():
    s = SecretValue({"jwk": "key"})
    assert "jwk" not in repr(s)
    assert s.reveal() == {"jwk": "key"}
    assert s.get_secret_value() == {"jwk": "key"}


@pytest.mark.unit
def test_credential_ref_env_tier_test_suffix(isolated_env):
    isolated_env.setenv("APP_ENV", "development")
    isolated_env.setenv("CRED_TEST_API_KEY_TEST", "test-value")
    ref = CredentialRef(tier="env", path="CRED_TEST_API_KEY")
    assert ref.resolve() == "test-value"


@pytest.mark.unit
def test_credential_ref_env_tier_live_suffix(isolated_env):
    isolated_env.setenv("APP_ENV", "production")
    isolated_env.setenv("CRED_TEST_API_KEY_LIVE", "live-value")
    ref = CredentialRef(tier="env", path="CRED_TEST_API_KEY")
    assert ref.resolve() == "live-value"


@pytest.mark.unit
def test_credential_ref_env_tier_bare_fallback(isolated_env):
    isolated_env.setenv("APP_ENV", "development")
    isolated_env.setenv("CRED_TEST_API_KEY", "bare-value")
    ref = CredentialRef(tier="env", path="CRED_TEST_API_KEY")
    assert ref.resolve() == "bare-value"


@pytest.mark.unit
def test_credential_ref_unresolvable_raises(isolated_env):
    isolated_env.setenv("APP_ENV", "development")
    ref = CredentialRef(tier="env", path="DOES_NOT_EXIST_XYZ")
    with pytest.raises(RuntimeError):
        ref.resolve()


@pytest.mark.unit
def test_credential_ref_caches_value(isolated_env):
    isolated_env.setenv("APP_ENV", "development")
    isolated_env.setenv("CRED_TEST_API_KEY_TEST", "first")
    ref = CredentialRef(tier="env", path="CRED_TEST_API_KEY")
    assert ref.resolve() == "first"
    isolated_env.setenv("CRED_TEST_API_KEY_TEST", "second")
    # Cached, should still return first
    assert ref.resolve() == "first"
    invalidate(ref)
    assert ref.resolve() == "second"


@pytest.mark.unit
def test_mark_bad_blocks_resolution(isolated_env):
    isolated_env.setenv("APP_ENV", "development")
    isolated_env.setenv("CRED_TEST_API_KEY_TEST", "value")
    ref = CredentialRef(tier="env", path="CRED_TEST_API_KEY")
    ref.resolve()
    mark_bad(ref)
    assert is_marked_bad(ref)
    with pytest.raises(RuntimeError, match="marked bad"):
        ref.resolve()
    clear_bad(ref)
    assert not is_marked_bad(ref)


@pytest.mark.unit
def test_redact_replaces_registered_secrets():
    register_secret("abc123")
    assert redact("hello abc123 world") == "hello *** world"


@pytest.mark.unit
def test_redacting_filter_scrubs_log_record():
    register_secret("topsecret")
    rec = logging.LogRecord(
        name="x", level=logging.INFO, pathname="", lineno=0,
        msg="leaking topsecret here", args=(), exc_info=None,
    )
    f = RedactingFilter()
    assert f.filter(rec)
    assert "topsecret" not in rec.getMessage()


@pytest.mark.unit
def test_validate_environment_refuses_test_in_production(isolated_env):
    isolated_env.setenv("CRED_TEST_API_KEY_TEST", "test-only")
    ref = CredentialRef(tier="env", path="CRED_TEST_API_KEY")
    with pytest.raises(RuntimeError, match="_TEST_ credentials"):
        validate_environment_credentials("production", {"stripe": ref})


@pytest.mark.unit
def test_validate_environment_passes_with_live(isolated_env):
    isolated_env.setenv("CRED_TEST_API_KEY_LIVE", "live")
    ref = CredentialRef(tier="env", path="CRED_TEST_API_KEY")
    validate_environment_credentials("production", {"stripe": ref})


@pytest.mark.unit
def test_validate_environment_skips_non_production(isolated_env):
    isolated_env.setenv("CRED_TEST_API_KEY_TEST", "test")
    ref = CredentialRef(tier="env", path="CRED_TEST_API_KEY")
    validate_environment_credentials("development", {"stripe": ref})
    validate_environment_credentials("staging", {"stripe": ref})


@pytest.mark.unit
def test_cache_bust_on_auth_rejection_returns_new_value(isolated_env):
    """Re-resolved value differs -> credential rotated externally;
    return the new value and clear the bad marker."""
    isolated_env.setenv("APP_ENV", "development")
    isolated_env.setenv("CRED_TEST_API_KEY_TEST", "v1")
    ref = CredentialRef(tier="env", path="CRED_TEST_API_KEY")
    assert ref.resolve() == "v1"
    isolated_env.setenv("CRED_TEST_API_KEY_TEST", "v2")
    fresh = cache_bust_on_auth_rejection(ref)
    assert fresh == "v2"
    assert not is_marked_bad(ref)


@pytest.mark.unit
def test_cache_bust_on_auth_rejection_marks_bad_when_identical(isolated_env):
    """Re-resolved value equals cached -> source tier did NOT rotate;
    mark bad and raise so the rotation system advances."""
    isolated_env.setenv("APP_ENV", "development")
    isolated_env.setenv("CRED_TEST_API_KEY_TEST", "stale")
    ref = CredentialRef(tier="env", path="CRED_TEST_API_KEY")
    assert ref.resolve() == "stale"
    with pytest.raises(RuntimeError, match="re-resolved identical"):
        cache_bust_on_auth_rejection(ref)
    assert is_marked_bad(ref)
