"""Item 58 - magic-link (passwordless email) authentication extension.

Walks through the door opened by the framework primitives in ``BLL_Auth.py``:
``OneTimeTokenMixin`` (the hashed-at-rest one-time-token shape),
``PasswordlessGrantRegistry`` (the grant dispatch hook),
``UserManager.login_via_grant`` (the session issuance path), and
``SessionModel.grant_type`` (so issued sessions are observably tagged
``"magic_link"``).
"""

from datetime import datetime, timezone
from typing import Any, Callable, ClassVar, List, Optional

from zephyrex.lib.DateTimeUtils import ensure_utc

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
# Test-visible email-send listeners. Tests register a callable here to capture
# the magic link that would otherwise reach the user via EXT_Email.
# ---------------------------------------------------------------------------

_send_listeners: List[Callable[..., None]] = []


def register_send_listener(listener: Callable[..., None]) -> None:
    _send_listeners.append(listener)


def clear_send_listeners() -> None:
    _send_listeners.clear()


# ---------------------------------------------------------------------------
# Pydantic input/output models
# ---------------------------------------------------------------------------


class MagicLinkRequest(BaseModel):
    email: str = Field(..., description="The email address to send a magic link to")


class MagicLinkResponse(BaseModel):
    """Identical shape regardless of whether the email is registered, so the
    response cannot be used to enumerate accounts."""

    status: str = Field("accepted", description="Always 'accepted'")


class MagicLinkVerify(BaseModel):
    token: str = Field(..., description="The raw magic-link token from the email URL")
    email: Optional[str] = Field(
        None,
        description="Optional email to disambiguate when multiple tokens may match",
    )


class MagicLinkVerifyResponse(BaseModel):
    session_key: str
    user_id: str
    grant_type: str
    expires_at: datetime


class MagicLinkGrantPayload(BaseModel):
    """Typed payload passed to the registered grant validator.

    Carries the ``model_registry`` along with the resolved ``user_id`` so the
    validator does not need a thread-local or post-load monkey-patch to recover
    the registry on dispatch.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    user_id: str
    model_registry: Any = None


# ---------------------------------------------------------------------------
# Database model
# ---------------------------------------------------------------------------


class AuthMagicLinkTokenModel(
    ApplicationModel,
    UpdateMixinModel,
    OneTimeTokenMixin,
    metaclass=ModelMeta,
):
    """Magic-link tokens. Stored hashed; the raw code reaches the user only
    via email."""

    user_id: str = Field(..., description="User this token authenticates")
    requested_email: str = Field(
        ..., description="The email this token was issued for (binds token to identity)"
    )

    table_comment: ClassVar[str] = (
        "Magic-link one-time tokens (Item 58 passwordless email auth)"
    )

    class Create(BaseModel):
        user_id: str
        requested_email: str
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

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        user_id: Optional[StringSearchModel] = None
        requested_email: Optional[StringSearchModel] = None
        is_used: Optional[bool] = None
        expires_at: Optional[DateSearchModel] = None


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class MagicLinkManager(AbstractBLLManager, RouterMixin):
    _model = AuthMagicLinkTokenModel

    prefix: ClassVar[Optional[str]] = "/v1/auth/magic-link"
    tags: ClassVar[Optional[List[str]]] = ["Magic Link Authentication"]
    auth_type: ClassVar[AuthType] = AuthType.NONE
    routes_to_register: ClassVar[Optional[List]] = []  # No auto CRUD endpoints

    def _ttl_minutes(self) -> int:
        return int(env("MAGIC_LINK_TTL_MINUTES") or 15)

    def _base_url(self) -> str:
        return env("MAGIC_LINK_BASE_URL") or ""

    def _resolve_user_by_email(self, email: str) -> Optional[UserModel]:
        UserDB = UserModel.DB(self.model_registry.DB.manager.Base)
        users = UserDB.list(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            filters=[
                UserDB.email == email,
                UserDB.deleted_at.is_(None),
            ],
            return_type="dto",
            override_dto=UserModel,
        )
        if not users:
            return None
        return users[0]

    def _send_magic_link(self, email: str, raw_token: str) -> None:
        """Notify any registered listeners and (best-effort) deliver via
        EXT_Email if available.

        The listener loop is the test seam. Production deployments configure
        EXT_Email; tests register a listener that captures ``raw_token``.
        """
        magic_link_url = f"{self._base_url()}?token={raw_token}"
        for listener in list(_send_listeners):
            listener(email=email, magic_link_url=magic_link_url, raw_token=raw_token)

    def request_magic_link(self, email: str) -> MagicLinkResponse:
        """Issue a magic link for ``email`` if a user matches. Always returns
        the same ``accepted`` response - no enumeration."""
        user = self._resolve_user_by_email(email)
        if user is not None:
            raw_code, token = OneTimeTokenMixin.generate(
                ttl_minutes=self._ttl_minutes()
            )
            TokenDB = AuthMagicLinkTokenModel.DB(self.model_registry.DB.manager.Base)
            TokenDB.create(
                requester_id=user.id,
                model_registry=self.model_registry,
                user_id=user.id,
                requested_email=email,
                code_hash=token.code_hash,
                code_salt=token.code_salt,
                code_fingerprint=token.code_fingerprint,
                expires_at=token.expires_at,
                is_used=False,
                used_at=None,
                created_ip=None,
            )
            self._send_magic_link(email, raw_code)

        return MagicLinkResponse(status="accepted")

    def verify_magic_link(
        self, token: str, email: Optional[str] = None
    ) -> MagicLinkVerifyResponse:
        # H-5 — indexed lookup via the HMAC fingerprint. Replaces the
        # bcrypt-against-every-unused-token loop that was a CPU DoS
        # primitive against unauthenticated callers.
        TokenDB = AuthMagicLinkTokenModel.DB(self.model_registry.DB.manager.Base)
        fingerprint = OneTimeTokenMixin.fingerprint(token)
        filters = [
            TokenDB.is_used == False,  # noqa: E712
            TokenDB.code_fingerprint == fingerprint,
        ]
        if email is not None:
            filters.append(TokenDB.requested_email == email)

        candidates = (
            TokenDB.list(
                requester_id=env("ROOT_ID"),
                model_registry=self.model_registry,
                filters=filters,
                return_type="dto",
                override_dto=AuthMagicLinkTokenModel,
            )
            or []
        )

        now = datetime.now(timezone.utc)
        matched: Optional[AuthMagicLinkTokenModel] = None
        for candidate in candidates:
            expires_at = candidate.expires_at
            if expires_at:
                expires_at = ensure_utc(expires_at)
            if expires_at < now:
                continue
            # Constant-time bcrypt confirm — the fingerprint narrows the
            # candidate set to ~1 row; bcrypt is the authoritative match.
            if candidate.verify(token):
                matched = candidate
                break

        if matched is None:
            raise InvalidGrantError(detail="Invalid or expired magic-link token")

        TokenDB.update(
            requester_id=matched.user_id,
            model_registry=self.model_registry,
            id=matched.id,
            new_properties={"is_used": True, "used_at": now},
        )

        self._invalidate_other_tokens(matched.user_id, matched.id)

        session = UserManager.login_via_grant(
            grant_type="magic_link",
            grant_payload=MagicLinkGrantPayload(
                user_id=matched.user_id, model_registry=self.model_registry
            ),
            model_registry=self.model_registry,
        )

        return MagicLinkVerifyResponse(
            session_key=session.session_key,
            user_id=session.user_id,
            grant_type=session.grant_type or "magic_link",
            expires_at=session.expires_at,
        )

    def _invalidate_other_tokens(self, user_id: str, current_id: str) -> None:
        TokenDB = AuthMagicLinkTokenModel.DB(self.model_registry.DB.manager.Base)
        tokens = TokenDB.list(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            filters=[
                TokenDB.user_id == user_id,
                TokenDB.is_used == False,  # noqa: E712
                TokenDB.id != current_id,
            ],
            return_type="dto",
            override_dto=AuthMagicLinkTokenModel,
        )
        now = datetime.now(timezone.utc)
        for t in tokens or []:
            TokenDB.update(
                requester_id=env("ROOT_ID"),
                model_registry=self.model_registry,
                id=t.id,
                new_properties={"is_used": True, "used_at": now},
            )

    @custom_route(
        method="POST",
        path="/request",
        input_model=MagicLinkRequest,
        output_model=MagicLinkResponse,
        authentication_type="none",
        openapi_tags=("Magic Link Authentication",),
        summary="Request a magic-link sign-in email",
    )
    @rate_limit(DEFAULT_AUTH_RATE_LIMIT, scope="ip")
    def request_route(self, body: MagicLinkRequest) -> MagicLinkResponse:
        return self.request_magic_link(email=body.email)

    @custom_route(
        method="POST",
        path="/verify",
        input_model=MagicLinkVerify,
        output_model=MagicLinkVerifyResponse,
        authentication_type="none",
        openapi_tags=("Magic Link Authentication",),
        summary="Redeem a magic-link token for a session",
    )
    @rate_limit(DEFAULT_AUTH_RATE_LIMIT, scope="ip")
    def verify_route(self, body: MagicLinkVerify) -> MagicLinkVerifyResponse:
        return self.verify_magic_link(token=body.token, email=body.email)


# ---------------------------------------------------------------------------
# Grant validator + registration
# ---------------------------------------------------------------------------


def magic_link_grant_validator(payload: MagicLinkGrantPayload) -> UserModel:
    """Resolve the magic-link grant payload to a ``UserModel``.

    Raises ``InvalidGrantError`` when the user_id no longer corresponds to
    an active user (e.g. user deleted between request and verify).
    """
    if payload.model_registry is None:
        raise InvalidGrantError(
            detail="Magic-link grant payload missing model_registry"
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
        raise InvalidGrantError(detail="Magic-link user no longer exists")
    return user


PasswordlessGrantRegistry.register("magic_link", magic_link_grant_validator)
