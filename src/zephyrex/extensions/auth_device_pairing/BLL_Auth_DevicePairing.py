# SPDX-License-Identifier: AGPL-3.0-or-later
"""Item 59 - QR-code cross-device pairing authentication.

Builds on the framework primitives in ``BLL_Auth.py`` (``OneTimeTokenMixin``,
``PasswordlessGrantRegistry``, ``UserManager.login_via_grant``,
``SessionModel.grant_type``+``pending_state``) to implement the Steam
Guard / Discord cross-device approval flow.

The approved session belongs to the device that asked for it. ``/request``
hands that device a binding secret in the HttpOnly ``zx_pairing`` cookie
(scoped to the pairing routes) and stores only its hash. The first status
read that finds the pairing approved and presents the binding takes delivery:
session cookies for a browser, or a bearer token in the body for a native
client that asked for one with ``token_in_body``. Every other read, including
any that knows only the pairing id, reports state and nothing else.

``PAIRING_BASE_URL`` is the client page that approves a scanned code
(``<app>/user/pair``); the QR payload is ``<base>/approve?token=<code>``.
"""

import hashlib
import hmac
import secrets
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

from fastapi import HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from zephyrex.lib.CustomRoute import ExposeIn, custom_route
from zephyrex.lib.DateTimeUtils import ensure_utc
from zephyrex.lib.Environment import env
from zephyrex.lib.EventStream import event_stream
from zephyrex.lib.InboundSecurity import DEFAULT_AUTH_RATE_LIMIT, rate_limit
from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    DateSearchModel,
    ModelMeta,
    StringSearchModel,
    UpdateMixinModel,
)
from zephyrex.logic.BLL_Auth import (
    InvalidGrantError,
    OneTimeTokenMixin,
    PasswordlessGrantRegistry,
    UserIdGrantPayload,
    UserManager,
    make_user_id_grant_validator,
)
from zephyrex.logic.BLL_Auth.user import issue_browser_session
from zephyrex.pydantic2.fastapi import AuthType, RouterMixin

PAIRING_PREFIX = "/v1/auth/pairing"
# The requesting device's binding secret, sent only to the pairing routes.
PAIRING_COOKIE = "zx_pairing"
_BINDING_BYTES = 32


def _binding_hash(binding: str) -> str:
    return hashlib.sha256(binding.encode()).hexdigest()


    """Where the binding cookie lives: the pairing routes only, never
    readable by page scripts."""
    return {
        "path": PAIRING_PREFIX,
        "domain": env("SESSION_COOKIE_DOMAIN") or None,
        "secure": True,
        "httponly": True,
        "samesite": "lax",
    }


# ---------------------------------------------------------------------------
# Pydantic input/output models
# ---------------------------------------------------------------------------


    requesting_device_type: str = Field(
        ..., description="mobile / desktop / web / unknown"
    )
    requesting_device_name: str | None = Field(
        None, description="Human-friendly device label, e.g. 'Office Chrome'"
    )
    token_in_body: bool = Field(
        False,
        description=(
            "Native clients: return the binding in the body and deliver the "
            "session as a bearer token instead of browser cookies"
        ),
    )


    pairing_id: str
    qr_payload: str
    expires_in: int
    binding: str | None = Field(
        None,
        description=(
            "Present it as the zx_pairing cookie on status reads; returned "
            "only when token_in_body was requested"
        ),
    )


    token: str = Field(..., description="The raw pairing token scanned from the QR")


    token: str = Field(..., description="The raw pairing token to deny")


    pairing_id: str
    state: str  # pending / approved / denied / expired
    # Set only on the read that delivers the session to the requesting
    # device; ``token`` only for a token_in_body pairing.
    user_id: str | None = None
    token: str | None = None


    pairing_id: str
    state: str


    pairing_id: str
    state: str
    user_id: str


# ---------------------------------------------------------------------------
# Database model
# ---------------------------------------------------------------------------


class DevicePairingRequestModel(
    ApplicationModel,
    UpdateMixinModel,
    OneTimeTokenMixin,
    metaclass=ModelMeta,
):
    """Persisted pairing request. Token hashed at rest via OneTimeTokenMixin."""

    requesting_device_type: str = Field(
        ..., description="mobile / desktop / web / unknown"
    )
    requesting_device_name: str | None = Field(None)
    requesting_ip: str | None = Field(None)
    requesting_user_agent: str | None = Field(None)
    approver_user_id: str | None = Field(
        None, description="User who approved this pairing, set on approval"
    )
    approved_at: datetime | None = Field(None)
    denied_at: datetime | None = Field(None)
    pending_session_id: str | None = Field(
        None, description="Session row id created at approval time"
    )
    binding_hash: str | None = Field(
        None, description="SHA-256 of the requesting device's binding secret"
    )
    token_in_body: bool = Field(
        False, description="Deliver the session as a bearer token, not cookies"
    )
    consumed_at: datetime | None = Field(
        None, description="When the requesting device took delivery of its session"
    )

    table_comment: ClassVar[str] = (
        "Device pairing requests (Item 59 cross-device passwordless auth)"
    )

        requesting_device_type: str
        requesting_device_name: str | None = None
        requesting_ip: str | None = None
        requesting_user_agent: str | None = None
        code_hash: str
        code_salt: str
        code_fingerprint: str | None = None
        expires_at: datetime
        is_used: bool = False
        used_at: datetime | None = None
        created_ip: str | None = None
        binding_hash: str | None = None
        token_in_body: bool = False

        is_used: bool | None = None
        used_at: datetime | None = None
        approver_user_id: str | None = None
        approved_at: datetime | None = None
        denied_at: datetime | None = None
        pending_session_id: str | None = None
        consumed_at: datetime | None = None

        requesting_device_type: StringSearchModel | None = None
        approver_user_id: StringSearchModel | None = None
        is_used: bool | None = None
        expires_at: DateSearchModel | None = None


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


    _model = DevicePairingRequestModel

    prefix: ClassVar[str | None] = PAIRING_PREFIX
    tags: ClassVar[list[str] | None] = ["Device Pairing Authentication"]
    auth_type: ClassVar[AuthType] = AuthType.NONE
    routes_to_register: ClassVar[list | None] = []

    def _ttl_seconds(self) -> int:
        return int(env("PAIRING_TTL_SECONDS") or 300)

        return env("PAIRING_BASE_URL") or ""

    # ------------------------------------------------------------------
    # Internal state machine
    # ------------------------------------------------------------------

        if row.denied_at is not None:
            return "denied"
        if row.approved_at is not None:
            return "approved"
        expires_at = row.expires_at
        if expires_at:
            expires_at = ensure_utc(expires_at)
        if expires_at and expires_at < datetime.now(UTC):
            return "expired"
        return "pending"

        DB = DevicePairingRequestModel.DB(self.model_registry.DB.manager.Base)
        rows = DB.list(
            model_registry=self.model_registry,
            filters=[DB.id == pairing_id],
            return_type="dto",
            override_dto=DevicePairingRequestModel,
        )
        return rows[0] if rows else None

        # H-5 — indexed fingerprint lookup; bcrypt verifies the unique
        # candidate. The shared resolver owns the fingerprint-narrow ->
        # expiry gate -> constant-time bcrypt confirm.
        DB = DevicePairingRequestModel.DB(self.model_registry.DB.manager.Base)
        return OneTimeTokenMixin.resolve(  # type: ignore[no-any-return]
            DB,
            lambda filters: DB.list(
                model_registry=self.model_registry,
                filters=filters,
                return_type="dto",
                override_dto=DevicePairingRequestModel,
            ),
            raw_token,
        )

    # ------------------------------------------------------------------
    # Public BLL methods (driven by routes below)
    # ------------------------------------------------------------------

        """A new pairing. The response always carries the binding; the route
        moves it into the cookie for a browser."""
        ttl = self._ttl_seconds()
        raw_code, token = OneTimeTokenMixin.generate(ttl_minutes=max(ttl // 60, 1))
        # Override the mixin's expiry with the seconds-precision TTL.
        precise_expiry = datetime.now(UTC) + timedelta(seconds=ttl)

        DB = DevicePairingRequestModel.DB(self.model_registry.DB.manager.Base)
        created = DB.create(
            model_registry=self.model_registry,
            requesting_device_type=device_type,
            requesting_device_name=device_name,
            requesting_ip=client_ip,
            requesting_user_agent=user_agent,
            code_hash=token.code_hash,
            code_salt=token.code_salt,
            code_fingerprint=token.code_fingerprint,
            expires_at=precise_expiry,
            is_used=False,
            binding_hash=_binding_hash(binding),
            token_in_body=token_in_body,
            return_type="dto",
            override_dto=DevicePairingRequestModel,
        )

        base = self._base_url()
        qr_payload = f"{base}/approve?token={raw_code}" if base else raw_code

        return PairingResponse(
            pairing_id=created.id,
            qr_payload=qr_payload,
            expires_in=ttl,
            binding=binding,
        )

        if not approver_user_id:
            raise HTTPException(
                status_code=401, detail="Approval requires an authenticated session"
            )
        row = self._resolve_token(token)
        if row is None:
            raise InvalidGrantError(detail="Invalid or expired pairing token")
        if row.approved_at is not None or row.denied_at is not None:
            raise InvalidGrantError(detail="Pairing already resolved")

        now = datetime.now(UTC)
        DB = DevicePairingRequestModel.DB(self.model_registry.DB.manager.Base)
        DB.update(
            model_registry=self.model_registry,
            id=row.id,
            new_properties={
                "is_used": True,
                "used_at": now,
                "approver_user_id": approver_user_id,
                "approved_at": now,
            },
        )

        session = UserManager.login_via_grant(
            grant_type="device_pairing",
            grant_payload=UserIdGrantPayload(
                user_id=approver_user_id, model_registry=self.model_registry
            ),
            model_registry=self.model_registry,
        )

        # Tag the issued session row with the pending_state="approved" marker
        # for observability and look up its DB id to wire to the pairing row.
            "zephyrex.logic.BLL_Auth", fromlist=["SessionModel"]
        ).SessionModel
        SessionDB = SessionDB_cls.DB(self.model_registry.DB.manager.Base)
        session_rows = SessionDB.list(
            model_registry=self.model_registry,
            filters=[SessionDB.session_key == session.session_key],
            return_type="dto",
            override_dto=SessionDB_cls,
        )
        if session_rows:
            SessionDB.update(
                model_registry=self.model_registry,
                id=session_rows[0].id,
                new_properties={"pending_state": "approved"},
            )
            DB.update(
                model_registry=self.model_registry,
                id=row.id,
                new_properties={"pending_session_id": session_rows[0].id},
            )

        return PairingApproveResponse(
            pairing_id=row.id, state="approved", user_id=session.user_id
        )

        if not approver_user_id:
            raise HTTPException(
                status_code=401, detail="Denial requires an authenticated session"
            )
        row = self._resolve_token(token)
        if row is None:
            raise InvalidGrantError(detail="Invalid or expired pairing token")
        if row.approved_at is not None or row.denied_at is not None:
            raise InvalidGrantError(detail="Pairing already resolved")

        now = datetime.now(UTC)
        DB = DevicePairingRequestModel.DB(self.model_registry.DB.manager.Base)
        DB.update(
            model_registry=self.model_registry,
            id=row.id,
            new_properties={
                "is_used": True,
                "used_at": now,
                "approver_user_id": approver_user_id,
                "denied_at": now,
            },
        )
        return PairingActionResponse(pairing_id=row.id, state="denied")

    def get_status(self, pairing_id: str, binding: str | None = None) -> PairingStatus:
        """The pairing's state. The one read that finds it approved and
        presents its binding also carries the session (``token``,
        ``user_id``); the pairing id alone never does."""
        status, _ = self._read_status(pairing_id, binding)
        return status

        """The status, and whether the session it carries goes in the body."""
        row = self._get_row_by_id(pairing_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Unknown pairing_id")
        status = PairingStatus(pairing_id=pairing_id, state=self._state_for(row))
        if status.state != "approved" or not self._presents_binding(row, binding):
            return status, False
        delivered = self._deliver(row)
        if delivered is None:
            return status, False
        user_id, token = delivered
        return (
            status.model_copy(update={"user_id": user_id, "token": token}),
            row.token_in_body,
        )

        if row.consumed_at is not None or not row.binding_hash or not binding:
            return False

        """Mark the pairing consumed and mint the approved session's token:
        ``(user_id, token)``, or None when the session is gone."""
        if row.pending_session_id is None:
            return None
        DB = DevicePairingRequestModel.DB(self.model_registry.DB.manager.Base)
        DB.update(
            model_registry=self.model_registry,
            id=row.id,
            new_properties={"consumed_at": datetime.now(UTC)},
        )
            "zephyrex.logic.BLL_Auth", fromlist=["SessionModel"]
        ).SessionModel
        SessionDB = SessionDB_cls.DB(self.model_registry.DB.manager.Base)
        session_rows = SessionDB.list(
            model_registry=self.model_registry,
            filters=[SessionDB.id == row.pending_session_id],
            return_type="dto",
            override_dto=SessionDB_cls,
        )
        if not session_rows:
            return None
        session = session_rows[0]
        return session.user_id, UserManager.session_token(session, self.model_registry)

    # ------------------------------------------------------------------
    # SSE generator behind GET /{pairing_id}/stream. Polls the DB once per
    # second; emits a single terminal event carrying state only (a stream's
    # headers are already sent, so it cannot set session cookies) then
    # closes. Max stream lifetime equals the pairing TTL.
    # ------------------------------------------------------------------

        import asyncio

        deadline = datetime.now(UTC) + timedelta(seconds=self._ttl_seconds())
        while datetime.now(UTC) < deadline:
            row = self._get_row_by_id(pairing_id)
            if row is None:
                yield "event: error\ndata: unknown_pairing_id\n\n"
                return
            state = self._state_for(row)
            if state in ("approved", "denied", "expired"):
                status = self.get_status(pairing_id)
                yield (f"event: {state}\n" f"data: {status.model_dump_json()}\n\n")
                return
            await asyncio.sleep(poll_interval)
        yield "event: expired\ndata: timed_out\n\n"

    # ------------------------------------------------------------------
    # Custom routes
    # ------------------------------------------------------------------

    @custom_route(
        method="POST",
        path="/request",
        input_model=PairingRequest,
        output_model=PairingResponse,
        authentication_type="none",
        openapi_tags=("Device Pairing Authentication",),
        summary="Request a new device pairing token (QR payload)",
    )
    @rate_limit(DEFAULT_AUTH_RATE_LIMIT, scope="ip")
    def request_route(
        self, body: PairingRequest, response: Response
    ) -> PairingResponse:
        pairing = self.request_pairing(
            device_type=body.requesting_device_type,
            device_name=body.requesting_device_name,
            token_in_body=body.token_in_body,
        )
        response.set_cookie(
            PAIRING_COOKIE,
            pairing.binding or "",
            max_age=pairing.expires_in,
            **_pairing_cookie_scope(),
        )
        if body.token_in_body:
            return pairing
        return pairing.model_copy(update={"binding": None})

    @custom_route(
        method="POST",
        path="/approve",
        input_model=PairingApprove,
        output_model=PairingApproveResponse,
        authentication_type="session",
        openapi_tags=("Device Pairing Authentication",),
        summary="Approve a scanned pairing token from an authenticated device",
    )
    @rate_limit(DEFAULT_AUTH_RATE_LIMIT, scope="ip")
    def approve_route(self, body: PairingApprove) -> PairingApproveResponse:
        requester = self.optional_requester
        approver_id = requester.id if requester is not None else ""
        return self.approve_pairing(token=body.token, approver_user_id=approver_id)

    @custom_route(
        method="POST",
        path="/deny",
        input_model=PairingDeny,
        output_model=PairingActionResponse,
        authentication_type="session",
        openapi_tags=("Device Pairing Authentication",),
        summary="Deny a scanned pairing token from an authenticated device",
    )
    @rate_limit(DEFAULT_AUTH_RATE_LIMIT, scope="ip")
    def deny_route(self, body: PairingDeny) -> PairingActionResponse:
        requester = self.optional_requester
        approver_id = requester.id if requester is not None else ""
        return self.deny_pairing(token=body.token, approver_user_id=approver_id)

    @custom_route(
        method="GET",
        path="/{pairing_id}/status",
        output_model=PairingStatus,
        authentication_type="none",
        openapi_tags=("Device Pairing Authentication",),
        summary=(
            "Read the pairing state; the requesting device's first approved "
            "read receives its session"
        ),
    )
    def status_route(
        self, pairing_id: str, request: Request, response: Response
    ) -> PairingStatus:
        status, token_in_body = self._read_status(
            pairing_id, request.cookies.get(PAIRING_COOKIE)
        )
        if status.token is None:
            return status
        response.delete_cookie(PAIRING_COOKIE, **_pairing_cookie_scope())
        if token_in_body:
            return status
        issue_browser_session(response, status.token)
        return status.model_copy(update={"token": None})

    @custom_route(
        method="GET",
        path="/{pairing_id}/stream",
        authentication_type="none",
        expose_in=(ExposeIn.REST,),
        response_class=StreamingResponse,
        openapi_tags=("Device Pairing Authentication",),
        summary=(
            "Server-sent event when the pairing resolves (state only; the "
            "session is delivered by a status read)"
        ),
    )
    @rate_limit(DEFAULT_AUTH_RATE_LIMIT, scope="ip")
    def stream_route(self, pairing_id: str) -> StreamingResponse:
        return event_stream(self.stream_status(pairing_id))


# ---------------------------------------------------------------------------
# Grant validator + registration
# ---------------------------------------------------------------------------


# Resolves the approver ``user_id`` to a ``UserModel``; raises
# ``InvalidGrantError`` when the approver no longer exists. The "Approver"
# subject keeps the user-gone message byte-identical to the pre-consolidation
# copy while the payload-missing message stays "Device-pairing".
device_pairing_grant_validator = make_user_id_grant_validator(
    "Device-pairing", subject="Approver"
)

PasswordlessGrantRegistry.register("device_pairing", device_pairing_grant_validator)
