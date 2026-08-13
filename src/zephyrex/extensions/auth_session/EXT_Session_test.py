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
    from zephyrex.logic.BLL_Auth import reset_session_hooks

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
    import zephyrex.extensions.auth_session.BLL_Session as session_mod
    from zephyrex.logic.BLL_Auth import register_session_hooks

    register_session_hooks(
        issue_session=session_mod.issue_session,
        enforce_not_revoked=session_mod.enforce_not_revoked,
        manager_factory=session_mod.session_manager_factory,
        revoke_user_sessions=session_mod.revoke_user_sessions,
    )


def test_register_session_hooks_partial_update():
    """Each hook is independently overridable so tests / operators can
    swap a single implementation without re-registering the rest."""
    from zephyrex.logic.BLL_Auth import (
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
    from zephyrex.extensions.auth_session.EXT_Session import (
        AuthSessionExtension,
    )
    from zephyrex.logic.BLL_Auth import _session_hooks

    AuthSessionExtension.on_load()

    assert _session_hooks["issue_session"] is not None
    assert _session_hooks["enforce_not_revoked"] is not None
    assert _session_hooks["manager_factory"] is not None
    assert _session_hooks["revoke_user_sessions"] is not None


def test_pep562_lazy_import_resolves_session_model():
    """``BLL_Auth.SessionModel`` resolves to the extension's class via
    the PEP 562 ``__getattr__`` shim. This is the migration-compat path
    for code that still does ``from BLL_Auth import SessionModel``."""
    from zephyrex.logic import BLL_Auth as core
    from zephyrex.extensions.auth_session.BLL_Session import (
        SessionModel as ExtSessionModel,
    )

    assert core.SessionModel is ExtSessionModel
    assert core.SessionManager is ExtSessionModel.Manager


def test_enforce_not_revoked_requires_jti_when_no_extension():
    """Without the extension, JWT verification still requires ``jti`` so
    enabling ``auth_session`` later is fully effective for outstanding
    tokens. The framework does not silently downgrade to "no jti, no gate"."""
    from fastapi import HTTPException

    from zephyrex.logic.BLL_Auth import UserManager

    with pytest.raises(HTTPException) as excinfo:
        UserManager._enforce_session_not_revoked(
            payload={}, model_registry=None, db=None
        )
    assert excinfo.value.status_code == 401
    assert "jti" in str(excinfo.value.detail).lower()


def test_enforce_not_revoked_calls_hook_when_registered():
    """When the extension's hook is registered, the core dispatches to
    it with the JWT payload, registry, and db connection."""
    from zephyrex.logic.BLL_Auth import (
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

    from zephyrex.lib.Dependencies import jwt
    from zephyrex.lib.Environment import env
    from zephyrex.logic.BLL_Auth import (
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

    from zephyrex.lib.Dependencies import jwt
    from zephyrex.lib.Environment import env
    from zephyrex.logic.BLL_Auth import UserManager

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


###############################################################################
# Unit tests for session lifecycle functions
###############################################################################

from unittest.mock import MagicMock, patch


class TestIssueSession:
    """Unit tests for the issue_session hook function."""

    def test_returns_key_when_registry_is_none(self):
        from zephyrex.extensions.auth_session.BLL_Session import issue_session

        key = issue_session(user_id="u1", model_registry=None)
        assert isinstance(key, str)
        assert len(key) == 32  # secrets.token_hex(16)

    def test_uses_provided_session_key(self):
        from zephyrex.extensions.auth_session.BLL_Session import issue_session

        key = issue_session(user_id="u1", model_registry=None, session_key="custom-key")
        assert key == "custom-key"

    def test_persist_failure_still_returns_key(self):
        from zephyrex.extensions.auth_session.BLL_Session import issue_session

        mock_registry = MagicMock()
        mock_db_cls = MagicMock()
        mock_db_cls.create.side_effect = RuntimeError("DB down")

        with patch(
            "zephyrex.extensions.auth_session.BLL_Session.SessionModel"
        ) as mock_model:
            mock_model.DB.return_value = mock_db_cls
            key = issue_session(user_id="u1", model_registry=mock_registry)

        assert isinstance(key, str)
        assert len(key) == 32


class TestEnforceNotRevoked:
    """Parameterized tests for enforce_not_revoked edge cases."""

    @pytest.fixture(autouse=True)
    def _restore_hooks(self):
        _reset_hooks()
        yield
        _reset_hooks()
        import zephyrex.extensions.auth_session.BLL_Session as session_mod

        session_mod.register_session_hooks(
            issue_session=session_mod.issue_session,
            enforce_not_revoked=session_mod.enforce_not_revoked,
            manager_factory=session_mod.session_manager_factory,
            revoke_user_sessions=session_mod.revoke_user_sessions,
        )

    def test_missing_jti_raises(self):
        from zephyrex.extensions.auth_session.BLL_Session import (
            enforce_not_revoked,
        )

        with pytest.raises(Exception, match="jti"):
            enforce_not_revoked({}, MagicMock())

    def test_null_registry_and_db_returns_early(self):
        from zephyrex.extensions.auth_session.BLL_Session import (
            enforce_not_revoked,
        )

        enforce_not_revoked({"jti": "abc"}, None, db=None)

    @pytest.mark.parametrize(
        "revoked, is_active, pending_state, should_raise",
        [
            (False, True, None, False),
            (True, True, None, True),
            (False, False, None, True),
            (False, True, "awaiting_approval", True),
        ],
        ids=["active-ok", "revoked", "inactive", "pending-approval"],
    )
    def test_session_states(self, revoked, is_active, pending_state, should_raise):
        from zephyrex.extensions.auth_session.BLL_Session import (
            enforce_not_revoked,
        )

        mock_session = MagicMock()
        mock_session.revoked = revoked
        mock_session.is_active = is_active
        mock_session.pending_state = pending_state

        mock_db_cls = MagicMock()
        mock_db_cls.get.return_value = mock_session

        mock_registry = MagicMock()

        with patch(
            "zephyrex.extensions.auth_session.BLL_Session.SessionModel"
        ) as mock_model:
            mock_model.DB.return_value = mock_db_cls
            if should_raise:
                with pytest.raises(Exception):
                    enforce_not_revoked({"jti": "abc"}, mock_registry)
            else:
                enforce_not_revoked({"jti": "abc"}, mock_registry)


class TestSessionManagerFactory:
    def test_returns_manager_instance(self):
        from zephyrex.extensions.auth_session.BLL_Session import (
            session_manager_factory,
        )

        mock_registry = MagicMock()
        mgr = session_manager_factory(requester_id="u1", model_registry=mock_registry)
        assert mgr is not None

    def test_accepts_none_registry(self):
        from zephyrex.extensions.auth_session.BLL_Session import (
            session_manager_factory,
        )

        result = session_manager_factory(requester_id="u1", model_registry=None)
        assert result is not None


class TestRevokeUserSessions:
    def test_noop_without_registry(self):
        from zephyrex.extensions.auth_session.BLL_Session import (
            revoke_user_sessions,
        )

        revoke_user_sessions(user_id="u1", requester_id="admin", model_registry=None)
