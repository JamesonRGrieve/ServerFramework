"""Item 59 - device-pairing BLL tests against a real ModelRegistry & DB."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.auth_device_pairing.BLL_Auth_DevicePairing import (
    DevicePairingManager,
    DevicePairingRequestModel,
)
from zephyrex.extensions.auth_device_pairing.EXT_Auth_DevicePairing import (
    EXT_Auth_DevicePairing,
)
from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Auth import (
    InvalidGrantError,
    PasswordlessGrantRegistry,
    SessionModel,
)


@pytest.fixture(autouse=True)
def _ensure_grant_registered():
    from zephyrex.extensions.auth_device_pairing.BLL_Auth_DevicePairing import (
        device_pairing_grant_validator,
    )

    PasswordlessGrantRegistry.register(
        "device_pairing", device_pairing_grant_validator
    )
    yield


def _extract_raw_token_from_payload(payload: str) -> str:
    """The QR payload is either a deep-link URL with ?token= or the raw token."""
    if "?token=" in payload:
        return payload.split("?token=", 1)[1]
    return payload


class TestDevicePairing(ExtensionServerMixin):
    extension_class = EXT_Auth_DevicePairing

    def _manager(self, model_registry):
        return DevicePairingManager(
            requester_id=env("ROOT_ID"), model_registry=model_registry
        )

    # ------------------------------------------------------------------
    # Token expiry
    # ------------------------------------------------------------------

    def test_expired_token_cannot_be_approved(self, model_registry, admin_a):
        manager = self._manager(model_registry)
        result = manager.request_pairing(device_type="mobile", device_name="phone")
        raw_token = _extract_raw_token_from_payload(result.qr_payload)

        DB = DevicePairingRequestModel.DB(model_registry.DB.manager.Base)
        DB.update(
            requester_id=env("ROOT_ID"),
            model_registry=model_registry,
            id=result.pairing_id,
            new_properties={
                "expires_at": datetime.now(timezone.utc) - timedelta(seconds=10),
            },
        )

        with pytest.raises(InvalidGrantError):
            manager.approve_pairing(token=raw_token, approver_user_id=admin_a.id)

    # ------------------------------------------------------------------
    # Replay protection
    # ------------------------------------------------------------------

    def test_approve_replay_fails(self, model_registry, admin_a):
        manager = self._manager(model_registry)
        result = manager.request_pairing(device_type="mobile")
        raw_token = _extract_raw_token_from_payload(result.qr_payload)

        first = manager.approve_pairing(token=raw_token, approver_user_id=admin_a.id)
        assert first.state == "approved"
        assert first.session_key

        with pytest.raises(InvalidGrantError):
            manager.approve_pairing(token=raw_token, approver_user_id=admin_a.id)

    # ------------------------------------------------------------------
    # Denial path
    # ------------------------------------------------------------------

    def test_deny_marks_pairing_denied_and_blocks_subsequent_approve(
        self, model_registry, admin_a
    ):
        manager = self._manager(model_registry)
        result = manager.request_pairing(device_type="desktop")
        raw_token = _extract_raw_token_from_payload(result.qr_payload)

        denied = manager.deny_pairing(token=raw_token, approver_user_id=admin_a.id)
        assert denied.state == "denied"

        status = manager.get_status(result.pairing_id)
        assert status.state == "denied"
        assert status.session_key is None

        with pytest.raises(InvalidGrantError):
            manager.approve_pairing(token=raw_token, approver_user_id=admin_a.id)

    # ------------------------------------------------------------------
    # Unauthenticated approve rejected
    # ------------------------------------------------------------------

    def test_unauthenticated_approve_rejected(self, model_registry):
        manager = self._manager(model_registry)
        result = manager.request_pairing(device_type="mobile")
        raw_token = _extract_raw_token_from_payload(result.qr_payload)

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            manager.approve_pairing(token=raw_token, approver_user_id="")
        assert exc_info.value.status_code == 401

    # ------------------------------------------------------------------
    # Awaiting-approval session unusable until approved
    # ------------------------------------------------------------------

    def test_awaiting_approval_no_session_until_approved(
        self, model_registry, admin_a
    ):
        manager = self._manager(model_registry)
        result = manager.request_pairing(device_type="mobile")

        status = manager.get_status(result.pairing_id)
        assert status.state == "pending"
        assert status.session_key is None

        # No SessionModel row should yet exist for this pairing.
        SessionDB = SessionModel.DB(model_registry.DB.manager.Base)
        sessions = SessionDB.list(
            requester_id=env("ROOT_ID"),
            model_registry=model_registry,
            filters=[SessionDB.grant_type == "device_pairing"],
            return_type="dto",
            override_dto=SessionModel,
        )
        # We may have other approved pairings from prior tests; ensure none
        # are linked back to this still-pending pairing.
        DB = DevicePairingRequestModel.DB(model_registry.DB.manager.Base)
        rows = DB.list(
            requester_id=env("ROOT_ID"),
            model_registry=model_registry,
            filters=[DB.id == result.pairing_id],
            return_type="dto",
            override_dto=DevicePairingRequestModel,
        )
        assert rows[0].pending_session_id is None
        assert rows[0].approver_user_id is None

        # Approve and check that the session now exists.
        raw_token = _extract_raw_token_from_payload(result.qr_payload)
        approved = manager.approve_pairing(
            token=raw_token, approver_user_id=admin_a.id
        )

        post_status = manager.get_status(result.pairing_id)
        assert post_status.state == "approved"
        assert post_status.session_key == approved.session_key
        assert post_status.user_id == admin_a.id

    # ------------------------------------------------------------------
    # Polling status parity (status returns the same shape SSE emits)
    # ------------------------------------------------------------------

    def test_polling_status_parity_with_approved_state(
        self, model_registry, admin_a
    ):
        manager = self._manager(model_registry)
        result = manager.request_pairing(device_type="web")
        raw_token = _extract_raw_token_from_payload(result.qr_payload)

        approved = manager.approve_pairing(
            token=raw_token, approver_user_id=admin_a.id
        )

        polled = manager.get_status(result.pairing_id)
        assert polled.state == "approved"
        assert polled.session_key == approved.session_key
        assert polled.user_id == admin_a.id

    # ------------------------------------------------------------------
    # Status: expired pairings report "expired"
    # ------------------------------------------------------------------

    def test_status_reports_expired_for_lapsed_pairing(
        self, model_registry
    ):
        manager = self._manager(model_registry)
        result = manager.request_pairing(device_type="mobile")
        DB = DevicePairingRequestModel.DB(model_registry.DB.manager.Base)
        DB.update(
            requester_id=env("ROOT_ID"),
            model_registry=model_registry,
            id=result.pairing_id,
            new_properties={
                "expires_at": datetime.now(timezone.utc) - timedelta(seconds=1),
            },
        )
        status = manager.get_status(result.pairing_id)
        assert status.state == "expired"

    # ------------------------------------------------------------------
    # SSE generator: emits a single terminal event then closes
    # ------------------------------------------------------------------

    def test_sse_generator_emits_terminal_event_on_approval(
        self, model_registry, admin_a
    ):
        manager = self._manager(model_registry)
        result = manager.request_pairing(device_type="mobile")
        raw_token = _extract_raw_token_from_payload(result.qr_payload)
        manager.approve_pairing(token=raw_token, approver_user_id=admin_a.id)

        async def collect():
            chunks = []
            agen = manager.stream_status(result.pairing_id, poll_interval=0.05)
            async for chunk in agen:
                chunks.append(chunk)
            return chunks

        chunks = asyncio.run(collect())
        assert len(chunks) == 1
        assert chunks[0].startswith("event: approved\n")
        assert "session_key" in chunks[0]

    def test_sse_generator_emits_denied_event(self, model_registry, admin_a):
        manager = self._manager(model_registry)
        result = manager.request_pairing(device_type="mobile")
        raw_token = _extract_raw_token_from_payload(result.qr_payload)
        manager.deny_pairing(token=raw_token, approver_user_id=admin_a.id)

        async def collect():
            chunks = []
            agen = manager.stream_status(result.pairing_id, poll_interval=0.05)
            async for chunk in agen:
                chunks.append(chunk)
            return chunks

        chunks = asyncio.run(collect())
        assert len(chunks) == 1
        assert chunks[0].startswith("event: denied\n")
