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
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, ClassVar, Dict, List, Optional, Tuple

from zephyrex.lib.DateTimeUtils import ensure_utc

from fastapi import HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from zephyrex.lib.CustomRoute import custom_route
from zephyrex.lib.Environment import env
from zephyrex.lib.InboundSecurity import DEFAULT_AUTH_RATE_LIMIT, rate_limit
from zephyrex.logic.BLL_Auth.user import issue_browser_session
from zephyrex.pydantic2.fastapi import AuthType, RouterMixin
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

PAIRING_PREFIX = "/v1/auth/pairing"
# The requesting device's binding secret, sent only to the pairing routes.
PAIRING_COOKIE = "zx_pairing"
_BINDING_BYTES = 32


def _binding_hash(binding: str) -> str:
    return hashlib.sha256(binding.encode()).hexdigest()


def _pairing_cookie_scope() -> Dict[str, Any]:
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


class PairingRequest(BaseModel):
    requesting_device_type: str = Field(
        ..., description="mobile / desktop / web / unknown"
    )
    requesting_device_name: Optional[str] = Field(
        None, description="Human-friendly device label, e.g. 'Office Chrome'"
    )
    token_in_body: bool = Field(
        False,
        description=(
            "Native clients: return the binding in the body and deliver the "
            "session as a bearer token instead of browser cookies"
        ),
    )


class PairingResponse(BaseModel):
    pairing_id: str
    qr_payload: str
    expires_in: int
    binding: Optional[str] = Field(
        None,
        description=(
            "Present it as the zx_pairing cookie on status reads; returned "
            "only when token_in_body was requested"
        ),
    )


class PairingApprove(BaseModel):
    token: str = Field(..., description="The raw pairing token scanned from the QR")


class PairingDeny(BaseModel):
    token: str = Field(..., description="The raw pairing token to deny")


class PairingStatus(BaseModel):
    pairing_id: str
    state: str  # pending / approved / denied / expired
    # Set only on the read that delivers the session to the requesting
    # device; ``token`` only for a token_in_body pairing.
    user_id: Optional[str] = None
    token: Optional[str] = None


class PairingActionResponse(BaseModel):
    pairing_id: str
    state: str


class PairingApproveResponse(BaseModel):
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
    requesting_device_name: Optional[str] = Field(None)
    requesting_ip: Optional[str] = Field(None)
    requesting_user_agent: Optional[str] = Field(None)
    approver_user_id: Optional[str] = Field(
        None, description="User who approved this pairing, set on approval"
    )
    approved_at: Optional[datetime] = Field(None)
    denied_at: Optional[datetime] = Field(None)
    pending_session_id: Optional[str] = Field(
        None, description="Session row id created at approval time"
    )
    binding_hash: Optional[str] = Field(
        None, description="SHA-256 of the requesting device's binding secret"
    )
    token_in_body: bool = Field(
        False, description="Deliver the session as a bearer token, not cookies"
    )
    consumed_at: Optional[datetime] = Field(
        None, description="When the requesting device took delivery of its session"
    )

    table_comment: ClassVar[str] = (
        "Device pairing requests (Item 59 cross-device passwordless auth)"
    )

    class Create(BaseModel):
        requesting_device_type: str
        requesting_device_name: Optional[str] = None
        requesting_ip: Optional[str] = None
        requesting_user_agent: Optional[str] = None
        code_hash: str
        code_salt: str
        code_fingerprint: Optional[str] = None
        expires_at: datetime
        is_used: bool = False
        used_at: Optional[datetime] = None
        created_ip: Optional[str] = None
        binding_hash: Optional[str] = None
        token_in_body: bool = False

    class Update(BaseModel):
        is_used: Optional[bool] = None
        used_at: Optional[datetime] = None
        approver_user_id: Optional[str] = None
        approved_at: Optional[datetime] = None
        denied_at: Optional[datetime] = None
        pending_session_id: Optional[str] = None
        consumed_at: Optional[datetime] = None

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        requesting_device_type: Optional[StringSearchModel] = None
        approver_user_id: Optional[StringSearchModel] = None
        is_used: Optional[bool] = None
        expires_at: Optional[DateSearchModel] = None


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class DevicePairingManager(AbstractBLLManager, RouterMixin):
    _model = DevicePairingRequestModel

    prefix: ClassVar[Optional[str]] = PAIRING_PREFIX
    tags: ClassVar[Optional[List[str]]] = ["Device Pairing Authentication"]
    auth_type: ClassVar[AuthType] = AuthType.NONE
    routes_to_register: ClassVar[Optional[List]] = []

    def _ttl_seconds(self) -> int:
        return int(env("PAIRING_TTL_SECONDS") or 300)

    def _base_url(self) -> str:
        return env("PAIRING_BASE_URL") or ""

    # ------------------------------------------------------------------
    # Internal state machine
    # ------------------------------------------------------------------

    def _state_for(self, row: DevicePairingRequestModel) -> str:
        if row.denied_at is not None:
            return "denied"
        if row.approved_at is not None:
            return "approved"
        expires_at = row.expires_at
        if expires_at:
            expires_at = ensure_utc(expires_at)
        if expires_at and expires_at < datetime.now(timezone.utc):
            return "expired"
        return "pending"

    def _get_row_by_id(self, pairing_id: str) -> Optional[DevicePairingRequestModel]:
        DB = DevicePairingRequestModel.DB(self.model_registry.DB.manager.Base)
        rows = DB.list(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            filters=[DB.id == pairing_id],
            return_type="dto",
            override_dto=DevicePairingRequestModel,
        )
        return rows[0] if rows else None

    def _resolve_token(self, raw_token: str) -> Optional[DevicePairingRequestModel]:
        # H-5 — indexed fingerprint lookup; bcrypt verifies the unique
        # candidate. The shared resolver owns the fingerprint-narrow ->
        # expiry gate -> constant-time bcrypt confirm.
        DB = DevicePairingRequestModel.DB(self.model_registry.DB.manager.Base)
        return OneTimeTokenMixin.resolve(  # type: ignore[no-any-return]
            DB,
            lambda filters: DB.list(
                requester_id=env("ROOT_ID"),
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

    def request_pairing(
        self,
        device_type: str,
        device_name: Optional[str] = None,
        client_ip: Optional[str] = None,
        user_agent: Optional[str] = None,
        token_in_body: bool = False,
    ) -> PairingResponse:
        """A new pairing. The response always carries the binding; the route
        moves it into the cookie for a browser."""
        ttl = self._ttl_seconds()
        raw_code, token = OneTimeTokenMixin.generate(ttl_minutes=max(ttl // 60, 1))
        binding = secrets.token_urlsafe(_BINDING_BYTES)
        # Override the mixin's expiry with the seconds-precision TTL.
        precise_expiry = datetime.now(timezone.utc) + timedelta(seconds=ttl)

        DB = DevicePairingRequestModel.DB(self.model_registry.DB.manager.Base)
        created = DB.create(
            requester_id=env("SYSTEM_ID"),
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

    def approve_pairing(
        self, token: str, approver_user_id: str
    ) -> PairingApproveResponse:
        if not approver_user_id:
            raise HTTPException(
                status_code=401, detail="Approval requires an authenticated session"
            )
        row = self._resolve_token(token)
        if row is None:
            raise InvalidGrantError(detail="Invalid or expired pairing token")
        if row.approved_at is not None or row.denied_at is not None:
            raise InvalidGrantError(detail="Pairing already resolved")

        now = datetime.now(timezone.utc)
        DB = DevicePairingRequestModel.DB(self.model_registry.DB.manager.Base)
        DB.update(
            requester_id=env("ROOT_ID"),
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
        SessionDB_cls = __import__(
            "zephyrex.logic.BLL_Auth", fromlist=["SessionModel"]
        ).SessionModel
        SessionDB = SessionDB_cls.DB(self.model_registry.DB.manager.Base)
        session_rows = SessionDB.list(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            filters=[SessionDB.session_key == session.session_key],
            return_type="dto",
            override_dto=SessionDB_cls,
        )
        if session_rows:
            SessionDB.update(
                requester_id=env("ROOT_ID"),
                model_registry=self.model_registry,
                id=session_rows[0].id,
                new_properties={"pending_state": "approved"},
            )
            DB.update(
                requester_id=env("ROOT_ID"),
                model_registry=self.model_registry,
                id=row.id,
                new_properties={"pending_session_id": session_rows[0].id},
            )

        return PairingApproveResponse(
            pairing_id=row.id, state="approved", user_id=session.user_id
        )

    def deny_pairing(self, token: str, approver_user_id: str) -> PairingActionResponse:
        if not approver_user_id:
            raise HTTPException(
                status_code=401, detail="Denial requires an authenticated session"
            )
        row = self._resolve_token(token)
        if row is None:
            raise InvalidGrantError(detail="Invalid or expired pairing token")
        if row.approved_at is not None or row.denied_at is not None:
            raise InvalidGrantError(detail="Pairing already resolved")

        now = datetime.now(timezone.utc)
        DB = DevicePairingRequestModel.DB(self.model_registry.DB.manager.Base)
        DB.update(
            requester_id=env("ROOT_ID"),
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

    def get_status(
        self, pairing_id: str, binding: Optional[str] = None
    ) -> PairingStatus:
        """The pairing's state. The one read that finds it approved and
        presents its binding also carries the session (``token``,
        ``user_id``); the pairing id alone never does."""
        status, _ = self._read_status(pairing_id, binding)
        return status

    def _read_status(
        self, pairing_id: str, binding: Optional[str]
    ) -> Tuple[PairingStatus, bool]:
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

    @staticmethod
    def _presents_binding(
        row: DevicePairingRequestModel, binding: Optional[str]
    ) -> bool:
        if row.consumed_at is not None or not row.binding_hash or not binding:
            return False
        return hmac.compare_digest(row.binding_hash, _binding_hash(binding))

    def _deliver(self, row: DevicePairingRequestModel) -> Optional[Tuple[str, str]]:
        """Mark the pairing consumed and mint the approved session's token:
        ``(user_id, token)``, or None when the session is gone."""
        if row.pending_session_id is None:
            return None
        DB = DevicePairingRequestModel.DB(self.model_registry.DB.manager.Base)
        DB.update(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            id=row.id,
            new_properties={"consumed_at": datetime.now(timezone.utc)},
        )
        SessionDB_cls = __import__(
            "zephyrex.logic.BLL_Auth", fromlist=["SessionModel"]
        ).SessionModel
        SessionDB = SessionDB_cls.DB(self.model_registry.DB.manager.Base)
        session_rows = SessionDB.list(
            requester_id=env("ROOT_ID"),
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
    # SSE generator (extracted so it can be unit-tested directly).
    # Polls DB once per second; emits a single terminal event then closes.
    # Max stream lifetime equals the pairing TTL.
    # ------------------------------------------------------------------

    async def stream_status(
        self, pairing_id: str, poll_interval: float = 1.0
    ) -> AsyncIterator[str]:
        import asyncio

        deadline = datetime.now(timezone.utc) + timedelta(seconds=self._ttl_seconds())
        while datetime.now(timezone.utc) < deadline:
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
        summary="Polling fallback: read the current pairing state",
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


# ---------------------------------------------------------------------------
# Streaming endpoint exposed as a stand-alone FastAPI route. (custom_route
# requires a Pydantic output_model; SSE is a raw stream so we register the
# route via a free function on the manager class.)
# ---------------------------------------------------------------------------


def make_stream_endpoint(manager_factory):
    async def stream(pairing_id: str):
        manager: DevicePairingManager = manager_factory()
        return StreamingResponse(
            manager.stream_status(pairing_id),
            media_type="text/event-stream",
        )

    return stream


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
