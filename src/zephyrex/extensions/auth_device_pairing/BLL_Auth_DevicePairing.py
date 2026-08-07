"""Item 59 - QR-code cross-device pairing authentication.

Builds on the framework primitives in ``BLL_Auth.py`` (``OneTimeTokenMixin``,
``PasswordlessGrantRegistry``, ``UserManager.login_via_grant``,
``SessionModel.grant_type``+``pending_state``) to implement the Steam
Guard / Discord cross-device approval flow.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, ClassVar, List, Optional

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from zephyrex.lib.CustomRoute import custom_route
from zephyrex.lib.Environment import env
from zephyrex.lib.InboundSecurity import DEFAULT_AUTH_RATE_LIMIT, rate_limit
from zephyrex.lib.Pydantic2FastAPI import AuthType, RouterMixin
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
    UserManager,
    UserModel,
)


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


class PairingResponse(BaseModel):
    pairing_id: str
    qr_payload: str
    expires_in: int


class PairingApprove(BaseModel):
    token: str = Field(..., description="The raw pairing token scanned from the QR")


class PairingDeny(BaseModel):
    token: str = Field(..., description="The raw pairing token to deny")


class PairingStatus(BaseModel):
    pairing_id: str
    state: str  # pending / approved / denied / expired
    session_key: Optional[str] = None
    user_id: Optional[str] = None


class PairingActionResponse(BaseModel):
    pairing_id: str
    state: str


class PairingApproveResponse(BaseModel):
    pairing_id: str
    state: str
    session_key: str
    user_id: str


class DevicePairingGrantPayload(BaseModel):
    """Payload passed to the registered grant validator. Carries the resolved
    approver ``user_id`` plus the registry needed to look the user up."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    user_id: str
    model_registry: Any = None


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

    class Update(BaseModel):
        is_used: Optional[bool] = None
        used_at: Optional[datetime] = None
        approver_user_id: Optional[str] = None
        approved_at: Optional[datetime] = None
        denied_at: Optional[datetime] = None
        pending_session_id: Optional[str] = None

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

    prefix: ClassVar[Optional[str]] = "/v1/auth/pairing"
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
        if expires_at and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
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

    def _resolve_token(
        self, raw_token: str
    ) -> Optional[DevicePairingRequestModel]:
        # H-5 — indexed fingerprint lookup; bcrypt verifies the unique
        # candidate. Replaces the bcrypt-against-every-unused-token loop.
        DB = DevicePairingRequestModel.DB(self.model_registry.DB.manager.Base)
        fingerprint = OneTimeTokenMixin.fingerprint(raw_token)
        candidates = (
            DB.list(
                requester_id=env("ROOT_ID"),
                model_registry=self.model_registry,
                filters=[
                    DB.is_used == False,  # noqa: E712
                    DB.code_fingerprint == fingerprint,
                ],
                return_type="dto",
                override_dto=DevicePairingRequestModel,
            )
            or []
        )
        now = datetime.now(timezone.utc)
        for row in candidates:
            expires_at = row.expires_at
            if expires_at and expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at < now:
                continue
            if row.verify(raw_token):
                return row  # type: ignore[no-any-return]
        return None

    # ------------------------------------------------------------------
    # Public BLL methods (driven by routes below)
    # ------------------------------------------------------------------

    def request_pairing(
        self,
        device_type: str,
        device_name: Optional[str] = None,
        client_ip: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> PairingResponse:
        ttl = self._ttl_seconds()
        raw_code, token = OneTimeTokenMixin.generate(ttl_minutes=max(ttl // 60, 1))
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
            return_type="dto",
            override_dto=DevicePairingRequestModel,
        )

        base = self._base_url()
        qr_payload = (
            f"{base}/approve?token={raw_code}" if base else raw_code
        )

        return PairingResponse(
            pairing_id=created.id,
            qr_payload=qr_payload,
            expires_in=ttl,
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
            grant_payload=DevicePairingGrantPayload(
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
            pairing_id=row.id,
            state="approved",
            session_key=session.session_key,
            user_id=session.user_id,
        )

    def deny_pairing(
        self, token: str, approver_user_id: str
    ) -> PairingActionResponse:
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

    def get_status(self, pairing_id: str) -> PairingStatus:
        row = self._get_row_by_id(pairing_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Unknown pairing_id")
        state = self._state_for(row)
        if state != "approved" or row.pending_session_id is None:
            return PairingStatus(pairing_id=pairing_id, state=state)

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
            return PairingStatus(pairing_id=pairing_id, state=state)
        s = session_rows[0]
        return PairingStatus(
            pairing_id=pairing_id,
            state=state,
            session_key=s.session_key,
            user_id=s.user_id,
        )

    # ------------------------------------------------------------------
    # SSE generator (extracted so it can be unit-tested directly).
    # Polls DB once per second; emits a single terminal event then closes.
    # Max stream lifetime equals the pairing TTL.
    # ------------------------------------------------------------------

    async def stream_status(
        self, pairing_id: str, poll_interval: float = 1.0
    ) -> AsyncIterator[str]:
        import asyncio

        deadline = datetime.now(timezone.utc) + timedelta(
            seconds=self._ttl_seconds()
        )
        while datetime.now(timezone.utc) < deadline:
            row = self._get_row_by_id(pairing_id)
            if row is None:
                yield "event: error\ndata: unknown_pairing_id\n\n"
                return
            state = self._state_for(row)
            if state in ("approved", "denied", "expired"):
                status = self.get_status(pairing_id)
                yield (
                    f"event: {state}\n"
                    f"data: {status.model_dump_json()}\n\n"
                )
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
    def request_route(self, body: PairingRequest) -> PairingResponse:
        return self.request_pairing(
            device_type=body.requesting_device_type,
            device_name=body.requesting_device_name,
        )

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
        approver_id = self.requester.id if self.requester is not None else ""
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
        approver_id = self.requester.id if self.requester is not None else ""
        return self.deny_pairing(token=body.token, approver_user_id=approver_id)

    @custom_route(
        method="GET",
        path="/{pairing_id}/status",
        output_model=PairingStatus,
        authentication_type="none",
        openapi_tags=("Device Pairing Authentication",),
        summary="Polling fallback: read the current pairing state",
    )
    def status_route(self, pairing_id: str) -> PairingStatus:
        return self.get_status(pairing_id=pairing_id)


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


def device_pairing_grant_validator(
    payload: DevicePairingGrantPayload,
) -> UserModel:
    if payload.model_registry is None:
        raise InvalidGrantError(
            detail="Device-pairing grant payload missing model_registry"
        )
    UserDB = UserModel.DB(payload.model_registry.DB.manager.Base)
    user = UserDB.get(
        requester_id=env("ROOT_ID"),
        model_registry=payload.model_registry,
        id=payload.user_id,
        return_type="dto",
        override_dto=UserModel,
    )
    if user is None:
        raise InvalidGrantError(detail="Approver user no longer exists")
    return user


PasswordlessGrantRegistry.register("device_pairing", device_pairing_grant_validator)
