# SPDX-License-Identifier: AGPL-3.0-or-later
"""Item 58 — magic-link BLL tests against a real ModelRegistry & DB."""

from datetime import datetime, timedelta, timezone

import pytest

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.auth_magic_link.BLL_Auth_MagicLink import (
    AuthMagicLinkTokenModel,
    MagicLinkManager,
    clear_send_listeners,
    magic_link_grant_validator,
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
    UserIdGrantPayload,
    UserManager,
)

# Mail goes out on a background thread; this bounds the wait for it.
_DELIVERY_TIMEOUT_SECONDS = 10


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

        # The token is the client's credential for that session.
        assert UserManager._decode_jwt(result.token)["jti"] == result.session_key
        user = UserManager.auth(
            model_registry=model_registry, authorization=f"Bearer {result.token}"
        )
        assert user.id == admin_a.id

    def test_verify_over_http_signs_the_browser_in(
        self, server, captured_emails, admin_a
    ):
        from fastapi.testclient import TestClient

        from zephyrex.lib.SessionCookies import CSRF_COOKIE, SESSION_COOKIE

        browser = TestClient(server.app, base_url="https://testserver")
        requested = browser.post(
            "/v1/auth/magic-link/request", json={"email": admin_a.email}
        )
        assert requested.status_code in (200, 202), requested.text
        raw_token = captured_emails[-1]["raw_token"]

        verified = browser.post(
            "/v1/auth/magic-link/verify",
            json={"token": raw_token, "email": admin_a.email},
        )
        assert verified.status_code == 200, verified.text
        assert browser.cookies[SESSION_COOKIE] == verified.json()["token"]
        assert browser.cookies[CSRF_COOKIE]
        assert browser.get("/v1/user").status_code == 200

    def test_the_link_is_emailed_through_the_email_extension(
        self, model_registry, captured_emails, admin_a, monkeypatch
    ):
        """The email extension (loaded here as a declared dependency) is the
        delivery path; its provider rotation is the external boundary."""
        import threading

        from zephyrex.extensions.email.EXT_EMail import EXT_EMail

        sent = []
        delivered = threading.Event()

        async def send_email(recipient, subject, body):
            sent.append((recipient, subject, body))
            delivered.set()

        monkeypatch.setattr(EXT_EMail, "send_email", send_email)
        self._manager(model_registry).request_magic_link(email=admin_a.email)

        assert delivered.wait(timeout=_DELIVERY_TIMEOUT_SECONDS)
        ((recipient, subject, body),) = sent
        assert recipient == admin_a.email
        assert subject.startswith("Sign in to ")
        assert captured_emails[-1]["magic_link_url"] in body

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

    # ------------------------------------------------------------------
    # verify: an unissued/garbage token is rejected (shared resolver's
    # constant-time bcrypt confirm returns no match)
    # ------------------------------------------------------------------

    def test_verify_wrong_token_rejected(self, model_registry, admin_a):
        manager = self._manager(model_registry)
        # An unissued token has no matching fingerprint row, so the shared
        # resolver finds no candidate and verification is rejected. (No token
        # is created, keeping the shared DB state hermetic for sibling tests.)
        with pytest.raises(InvalidGrantError):
            manager.verify_magic_link(token="not-a-real-token", email=admin_a.email)

    # ------------------------------------------------------------------
    # grant validator (shared factory) resolves a live user and guards a
    # missing model_registry
    # ------------------------------------------------------------------

    def test_grant_validator_resolves_user_and_guards_missing_registry(
        self, model_registry, admin_a
    ):
        user = magic_link_grant_validator(
            UserIdGrantPayload(user_id=admin_a.id, model_registry=model_registry)
        )
        assert user.id == admin_a.id

        with pytest.raises(InvalidGrantError) as exc:
            magic_link_grant_validator(
                UserIdGrantPayload(user_id=admin_a.id, model_registry=None)
            )
        assert "Magic-link grant payload missing model_registry" in exc.value.detail

    def test_verify_expired_token_fails(self, model_registry, captured_emails, admin_a):
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
                manager.verify_magic_link(token=older["raw_token"], email=admin_a.email)
