"""Item 58 — magic-link (passwordless email) authentication extension.

Walks through the door opened by the framework primitives in
``BLL_Auth.py``: ``OneTimeTokenMixin`` (the hashed-at-rest one-time-token
shape), ``PasswordlessGrantRegistry`` (the grant dispatch hook),
``UserManager.login_via_grant`` (the session issuance path), and
``SessionModel.grant_type`` (so issued sessions are observably tagged
``"magic_link"``).
"""

from datetime import datetime, timezone
from typing import Any, ClassVar, List, Optional

from fastapi import HTTPException
from pydantic import BaseModel, Field

from serverframework.lib.Logging import logger
from serverframework.lib.Pydantic2FastAPI import AuthType, RouterMixin
from serverframework.lib.CustomRoute import custom_route
from serverframework.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    DateSearchModel,
    ModelMeta,
    StringSearchModel,
    UpdateMixinModel,
)
from serverframework.logic.BLL_Auth import (
    InvalidGrantError,
    OneTimeTokenMixin,
    PasswordlessGrantRegistry,
    SessionModel,
    UserManager,
    UserModel,
)


# ---------------------------------------------------------------------------
# Pydantic input/output models
# ---------------------------------------------------------------------------


class MagicLinkRequest(BaseModel):
    """POST /v1/auth/magic-link/request body."""

    email: str = Field(..., description="The email address to send a magic link to")


class MagicLinkResponse(BaseModel):
    """POST /v1/auth/magic-link/request response (always identical regardless of registration)."""

    status: str = Field(
        "accepted",
        description="Constant 'accepted' — never reveals whether the email is registered",
    )


class MagicLinkVerify(BaseModel):
    """POST /v1/auth/magic-link/verify body."""

    token: str = Field(..., description="The raw magic-link token from the email URL")
    email: Optional[str] = Field(
        None,
        description="Optional email to disambiguate when multiple tokens may match",
    )


class MagicLinkVerifyResponse(BaseModel):
    """POST /v1/auth/magic-link/verify response — the issued session."""

    session_key: str
    user_id: str
    grant_type: str
    expires_at: datetime


class MagicLinkGrantPayload(BaseModel):
    """Typed payload passed to the registered grant validator."""

    user_id: str


# ---------------------------------------------------------------------------
# Database model
# ---------------------------------------------------------------------------


class AuthMagicLinkTokenModel(
    ApplicationModel,
    UpdateMixinModel,
    OneTimeTokenMixin,
    metaclass=ModelMeta,
):
    """Magic-link tokens. Stored hashed; raw code reaches the user only via email."""

    user_id: str = Field(..., description="User this token authenticates")
    requested_email: str = Field(
        ..., description="The email this token was issued for (binds token to identity)"
    )

    table_comment: ClassVar[str] = (
        "Magic-link one-time tokens (Item 58 passwordless email auth)"
    )

    class Create(BaseModel):
        user_id: str = Field(..., description="User this token authenticates")
        requested_email: str = Field(..., description="Email the token was issued for")
        code_hash: str = Field(..., description="bcrypt hash of the raw code")
        code_salt: str = Field(..., description="Salt used when hashing the code")
        expires_at: datetime = Field(..., description="UTC expiry timestamp")
        is_used: bool = Field(False, description="Whether this token has been redeemed")
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

    def __init__(
        self,
        requester_id: Optional[str] = None,
        target_id: Optional[str] = None,
        target_team_id: Optional[str] = None,
        model_registry: Any = None,
    ) -> None:
        super().__init__(
            requester_id=requester_id,
            target_id=target_id,
            target_team_id=target_team_id,
            model_registry=model_registry,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_user_by_email(self, email: str) -> Optional[Any]:
        """Look up a user by email. Returns None when no match."""
        from serverframework.lib.Environment import env as _env

        try:
            users = UserModel.DB(self.model_registry.DB.manager.Base).list(
                requester_id=_env("ROOT_ID"),
                model_registry=self.model_registry,
                filters=[
                    UserModel.DB(self.model_registry.DB.manager.Base).email == email,
                    UserModel.DB(self.model_registry.DB.manager.Base).deleted_at.is_(
                        None
                    ),
                ],
                return_type="dto",
                override_dto=UserModel,
            )
        except Exception as exc:  # noqa: BLE001 — surfaced as silent no-match
            logger.debug(f"Magic-link user lookup failed for {email!r}: {exc}")
            return None
        if not users:
            return None
        # Pydantic list result; pick first (email is unique in practice)
        return users[0]

    def _send_magic_link_email(self, email: str, raw_token: str) -> None:
        """Render the templated link and dispatch via EXT_Email if available.

        The email-send call is best-effort: failure to deliver does not
        leak through to the response (which always reports the same
        "accepted" status regardless). All such failures are logged.
        """
        from serverframework.extensions.auth_magic_link.EXT_Auth_MagicLink import (
            EXT_Auth_MagicLink,
        )

        base_url = EXT_Auth_MagicLink.get_env_value("MAGIC_LINK_BASE_URL")
        template = EXT_Auth_MagicLink.get_env_value(
            "MAGIC_LINK_EMAIL_TEMPLATE"
        ) or EXT_Auth_MagicLink.DEFAULT_EMAIL_TEMPLATE

        magic_link_url = f"{base_url}?token={raw_token}"
        body = template.format(magic_link_url=magic_link_url)

        # Notify any registered side-effect listeners (test-only fake provider
        # plugs in here without forcing a real email transport in tests).
        for listener in EXT_Auth_MagicLink._send_listeners:
            try:
                listener(email=email, magic_link_url=magic_link_url, body=body)
            except Exception as exc:  # noqa: BLE001
                logger.error(f"Magic-link send listener raised: {exc}")

        # Best-effort production send via EXT_Email rotation. Silenced
        # whenever no email rotation is configured (test environments).
        try:
            from serverframework.extensions.email.EXT_EMail import EXT_EMail

            if EXT_EMail.root is not None:
                # Fire-and-forget — magic-link delivery should not block
                # the request thread on SMTP latency.
                import asyncio

                async def _send_async():
                    await EXT_EMail.send_email(
                        recipient=email,
                        subject="Your magic sign-in link",
                        body=body,
                    )

                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        asyncio.ensure_future(_send_async())
                    else:
                        loop.run_until_complete(_send_async())
                except RuntimeError:
                    # No running loop; spin one up briefly
                    asyncio.run(_send_async())
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"EXT_Email magic-link send skipped: {exc}")

    def _invalidate_other_tokens(self, user_id: str, current_id: str) -> None:
        """Mark every other unused token for ``user_id`` as used so a
        successful verify retires older outstanding magic links."""
        TokenDB = AuthMagicLinkTokenModel.DB(self.model_registry.DB.manager.Base)
        tokens = TokenDB.list(
            requester_id=user_id,
            model_registry=self.model_registry,
            filters=[
                TokenDB.user_id == user_id,
                TokenDB.is_used == False,  # noqa: E712
                TokenDB.id != current_id,
            ],
            return_type="dto",
            override_dto=AuthMagicLinkTokenModel,
        )
        for t in tokens or []:
            tid = t.id if hasattr(t, "id") else t.get("id")
            try:
                TokenDB.update(
                    requester_id=user_id,
                    model_registry=self.model_registry,
                    id=tid,
                    fields={
                        "is_used": True,
                        "used_at": datetime.now(timezone.utc),
                    },
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"Failed to invalidate sibling magic-link {tid}: {exc}")

    # ------------------------------------------------------------------
    # Public BLL methods (wired into custom routes below)
    # ------------------------------------------------------------------

    def request_magic_link(self, email: str) -> MagicLinkResponse:
        """Issue a magic link for ``email`` if a user matches. Always
        returns the same ``accepted`` response shape — no enumeration."""
        from serverframework.extensions.auth_magic_link.EXT_Auth_MagicLink import (
            EXT_Auth_MagicLink,
        )

        ttl = int(EXT_Auth_MagicLink.get_env_value("MAGIC_LINK_TTL_MINUTES") or 15)

        user = self._resolve_user_by_email(email)
        if user is not None:
            raw_code, token = OneTimeTokenMixin.generate(ttl_minutes=ttl)
            user_id = user.id if hasattr(user, "id") else user["id"]
            TokenDB = AuthMagicLinkTokenModel.DB(self.model_registry.DB.manager.Base)
            TokenDB.create(
                requester_id=user_id,
                model_registry=self.model_registry,
                user_id=user_id,
                requested_email=email,
                code_hash=token.code_hash,
                code_salt=token.code_salt,
                expires_at=token.expires_at,
                is_used=False,
                used_at=None,
                created_ip=None,
            )
            self._send_magic_link_email(email, raw_code)

        return MagicLinkResponse(status="accepted")

    def verify_magic_link(
        self, token: str, email: Optional[str] = None
    ) -> MagicLinkVerifyResponse:
        """Validate ``token`` against an outstanding row and issue a session."""
        TokenDB = AuthMagicLinkTokenModel.DB(self.model_registry.DB.manager.Base)
        from serverframework.lib.Environment import env as _env

        filters = [TokenDB.is_used == False]  # noqa: E712
        if email is not None:
            filters.append(TokenDB.requested_email == email)

        candidates = TokenDB.list(
            requester_id=_env("ROOT_ID"),
            model_registry=self.model_registry,
            filters=filters,
            return_type="dto",
            override_dto=AuthMagicLinkTokenModel,
        ) or []

        now = datetime.now(timezone.utc)
        matched: Optional[AuthMagicLinkTokenModel] = None
        for candidate in candidates:
            # Reject expired up-front
            expires_at = candidate.expires_at
            if expires_at and expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at < now:
                continue
            # Reject when caller-provided email mismatches stored requested_email
            if email is not None and candidate.requested_email != email:
                continue
            if candidate.verify(token):
                matched = candidate
                break

        if matched is None:
            raise InvalidGrantError(detail="Invalid or expired magic-link token")

        # Mark as used immediately
        TokenDB.update(
            requester_id=matched.user_id,
            model_registry=self.model_registry,
            id=matched.id,
            fields={
                "is_used": True,
                "used_at": now,
            },
        )

        # Invalidate any other outstanding tokens for the same user
        self._invalidate_other_tokens(matched.user_id, matched.id)

        # Issue a session via the framework's grant dispatch
        session = UserManager.login_via_grant(
            grant_type="magic_link",
            grant_payload=MagicLinkGrantPayload(user_id=matched.user_id),
            model_registry=self.model_registry,
        )

        return MagicLinkVerifyResponse(
            session_key=session.session_key,
            user_id=session.user_id,
            grant_type=session.grant_type or "magic_link",
            expires_at=session.expires_at,
        )

    # ------------------------------------------------------------------
    # Custom routes
    # ------------------------------------------------------------------

    @custom_route(
        method="POST",
        path="/request",
        input_model=MagicLinkRequest,
        output_model=MagicLinkResponse,
        authentication_type="none",
        openapi_tags=("Magic Link Authentication",),
        summary="Request a magic-link sign-in email",
        description=(
            "Submit an email; if it is registered, a one-time link is "
            "delivered via email. The response is identical regardless of "
            "whether the email is registered, to prevent user enumeration."
        ),
    )
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
        description=(
            "Verify a magic-link token and exchange it for an active "
            "session. The token is single-use; replay fails."
        ),
    )
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
    # The validator runs inside ``UserManager.login_via_grant`` which is
    # passed a ``model_registry``; we recover it via the framework's
    # active-context helper.
    from serverframework.lib.Environment import env as _env
    from serverframework.logic.BLL_Auth import UserModel as _UserModel

    # ``login_via_grant`` validates and immediately calls ``_issue_session``
    # against the registry it received; the registry is in scope of the
    # caller. The cleanest way to access it without threading a fourth
    # parameter through the registry signature is to look it up via the
    # _MagicLinkRegistryContext singleton, which the manager populates
    # before dispatch.
    context = _MagicLinkRegistryContext.current
    if context is None or context.get("model_registry") is None:
        raise InvalidGrantError(
            detail="Magic-link validator invoked outside a registry context"
        )
    model_registry = context["model_registry"]

    user = _UserModel.DB(model_registry.DB.manager.Base).get(
        requester_id=_env("ROOT_ID"),
        model_registry=model_registry,
        id=payload.user_id,
        return_type="dto",
        override_dto=_UserModel,
    )
    if user is None:
        raise InvalidGrantError(detail="Magic-link user no longer exists")
    return user


class _MagicLinkRegistryContext:
    """Process-local context for passing the model_registry into the
    grant validator. ``UserManager.login_via_grant``'s registered
    validator signature is ``(payload) -> UserModel`` so we do not get
    direct access to the registry — the manager sets the context before
    dispatch and clears it afterwards."""

    current: ClassVar[Optional[dict]] = None


# Patch the manager's verify path to set the context around dispatch.
_original_verify = MagicLinkManager.verify_magic_link


def _verify_with_context(self, token: str, email: Optional[str] = None):
    _MagicLinkRegistryContext.current = {"model_registry": self.model_registry}
    try:
        return _original_verify(self, token=token, email=email)
    finally:
        _MagicLinkRegistryContext.current = None


MagicLinkManager.verify_magic_link = _verify_with_context


# Register the validator at import time so the framework's
# ``PasswordlessGrantRegistry`` recognises ``"magic_link"``.
PasswordlessGrantRegistry.register("magic_link", magic_link_grant_validator)
