"""Unit tests for the auth_session extension's hook wiring.

These tests exercise the registration contract — they do not spin up the
full ModelRegistry/database stack. Integration tests for the SessionModel
+ SessionManager surface live alongside the parent ``BLL_Auth_test``
suite, which still imports ``SessionModel``/``SessionManager`` through the
PEP 562 ``__getattr__`` shim in core ``BLL_Auth``.
"""

from __future__ import annotations

import pytest


def _reset_hooks() -> None:
    from serverframework.logic.BLL_Auth import reset_session_hooks

    reset_session_hooks()


@pytest.fixture(autouse=True)
def _hook_reset_around_each_test():
    """Each test starts with empty hooks and restores them on teardown so
    later tests in the suite still see auth_session's registrations."""
    _reset_hooks()
    yield
    _reset_hooks()
    # Re-register with the extension's defaults so unrelated tests that
    # depend on the loaded extension keep working under pytest-xdist.
    import serverframework.extensions.auth_session.BLL_Session as session_mod
    from serverframework.logic.BLL_Auth import register_session_hooks

    register_session_hooks(
        issue_session=session_mod.issue_session,
        enforce_not_revoked=session_mod.enforce_not_revoked,
        manager_factory=session_mod.session_manager_factory,
        revoke_user_sessions=session_mod.revoke_user_sessions,
    )


def test_register_session_hooks_partial_update():
    """Each hook is independently overridable so tests / operators can
    swap a single implementation without re-registering the rest."""
    from serverframework.logic.BLL_Auth import (
        _session_hooks,
        register_session_hooks,
    )

    sentinel = lambda **kwargs: "sentinel-key"
    register_session_hooks(issue_session=sentinel)

    assert _session_hooks["issue_session"] is sentinel
    assert _session_hooks["enforce_not_revoked"] is None
    assert _session_hooks["manager_factory"] is None
    assert _session_hooks["revoke_user_sessions"] is None


def test_extension_on_load_registers_all_four_hooks():
    """The extension's ``on_load`` wires every hook the core uses."""
    from serverframework.extensions.auth_session.EXT_Session import (
        AuthSessionExtension,
    )
    from serverframework.logic.BLL_Auth import _session_hooks

    AuthSessionExtension.on_load()

    assert _session_hooks["issue_session"] is not None
    assert _session_hooks["enforce_not_revoked"] is not None
    assert _session_hooks["manager_factory"] is not None
    assert _session_hooks["revoke_user_sessions"] is not None


def test_pep562_lazy_import_resolves_session_model():
    """``BLL_Auth.SessionModel`` resolves to the extension's class via
    the PEP 562 ``__getattr__`` shim. This is the migration-compat path
    for code that still does ``from BLL_Auth import SessionModel``."""
    from serverframework.logic import BLL_Auth as core
    from serverframework.extensions.auth_session.BLL_Session import (
        SessionModel as ExtSessionModel,
    )

    assert core.SessionModel is ExtSessionModel
    assert core.SessionManager is ExtSessionModel.Manager


def test_enforce_not_revoked_requires_jti_when_no_extension():
    """Without the extension, JWT verification still requires ``jti`` so
    enabling ``auth_session`` later is fully effective for outstanding
    tokens. The framework does not silently downgrade to "no jti, no gate"."""
    from fastapi import HTTPException

    from serverframework.logic.BLL_Auth import UserManager

    with pytest.raises(HTTPException) as excinfo:
        UserManager._enforce_session_not_revoked(
            payload={}, model_registry=None, db=None
        )
    assert excinfo.value.status_code == 401
    assert "jti" in str(excinfo.value.detail).lower()


def test_enforce_not_revoked_calls_hook_when_registered():
    """When the extension's hook is registered, the core dispatches to
    it with the JWT payload, registry, and db connection."""
    from serverframework.logic.BLL_Auth import (
        UserManager,
        register_session_hooks,
    )

    captured = {}

    def fake_enforce(payload, model_registry, db):
        captured["payload"] = payload
        captured["mr"] = model_registry
        captured["db"] = db

    register_session_hooks(enforce_not_revoked=fake_enforce)

    payload = {"jti": "deadbeef"}
    UserManager._enforce_session_not_revoked(
        payload=payload, model_registry="MR", db="DB"
    )

    assert captured == {"payload": payload, "mr": "MR", "db": "DB"}


def test_generate_jwt_token_uses_issue_hook_when_registered():
    """When ``issue_session`` is wired, ``generate_jwt_token`` dispatches
    through it instead of generating a key locally. The session_key
    returned by the hook becomes the JWT's ``jti``."""
    import os

    os.environ.setdefault("JWT_SECRET", "a" * 64)
    os.environ.setdefault("JWT_AUDIENCE", "test-aud")
    os.environ.setdefault("JWT_ISSUER", "test-iss")

    from serverframework.lib.Dependencies import jwt
    from serverframework.lib.Environment import env
    from serverframework.logic.BLL_Auth import (
        UserManager,
        register_session_hooks,
    )

    captured = {}

    def fake_issue(**kwargs) -> str:
        captured.update(kwargs)
        return "fake-session-key"

    register_session_hooks(issue_session=fake_issue)

    token = UserManager.generate_jwt_token(
        user_id="u1",
        email="u1@test.test",
    )
    payload = jwt.decode(
        token,
        env("JWT_SECRET"),
        algorithms=["HS256"],
        audience=env("JWT_AUDIENCE"),
        issuer=env("JWT_ISSUER"),
        leeway=30,
    )
    assert payload["jti"] == "fake-session-key"
    assert payload["sub"] == "u1"
    assert "nbf" in payload  # M-4 nbf claim emitted
    assert "iat" in payload
    assert captured["user_id"] == "u1"


def test_generate_jwt_token_falls_back_when_hook_missing():
    """Without the extension, ``generate_jwt_token`` mints a random key
    so the JWT is still well-formed (just stateless)."""
    import os

    os.environ.setdefault("JWT_SECRET", "a" * 64)
    os.environ.setdefault("JWT_AUDIENCE", "test-aud")
    os.environ.setdefault("JWT_ISSUER", "test-iss")

    from serverframework.lib.Dependencies import jwt
    from serverframework.lib.Environment import env
    from serverframework.logic.BLL_Auth import UserManager

    token = UserManager.generate_jwt_token(
        user_id="u1",
        email="u1@test.test",
    )
    payload = jwt.decode(
        token,
        env("JWT_SECRET"),
        algorithms=["HS256"],
        audience=env("JWT_AUDIENCE"),
        issuer=env("JWT_ISSUER"),
        leeway=30,
    )
    assert payload["jti"]  # non-empty
    assert payload["sub"] == "u1"
