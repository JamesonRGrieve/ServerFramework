# SPDX-License-Identifier: AGPL-3.0-or-later
"""Item 58 - magic-link (passwordless email) authentication extension.

Walks through the door opened by the framework primitives in ``BLL_Auth.py``:
``OneTimeTokenMixin`` (the hashed-at-rest one-time-token shape),
``PasswordlessGrantRegistry`` (the grant dispatch hook),
``UserManager.login_via_grant`` (the session issuance path), and
``SessionModel.grant_type`` (so issued sessions are observably tagged
``"magic_link"``).
"""

from datetime import datetime, timezone
from typing import Callable, ClassVar, Dict, List, Optional

from fastapi import Response
from pydantic import BaseModel, Field

from zephyrex.lib.CustomRoute import custom_route
from zephyrex.lib import Environment
from zephyrex.lib.Environment import env
from zephyrex.lib.InboundSecurity import DEFAULT_AUTH_RATE_LIMIT, rate_limit
from zephyrex.lib.Logging import logger
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
    UserModel,
    _invitation_hooks,
    make_user_id_grant_validator,
)
from zephyrex.logic.BLL_Auth.user import issue_browser_session

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
    create: bool = Field(
        False,
        description=(
            "Sign up: for an email with no account, where registration allows, send "
            "a link that creates the account when its owner clicks it. An email with "
            "an account gets an ordinary sign-in link either way."
        ),
    )


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
    """Either a session (``token`` and the session fields) or, for a user
    with a verified second factor, an MFA challenge (``mfa_required``,
    ``challenge_token``, ``methods``) to complete at POST
    /v1/user/authorize/mfa, exactly as a password login would."""

    user_id: str
    token: Optional[str] = Field(
        None, description="JWT to present as Authorization: Bearer"
    )
    session_key: Optional[str] = None
    grant_type: Optional[str] = None
    expires_at: Optional[datetime] = None
    mfa_required: bool = False
    challenge_token: Optional[str] = None
    methods: Optional[List[Dict[str, str]]] = None


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

    user_id: Optional[str] = Field(
        None,
        description=(
            "User this token authenticates; none for a sign-up link, whose click "
            "makes the account (so nothing exists for an address until its owner "
            "proves it)"
        ),
    )
    requested_email: str = Field(
        ..., description="The email this token was issued for (binds token to identity)"
    )

    table_comment: ClassVar[str] = (
        "Magic-link one-time tokens (Item 58 passwordless email auth)"
    )

    class Create(BaseModel):
        user_id: Optional[str] = None
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
        users: List[UserModel] = UserDB.list(
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

    def _signup_allowed(self, email: str) -> bool:
        """Whether a sign-up link may create an account for ``email``:
        ``REGISTRATION_MODE`` open, or invite with an invitation pending for
        that address. sso (accounts only through a provider) and closed refuse,
        silently, so the response still says nothing about the address."""
        mode = Environment.settings.REGISTRATION_MODE
        if mode == "open":
            return True
        if mode == "invite":
            pending_for = _invitation_hooks["pending_invitations_for_user"]
            return pending_for is not None and bool(
                pending_for("", email, self.model_registry)
            )
        return False

    def _create_account(self, email: str) -> str:
        """A passwordless account for an email whose owner just proved it by
        clicking a sign-up link; the one already there if the address was
        registered in the meantime."""
        existing = UserManager.user_id_for_verified_email(email, self.model_registry)
        if existing is not None:
            return existing
        if not self._signup_allowed(email):
            raise InvalidGrantError(detail="Sign-up is not open")
        UserDB = UserModel.DB(self.model_registry.DB.manager.Base)
        user: UserModel = UserDB.create(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            override_dto=self.model_registry.apply(UserModel),
            return_type="dto",
            email=email,
        )
        return str(user.id)

    def _send_magic_link(
        self, email: str, raw_token: str, signup: bool = False
    ) -> None:
        """Email the link through the email extension (when this app loaded
        it) and notify any registered listeners, the test seam.

        Mail goes out in the background, so the request never waits on (or
        fails with) the mail provider, and the response stays identical
        whether or not the address has an account."""
        magic_link_url = f"{self._base_url()}?token={raw_token}"
        for listener in list(_send_listeners):
            listener(email=email, magic_link_url=magic_link_url, raw_token=raw_token)
        if "email" not in self.model_registry.loaded_extension_names():
            logger.warning(
                "auth_magic_link: the email extension is not loaded, so the "
                "requested sign-in link cannot be delivered"
            )
            return
        from zephyrex.extensions.email.EXT_EMail import EXT_EMail

        EXT_EMail.queue_email(
            email,
            (
                f"Create your {env('APP_NAME')} account"
                if signup
                else f"Sign in to {env('APP_NAME')}"
            ),
            (
                "Use this link to finish creating your account. "
                if signup
                else "Use this link to sign in. "
            )
            + f"It works once and expires in "
            f"{self._ttl_minutes()} minutes.\n\n{magic_link_url}\n\n"
            "If you did not ask to sign in, you can ignore this email.",
        )

    def _issue(self, email: str, user_id: Optional[str]) -> None:
        """Store a one-time token for ``email`` (and ``user_id``, or
        none for a sign-up, owned by root until the click makes the account)
        and send its link."""
        raw_code, token = OneTimeTokenMixin.generate(ttl_minutes=self._ttl_minutes())
        TokenDB = AuthMagicLinkTokenModel.DB(self.model_registry.DB.manager.Base)
        TokenDB.create(
            requester_id=user_id or env("ROOT_ID"),
            model_registry=self.model_registry,
            user_id=user_id,
            requested_email=email,
            code_hash=token.code_hash,
            code_salt=token.code_salt,
            code_fingerprint=token.code_fingerprint,
            expires_at=token.expires_at,
            is_used=False,
            used_at=None,
            created_ip=None,
        )
        self._send_magic_link(email, raw_code, signup=user_id is None)

    def request_magic_link(self, email: str, create: bool = False) -> MagicLinkResponse:
        """Issue a magic link for ``email`` if a user matches; with ``create``,
        a sign-up link for an email with no account, where registration
        allows. Always returns the same ``accepted`` response - no
        enumeration."""
        user = self._resolve_user_by_email(email)
        if user is None and create:
            address = UserManager._normalize_identifier(email)
            if address and self._signup_allowed(address):
                self._issue(address, None)
        if user is not None:
            self._issue(email, str(user.id))

        return MagicLinkResponse(status="accepted")

    def verify_magic_link(
        self, token: str, email: Optional[str] = None
    ) -> MagicLinkVerifyResponse:
        # H-5 — indexed lookup via the HMAC fingerprint. Replaces the
        # bcrypt-against-every-unused-token loop that was a CPU DoS
        # primitive against unauthenticated callers. The shared resolver
        # owns the fingerprint-narrow -> expiry gate -> constant-time bcrypt
        # confirm; the optional ``email`` becomes an extra narrowing filter.
        TokenDB = AuthMagicLinkTokenModel.DB(self.model_registry.DB.manager.Base)
        extra_filters = [TokenDB.requested_email == email] if email is not None else []
        matched = OneTimeTokenMixin.resolve(
            TokenDB,
            lambda filters: TokenDB.list(
                requester_id=env("ROOT_ID"),
                model_registry=self.model_registry,
                filters=filters,
                return_type="dto",
                override_dto=AuthMagicLinkTokenModel,
            ),
            token,
            extra_filters=extra_filters,
        )

        if matched is None:
            raise InvalidGrantError(detail="Invalid or expired magic-link token")

        # A sign-up link: the click is the proof, so the account is made now.
        user_id = (
            self._create_account(matched.requested_email)
            if matched.user_id is None
            else matched.user_id
        )

        now = datetime.now(timezone.utc)
        TokenDB.update(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            id=matched.id,
            new_properties={"is_used": True, "used_at": now},
        )

        self._invalidate_other_tokens(user_id, matched.id)

        # The link proves the email only; a second factor still stands.
        challenge = UserManager.mfa_challenge(user_id, self.model_registry)
        if challenge is not None:
            return MagicLinkVerifyResponse(user_id=user_id, **challenge)

        session = UserManager.login_via_grant(
            grant_type="magic_link",
            grant_payload=UserIdGrantPayload(
                user_id=user_id, model_registry=self.model_registry
            ),
            model_registry=self.model_registry,
        )

        return MagicLinkVerifyResponse(
            token=UserManager.session_token(session, self.model_registry),
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
        return self.request_magic_link(email=body.email, create=body.create)

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
    def verify_route(
        self, body: MagicLinkVerify, response: Response
    ) -> MagicLinkVerifyResponse:
        """The session in the body for API clients and in the session
        cookies for the browser that opened the link."""
        verified = self.verify_magic_link(token=body.token, email=body.email)
        if verified.token is not None:
            issue_browser_session(response, verified.token)
        return verified


# ---------------------------------------------------------------------------
# Grant validator + registration
# ---------------------------------------------------------------------------


# Resolves the magic-link grant payload to a ``UserModel``; raises
# ``InvalidGrantError`` when the user_id no longer corresponds to an active
# user (e.g. deleted between request and verify). Shared with device pairing
# via the ``make_user_id_grant_validator`` factory.
magic_link_grant_validator = make_user_id_grant_validator("Magic-link")

PasswordlessGrantRegistry.register("magic_link", magic_link_grant_validator)
