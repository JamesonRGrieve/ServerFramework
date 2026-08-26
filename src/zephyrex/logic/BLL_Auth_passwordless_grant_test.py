"""Framework-level tests for the passwordless-grant primitives in
``BLL_Auth``. These cover the door (mixin, registry, session fields,
dispatch); the magic-link and device-pairing extensions arrive separately."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from pydantic import BaseModel

from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Auth import (
    InvalidGrantError,
    OneTimeTokenMixin,
    PasswordlessGrantRegistry,
    PendingSessionError,
    SessionModel,
    UserIdGrantPayload,
    UserManager,
    UserModel,
    make_user_id_grant_validator,
)

# ---------------------------------------------------------------------------
# OneTimeTokenMixin
# ---------------------------------------------------------------------------


class TestOneTimeTokenMixin:
    def test_generate_returns_raw_and_instance(self):
        raw, token = OneTimeTokenMixin.generate(ttl_minutes=15)
        assert isinstance(raw, str)
        # 256 bits → 32 bytes → 43 base64url chars (no padding)
        assert len(raw) >= 40
        assert isinstance(token, OneTimeTokenMixin)
        assert token.code_hash and token.code_hash != raw
        assert token.code_salt
        assert token.is_used is False
        assert token.used_at is None
        assert token.created_ip is None
        # TTL within ±5s of now+15min
        delta = token.expires_at - datetime.now(timezone.utc)
        assert timedelta(minutes=14) <= delta <= timedelta(minutes=16)

    def test_generate_produces_unique_codes(self):
        raw_a, _ = OneTimeTokenMixin.generate(ttl_minutes=5)
        raw_b, _ = OneTimeTokenMixin.generate(ttl_minutes=5)
        assert raw_a != raw_b

    def test_verify_accepts_correct_code(self):
        raw, token = OneTimeTokenMixin.generate(ttl_minutes=5)
        assert token.verify(raw) is True

    def test_verify_rejects_wrong_code(self):
        _, token = OneTimeTokenMixin.generate(ttl_minutes=5)
        assert token.verify("not-the-real-code") is False

    def test_verify_rejects_garbage_input(self):
        _, token = OneTimeTokenMixin.generate(ttl_minutes=5)
        # bcrypt rejects empty / oversized inputs by raising; verify swallows
        assert token.verify("") is False

    def test_mark_used_sets_state(self):
        _, token = OneTimeTokenMixin.generate(ttl_minutes=5)
        assert token.is_used is False
        before = datetime.now(timezone.utc)
        token.mark_used()
        after = datetime.now(timezone.utc)
        assert token.is_used is True
        assert token.used_at is not None
        assert before <= token.used_at <= after


# ---------------------------------------------------------------------------
# OneTimeTokenMixin.resolve — the shared lookup hoisted out of magic-link and
# device pairing (issue #228). Driven with a controllable ``list_candidates``
# so the algorithm (fingerprint-narrow -> expiry gate -> constant-time bcrypt
# confirm -> single-use base filter) is exercised without a DB; the extension
# suites cover the same resolver against a real ModelRegistry end-to-end.
# ---------------------------------------------------------------------------


class _RecordingColumn:
    """Stand-in for a SQLAlchemy column: ``col == value`` records the pair so
    a test can assert which filters ``resolve`` built."""

    def __init__(self, name: str) -> None:
        self.name = name

    def __eq__(self, other):  # type: ignore[override]
        return (self.name, other)

    __hash__ = None  # type: ignore[assignment]


class _FakeTokenDB:
    is_used = _RecordingColumn("is_used")
    code_fingerprint = _RecordingColumn("code_fingerprint")


class TestOneTimeTokenResolve:
    def test_resolves_matching_unexpired_token(self):
        raw, token = OneTimeTokenMixin.generate(ttl_minutes=5)
        captured = {}

        def list_candidates(filters):
            captured["filters"] = filters
            return [token]

        matched = OneTimeTokenMixin.resolve(_FakeTokenDB, list_candidates, raw)
        assert matched is token
        # Base filter narrows on the single-use flag AND the HMAC fingerprint —
        # the single-use gate lives on the read side, exactly as before.
        assert ("is_used", False) in captured["filters"]
        assert (
            "code_fingerprint",
            OneTimeTokenMixin.fingerprint(raw),
        ) in captured["filters"]

    def test_rejects_wrong_token_via_bcrypt_confirm(self):
        # A candidate is offered but the submitted code differs, so the
        # constant-time bcrypt confirm must reject it.
        _, token = OneTimeTokenMixin.generate(ttl_minutes=5)
        matched = OneTimeTokenMixin.resolve(
            _FakeTokenDB, lambda f: [token], "not-the-real-code"
        )
        assert matched is None

    def test_skips_expired_token_even_when_bcrypt_would_match(self):
        raw, token = OneTimeTokenMixin.generate(ttl_minutes=5)
        token.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        matched = OneTimeTokenMixin.resolve(_FakeTokenDB, lambda f: [token], raw)
        assert matched is None

    def test_returns_none_for_empty_or_null_candidate_set(self):
        raw, _ = OneTimeTokenMixin.generate(ttl_minutes=5)
        assert OneTimeTokenMixin.resolve(_FakeTokenDB, lambda f: [], raw) is None
        # A None from the list callable is tolerated like an empty list.
        assert OneTimeTokenMixin.resolve(_FakeTokenDB, lambda f: None, raw) is None

    def test_extra_filters_are_appended_to_base_filter(self):
        raw, token = OneTimeTokenMixin.generate(ttl_minutes=5)
        captured = {}

        def list_candidates(filters):
            captured["filters"] = filters
            return [token]

        sentinel = ("requested_email", "alice@example.com")
        matched = OneTimeTokenMixin.resolve(
            _FakeTokenDB, list_candidates, raw, extra_filters=[sentinel]
        )
        assert matched is token
        assert sentinel in captured["filters"]

    def test_first_unexpired_verifying_candidate_wins(self):
        # An expired row sharing the fingerprint slot is skipped; the live one
        # that bcrypt-confirms is returned (the >1-candidate edge).
        raw, token = OneTimeTokenMixin.generate(ttl_minutes=5)
        _, stale = OneTimeTokenMixin.generate(ttl_minutes=5)
        stale.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        matched = OneTimeTokenMixin.resolve(_FakeTokenDB, lambda f: [stale, token], raw)
        assert matched is token


# ---------------------------------------------------------------------------
# make_user_id_grant_validator — the shared passwordless-grant validator
# factory hoisted out of both extensions (issue #228).
# ---------------------------------------------------------------------------


class TestMakeUserIdGrantValidator:
    def test_resolves_a_live_user(self, admin_a, model_registry):
        validator = make_user_id_grant_validator("Magic-link")
        user = validator(
            UserIdGrantPayload(user_id=admin_a.id, model_registry=model_registry)
        )
        assert isinstance(user, UserModel)
        assert user.id == admin_a.id

    def test_missing_registry_raises_invalid_grant_with_label(self):
        validator = make_user_id_grant_validator("Magic-link")
        with pytest.raises(InvalidGrantError) as exc:
            validator(UserIdGrantPayload(user_id="whatever", model_registry=None))
        assert exc.value.status_code == 401
        assert "Magic-link grant payload missing model_registry" in exc.value.detail

    def test_subject_overrides_only_the_user_gone_message(self):
        # Device pairing passes subject="Approver": the payload-missing message
        # keeps the label, byte-identical to the pre-consolidation copies.
        validator = make_user_id_grant_validator("Device-pairing", subject="Approver")
        with pytest.raises(InvalidGrantError) as exc:
            validator(UserIdGrantPayload(user_id="whatever", model_registry=None))
        assert "Device-pairing grant payload missing model_registry" in exc.value.detail

    def test_unknown_user_id_raises_not_found_from_get(self, model_registry):
        # Preserved behavior: ``UserDB.get`` (no allow_nonexistent) raises a 404
        # HTTPException for a missing row — the ``if user is None`` branch is
        # not reached. Both pre-consolidation validators did exactly this.
        validator = make_user_id_grant_validator("Magic-link")
        with pytest.raises(HTTPException) as exc:
            validator(
                UserIdGrantPayload(
                    user_id=str(uuid.uuid4()), model_registry=model_registry
                )
            )
        assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# PasswordlessGrantRegistry
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_registry():
    """Snapshot+restore the global registry around each test so
    extensions registered by other suites don't leak in or out."""
    snapshot = dict(PasswordlessGrantRegistry._validators)
    PasswordlessGrantRegistry._validators.clear()
    try:
        yield
    finally:
        PasswordlessGrantRegistry._validators.clear()
        PasswordlessGrantRegistry._validators.update(snapshot)


class TestPasswordlessGrantRegistry:
    def test_register_and_get(self):
        sentinel = object()

        def validator(payload):
            return sentinel

        PasswordlessGrantRegistry.register("demo", validator)
        assert PasswordlessGrantRegistry.get("demo") is validator
        assert PasswordlessGrantRegistry.get("demo")(None) is sentinel

    def test_get_unknown_grant_raises_keyerror_naming_grant(self):
        with pytest.raises(KeyError) as exc_info:
            PasswordlessGrantRegistry.get("does_not_exist")
        assert "does_not_exist" in str(exc_info.value)

    def test_list_grant_types(self):
        assert PasswordlessGrantRegistry.list_grant_types() == []
        PasswordlessGrantRegistry.register("a", lambda p: None)
        PasswordlessGrantRegistry.register("b", lambda p: None)
        types = PasswordlessGrantRegistry.list_grant_types()
        assert set(types) == {"a", "b"}

    def test_register_overwrites(self):
        PasswordlessGrantRegistry.register("dup", lambda p: 1)
        PasswordlessGrantRegistry.register("dup", lambda p: 2)
        assert PasswordlessGrantRegistry.get("dup")(None) == 2


# ---------------------------------------------------------------------------
# SessionModel new fields
# ---------------------------------------------------------------------------


class TestSessionModelNewFields:
    def _kwargs(self, **overrides):
        now = datetime.now(timezone.utc)
        base = dict(
            id=str(uuid.uuid4()),
            user_id=str(uuid.uuid4()),
            session_key="abc",
            jwt_issued_at=now,
            is_active=True,
            last_activity=now,
            expires_at=now + timedelta(hours=1),
            revoked=False,
            trust_score=50,
            requires_verification=False,
        )
        base.update(overrides)
        return base

    def test_grant_type_defaults_to_none(self):
        s = SessionModel(**self._kwargs())
        assert s.grant_type is None

    def test_grant_type_round_trips(self):
        s = SessionModel(**self._kwargs(grant_type="magic_link"))
        assert s.grant_type == "magic_link"
        dumped = s.model_dump()
        assert dumped["grant_type"] == "magic_link"

    def test_pending_state_defaults_to_none(self):
        s = SessionModel(**self._kwargs())
        assert s.pending_state is None

    def test_pending_state_accepts_literal_values(self):
        for value in ("awaiting_approval", "approved", "denied"):
            s = SessionModel(**self._kwargs(pending_state=value))
            assert s.pending_state == value

    def test_pending_state_rejects_unknown_values(self):
        with pytest.raises(Exception):
            SessionModel(**self._kwargs(pending_state="bogus"))


# ---------------------------------------------------------------------------
# Session pending_state enforcement at the bonding layer
# ---------------------------------------------------------------------------


class TestPendingSessionEnforcement:
    def test_awaiting_approval_session_cannot_bond(
        self, admin_a, server, model_registry
    ):
        """A session in ``awaiting_approval`` must be refused by
        ``_enforce_session_not_revoked`` with a typed
        ``PendingSessionError``."""
        import secrets as _secrets

        session_key = _secrets.token_hex(16)
        now = datetime.now(timezone.utc)
        SessionModel.DB(model_registry.DB.manager.Base).create(
            requester_id=admin_a.id,
            model_registry=model_registry,
            user_id=admin_a.id,
            session_key=session_key,
            jwt_issued_at=now,
            device_type="web",
            browser="unknown",
            is_active=True,
            last_activity=now,
            expires_at=now + timedelta(hours=1),
            revoked=False,
            trust_score=50,
            pending_state="awaiting_approval",
        )

        with pytest.raises(PendingSessionError) as exc_info:
            UserManager._enforce_session_not_revoked(
                {"jti": session_key}, model_registry
            )
        assert exc_info.value.status_code == 401
        assert "awaiting approval" in exc_info.value.detail.lower()

    def test_approved_session_bonds(self, admin_a, server, model_registry):
        import secrets as _secrets

        session_key = _secrets.token_hex(16)
        now = datetime.now(timezone.utc)
        SessionModel.DB(model_registry.DB.manager.Base).create(
            requester_id=admin_a.id,
            model_registry=model_registry,
            user_id=admin_a.id,
            session_key=session_key,
            jwt_issued_at=now,
            device_type="web",
            browser="unknown",
            is_active=True,
            last_activity=now,
            expires_at=now + timedelta(hours=1),
            revoked=False,
            trust_score=50,
            pending_state="approved",
        )

        # Should not raise.
        UserManager._enforce_session_not_revoked({"jti": session_key}, model_registry)


# ---------------------------------------------------------------------------
# UserManager.login_via_grant
# ---------------------------------------------------------------------------


class _DemoGrantPayload(BaseModel):
    user_id: str


class TestLoginViaGrant:
    def test_dispatches_to_registered_validator_and_issues_session(
        self, admin_a, model_registry
    ):
        """Happy path: a registered validator returns a real seeded user,
        ``login_via_grant`` issues a SessionModel stamped with the
        grant_type."""

        called_with = {}

        def validator(payload: _DemoGrantPayload) -> UserModel:
            called_with["payload"] = payload
            return UserModel.DB(model_registry.DB.manager.Base).get(
                requester_id=env("ROOT_ID"),
                model_registry=model_registry,
                id=payload.user_id,
                return_type="dto",
                override_dto=UserModel,
            )

        PasswordlessGrantRegistry.register("demo_grant", validator)

        payload = _DemoGrantPayload(user_id=admin_a.id)
        session = UserManager.login_via_grant(
            grant_type="demo_grant",
            grant_payload=payload,
            model_registry=model_registry,
        )

        assert called_with["payload"] is payload
        assert isinstance(session, SessionModel)
        assert session.user_id == admin_a.id
        assert session.grant_type == "demo_grant"
        assert session.session_key
        assert session.is_active is True
        assert session.pending_state is None

    def test_unknown_grant_type_raises_invalid_grant(self, model_registry):
        with pytest.raises(InvalidGrantError) as exc_info:
            UserManager.login_via_grant(
                grant_type="never_registered",
                grant_payload=_DemoGrantPayload(user_id="x"),
                model_registry=model_registry,
            )
        assert exc_info.value.status_code == 401
        assert "never_registered" in exc_info.value.detail

    def test_invalid_grant_error_is_http_exception(self):
        err = InvalidGrantError()
        assert isinstance(err, HTTPException)
        assert err.status_code == 401

    def test_pending_session_error_is_http_exception(self):
        err = PendingSessionError()
        assert isinstance(err, HTTPException)
        assert err.status_code == 401
