"""Item 58 — magic-link BLL tests against a real ModelRegistry & DB."""

from datetime import datetime, timedelta, timezone

import pytest

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.auth_magic_link.BLL_Auth_MagicLink import (
    AuthMagicLinkTokenModel,
    MagicLinkManager,
    clear_send_listeners,
    register_send_listener,
)
from zephyrex.extensions.auth_magic_link.EXT_Auth_MagicLink import (
    EXT_Auth_MagicLink,
)
from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Auth import (
    InvalidGrantError,
    PasswordlessGrantRegistry,
    SessionModel,
)


@pytest.fixture(autouse=True)
def _ensure_grant_registered():
    """The ``magic_link`` validator registers at module import. Ensure it is
    present even if a sibling test snapshotted+cleared the registry."""
    from zephyrex.extensions.auth_magic_link.BLL_Auth_MagicLink import (
        magic_link_grant_validator,
    )

    PasswordlessGrantRegistry.register("magic_link", magic_link_grant_validator)
    yield


@pytest.fixture
def captured_emails():
    """In-memory capture of emails the manager would dispatch."""
    inbox = []

    def listener(email, magic_link_url, raw_token):
        inbox.append(
            {"email": email, "magic_link_url": magic_link_url, "raw_token": raw_token}
        )

    clear_send_listeners()
    register_send_listener(listener)
    yield inbox
    clear_send_listeners()


class TestMagicLink(ExtensionServerMixin):
    extension_class = EXT_Auth_MagicLink

    def _manager(self, model_registry):
        return MagicLinkManager(
            requester_id=env("ROOT_ID"), model_registry=model_registry
        )

    # ------------------------------------------------------------------
    # request: 202 with no enumeration
    # ------------------------------------------------------------------

    def test_request_returns_202_for_unregistered_email_no_enumeration(
        self, model_registry, captured_emails, admin_a
    ):
        manager = self._manager(model_registry)

        unregistered = manager.request_magic_link(email="ghost@example.com")
        registered = manager.request_magic_link(email=admin_a.email)

        # Identical response shape, regardless of email registration.
        assert unregistered.status == "accepted"
        assert registered.status == "accepted"
        assert unregistered.model_dump() == registered.model_dump()

        # No token leaked through the response - the manager only returns
        # the constant "accepted" envelope.
        assert "token" not in unregistered.model_dump()
        assert "token" not in registered.model_dump()

        # The unregistered email path must not have produced any email.
        registered_emails = [c for c in captured_emails if c["email"] == admin_a.email]
        ghost_emails = [c for c in captured_emails if c["email"] == "ghost@example.com"]
        assert len(registered_emails) == 1
        assert len(ghost_emails) == 0

    # ------------------------------------------------------------------
    # verify: happy path
    # ------------------------------------------------------------------

    def test_verify_happy_path_issues_session_with_grant_type_magic_link(
        self, model_registry, captured_emails, admin_a
    ):
        manager = self._manager(model_registry)
        manager.request_magic_link(email=admin_a.email)

        assert len(captured_emails) == 1
        raw_token = captured_emails[0]["raw_token"]

        result = manager.verify_magic_link(token=raw_token, email=admin_a.email)

        assert result.user_id == admin_a.id
        assert result.grant_type == "magic_link"
        assert result.session_key

        # The session was persisted with grant_type="magic_link".
        SessionDB = SessionModel.DB(model_registry.DB.manager.Base)
        sessions = SessionDB.list(
            requester_id=env("ROOT_ID"),
            model_registry=model_registry,
            filters=[SessionDB.session_key == result.session_key],
            return_type="dto",
            override_dto=SessionModel,
        )
        assert len(sessions) == 1
        assert sessions[0].grant_type == "magic_link"
        assert sessions[0].user_id == admin_a.id

    # ------------------------------------------------------------------
    # verify: replay rejected
    # ------------------------------------------------------------------

    def test_verify_replay_fails(self, model_registry, captured_emails, admin_a):
        manager = self._manager(model_registry)
        manager.request_magic_link(email=admin_a.email)
        raw_token = captured_emails[-1]["raw_token"]

        manager.verify_magic_link(token=raw_token, email=admin_a.email)

        with pytest.raises(InvalidGrantError) as exc_info:
            manager.verify_magic_link(token=raw_token, email=admin_a.email)
        assert exc_info.value.status_code == 401

    # ------------------------------------------------------------------
    # verify: expired token rejected
    # ------------------------------------------------------------------

    def test_verify_expired_token_fails(
        self, model_registry, captured_emails, admin_a
    ):
        manager = self._manager(model_registry)
        manager.request_magic_link(email=admin_a.email)
        raw_token = captured_emails[-1]["raw_token"]

        # Force the just-created token's expiry into the past.
        TokenDB = AuthMagicLinkTokenModel.DB(model_registry.DB.manager.Base)
        rows = TokenDB.list(
            requester_id=env("ROOT_ID"),
            model_registry=model_registry,
            filters=[
                TokenDB.user_id == admin_a.id,
                TokenDB.is_used == False,  # noqa: E712
            ],
            return_type="dto",
            override_dto=AuthMagicLinkTokenModel,
        )
        latest = max(rows, key=lambda r: r.created_at or datetime.min)
        TokenDB.update(
            requester_id=env("ROOT_ID"),
            model_registry=model_registry,
            id=latest.id,
            new_properties={
                "expires_at": datetime.now(timezone.utc) - timedelta(minutes=1),
            },
        )

        with pytest.raises(InvalidGrantError):
            manager.verify_magic_link(token=raw_token, email=admin_a.email)

    # ------------------------------------------------------------------
    # verify: success invalidates other outstanding tokens for the user
    # ------------------------------------------------------------------

    def test_verify_succeeds_invalidates_other_outstanding_tokens_for_same_user(
        self, model_registry, captured_emails, admin_a
    ):
        manager = self._manager(model_registry)

        manager.request_magic_link(email=admin_a.email)
        manager.request_magic_link(email=admin_a.email)
        manager.request_magic_link(email=admin_a.email)

        # Verify against the latest token; the other two must be invalidated.
        latest_raw = captured_emails[-1]["raw_token"]
        manager.verify_magic_link(token=latest_raw, email=admin_a.email)

        TokenDB = AuthMagicLinkTokenModel.DB(model_registry.DB.manager.Base)
        outstanding = TokenDB.list(
            requester_id=env("ROOT_ID"),
            model_registry=model_registry,
            filters=[
                TokenDB.user_id == admin_a.id,
                TokenDB.is_used == False,  # noqa: E712
            ],
            return_type="dto",
            override_dto=AuthMagicLinkTokenModel,
        )
        assert outstanding == [] or all(
            t.is_used for t in (outstanding or [])
        ), f"Expected zero outstanding tokens; got {outstanding!r}"

        # Replay with one of the now-invalidated older tokens must also fail.
        for older in captured_emails[:-1]:
            with pytest.raises(InvalidGrantError):
                manager.verify_magic_link(
                    token=older["raw_token"], email=admin_a.email
                )
