"""User-merge BLL.

Records that a *target* user account was consolidated into an *initiating*
user account. Movement of side data is delegated to the canonical managers
in ``zephyrex.logic.BLL_Auth`` (team memberships, here) and to
extension-registered merge handlers (notifications, OAuth links, etc.).

Other extensions participate via :func:`register_merge_handler`. A handler
receives ``(ctx)`` with ``initiating_user_id``, ``target_user_id``,
``model_registry``, and ``requester_id``; it re-homes its own side data
and returns ``None``. Handler exceptions are caught and logged so one
extension's failure does not abort the merge — the audit row is then
marked with the handler errors and the operator can rerun.

Pattern reference: ``auth_invitations/BLL_Invitations.py``.
"""

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, ClassVar, Dict, List, Optional, Type

import jwt
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from zephyrex.lib.CustomRoute import custom_route
from zephyrex.lib.Environment import env
from zephyrex.lib.InboundSecurity import DEFAULT_AUTH_RATE_LIMIT, rate_limit
from zephyrex.lib.Logging import logger
from zephyrex.lib.Pydantic2FastAPI import AuthType, RouteType, RouterMixin
from zephyrex.lib.ReplayCache import get_replay_cache
from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    DateSearchModel,
    ModelMeta,
    StringSearchModel,
    UpdateMixinModel,
)
from zephyrex.logic.BLL_Auth import (
    UserManager,
    UserModel,
    UserTeamManager,
    UserTeamModel,
)


# C-1 — merge target-consent tokens.
#
# A merge absorbs the target user's data and deactivates their account.
# The initiating user alone cannot prove the target consents — they
# could have stolen the target's email or guessed an unlinked
# enumeration. We require a short-lived signed token that the target
# mints from their *own* authenticated session and hands to the
# initiating user, who then submits it on the merge call.
#
# The token is HS256 with ``JWT_SECRET`` (already required at startup),
# carries ``sub=target_user_id`` and ``init=initiating_user_id``, and
# its ``jti`` is burned on first use via the replay cache so a leaked
# token cannot drive a second merge.
_MERGE_CONSENT_TTL_SECONDS = 600
_MERGE_CONSENT_AUD = "auth.user_merge.consent"
_MERGE_CONSENT_KEY_PREFIX = "auth.user_merge.consent.jti:"


def _mint_merge_consent_token(
    *, target_user_id: str, initiating_user_id: str
) -> str:
    """Mint a single-use JWT proving the target consents to be merged
    into the initiating user. Caller is responsible for verifying the
    target's own session before calling this helper."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": target_user_id,
        "init": initiating_user_id,
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=_MERGE_CONSENT_TTL_SECONDS)).timestamp()),
        "aud": _MERGE_CONSENT_AUD,
        "jti": secrets.token_urlsafe(24),
    }
    return jwt.encode(payload, env("JWT_SECRET"), algorithm="HS256")


def _verify_merge_consent_token(
    token: str, *, target_user_id: str, initiating_user_id: str
) -> None:
    """Validate ``token`` and burn its ``jti``. Raises HTTPException 403
    on any failure (forged signature, wrong subject, expired, replayed)."""
    try:
        payload = jwt.decode(
            token,
            env("JWT_SECRET"),
            algorithms=["HS256"],
            audience=_MERGE_CONSENT_AUD,
            leeway=30,
            options={"require": ["exp", "nbf", "iat", "jti", "aud", "sub"]},
        )
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=403, detail=f"Invalid consent token: {exc}")
    if payload.get("sub") != target_user_id:
        raise HTTPException(
            status_code=403, detail="Consent token target does not match request"
        )
    if payload.get("init") != initiating_user_id:
        raise HTTPException(
            status_code=403,
            detail="Consent token initiating user does not match request",
        )
    cache = get_replay_cache()
    jti_key = _MERGE_CONSENT_KEY_PREFIX + payload["jti"]
    if not cache.mark_if_unused(jti_key, ttl_seconds=_MERGE_CONSENT_TTL_SECONDS * 2):
        raise HTTPException(
            status_code=403, detail="Consent token already redeemed"
        )


# ---------------------------------------------------------------------------
# Merge-handler registry (cross-extension participation)
# ---------------------------------------------------------------------------


class MergeContext(BaseModel):
    """Payload passed to every registered merge handler."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    initiating_user_id: str
    target_user_id: str
    requester_id: str
    model_registry: Any = None


MergeHandler = Callable[[MergeContext], None]


_HANDLERS: Dict[str, MergeHandler] = {}


def register_merge_handler(name: str, handler: MergeHandler) -> None:
    """Register a callable that is invoked during ``UserMergeManager.merge_users``.

    The ``name`` is purely diagnostic (it is included in the per-handler
    error report). Re-registering the same name overwrites — extensions
    are expected to register exactly once at boot.
    """
    _HANDLERS[name] = handler


def unregister_merge_handler(name: str) -> None:
    """Remove a registered merge handler. Used by tests and in extension
    teardown paths; production extensions register at boot and never
    unregister."""
    _HANDLERS.pop(name, None)


def list_merge_handlers() -> List[str]:
    return sorted(_HANDLERS.keys())


def _run_merge_handlers(ctx: MergeContext) -> Dict[str, str]:
    """Invoke every registered handler with ``ctx`` and return per-handler
    error strings (empty if every handler succeeded)."""
    errors: Dict[str, str] = {}
    for name, handler in list(_HANDLERS.items()):
        try:
            handler(ctx)
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning(f"Merge handler {name!r} raised: {exc}")
            errors[name] = str(exc)
    return errors


# ---------------------------------------------------------------------------
# Models + manager
# ---------------------------------------------------------------------------


class UserMergeModel(
    ApplicationModel,
    UpdateMixinModel,
    metaclass=ModelMeta,
):
    """Audit record of a user-account merge."""

    Manager: ClassVar[Type["UserMergeManager"]] = None  # type: ignore[assignment]
    initiating_user_id: str = Field(
        ..., description="User who survives the merge (data-owner)"
    )
    target_user_id: str = Field(
        ..., description="User being merged into the initiating user; deactivated."
    )
    completed_at: Optional[datetime] = Field(
        None, description="When the side-data transfer finished"
    )
    handler_errors: Optional[str] = Field(
        None,
        description=(
            "JSON map of handler name -> error string for any merge "
            "handler that raised. Empty/None when every handler succeeded."
        ),
    )

    table_comment: ClassVar[str] = (
        "Audit record of user-account consolidation events"
    )

    class Create(BaseModel):
        initiating_user_id: str
        target_user_id: str

    class Update(BaseModel):
        completed_at: Optional[datetime] = None
        handler_errors: Optional[str] = None

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        initiating_user_id: Optional[StringSearchModel] = None
        target_user_id: Optional[StringSearchModel] = None
        completed_at: Optional[DateSearchModel] = None


class UserMergeRequest(BaseModel):
    initiating_user_id: str = Field(
        ..., description="The surviving user (data-owner)"
    )
    target_user_id: str = Field(
        ..., description="The user being merged in and deactivated"
    )
    consent_token: Optional[str] = Field(
        None,
        description=(
            "Single-use consent token minted by the target user (see "
            "POST /v1/auth/user-merge/consent). Required unless the "
            "caller is ROOT."
        ),
    )


class UserMergeResponse(BaseModel):
    message: str
    merge_id: str
    handler_errors: str = ""


class UserMergeConsentRequest(BaseModel):
    initiating_user_id: str = Field(
        ...,
        description=(
            "The surviving user the caller agrees to be merged into. "
            "The caller (target) must be authenticated as themselves; "
            "this endpoint mints a short-lived single-use token."
        ),
    )


class UserMergeConsentResponse(BaseModel):
    consent_token: str
    expires_in_seconds: int


class UserMergeManager(AbstractBLLManager, RouterMixin):
    _model = UserMergeModel
    prefix: ClassVar[Optional[str]] = "/v1/auth/user-merge"
    tags: ClassVar[Optional[List[str]]] = ["User Merge"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    # Audit rows are read-only over CRUD. Merges happen via the explicit
    # ``/merge`` endpoint, which is authorization-gated.
    routes_to_register: ClassVar[Optional[List[RouteType]]] = [
        RouteType.GET,
        RouteType.LIST,
        RouteType.SEARCH,
    ]

    def create_validation(self, entity) -> None:
        """Refuse direct CREATE — audit rows are minted by ``merge_users``."""
        from zephyrex.database.StaticPermissions import is_root_id

        if not is_root_id(self.requester.id):
            raise HTTPException(
                status_code=403,
                detail="Direct CREATE on /v1/auth/user-merge is not permitted; "
                "use POST /v1/auth/user-merge/merge",
            )

    def _assert_can_merge(
        self,
        initiating_user_id: str,
        target_user_id: str,
        consent_token: Optional[str],
    ) -> None:
        """Authorization for a merge call.

        - ROOT_ID may merge anyone (operator path; bypasses target consent).
        - Otherwise the caller MUST be the initiating user AND present a
          valid single-use consent token minted by the target. Without
          the consent token an authenticated user could absorb any other
          user's data into their own account (C-1).
        """
        from zephyrex.database.StaticPermissions import is_root_id

        if is_root_id(self.requester.id):
            return
        if self.requester.id != initiating_user_id:
            raise HTTPException(
                status_code=403,
                detail=(
                    "Only the initiating (surviving) user can drive the merge"
                ),
            )
        if not consent_token:
            raise HTTPException(
                status_code=403,
                detail=(
                    "Target user consent token is required; the target must "
                    "POST /v1/auth/user-merge/consent first while logged in"
                ),
            )
        _verify_merge_consent_token(
            consent_token,
            target_user_id=target_user_id,
            initiating_user_id=initiating_user_id,
        )

    def _validate(self, initiating_user_id: str, target_user_id: str) -> None:
        if initiating_user_id == target_user_id:
            raise HTTPException(
                status_code=400, detail="Cannot merge a user with themselves"
            )
        UserDB = UserModel.DB(self.model_registry.DB.manager.Base)
        for user_id, role in (
            (initiating_user_id, "initiating"),
            (target_user_id, "target"),
        ):
            if (
                UserDB.get(
                    requester_id=env("ROOT_ID"),
                    model_registry=self.model_registry,
                    id=user_id,
                    return_type="dto",
                    override_dto=UserModel,
                )
                is None
            ):
                raise HTTPException(
                    status_code=404, detail=f"{role} user not found: {user_id}"
                )

    def merge_users(
        self,
        initiating_user_id: str,
        target_user_id: str,
        consent_token: Optional[str] = None,
    ) -> Dict[str, str]:
        """Merge ``target_user_id`` into ``initiating_user_id``.

        Authorization: caller is the initiating user AND presents a
        valid single-use ``consent_token`` minted by the target, OR the
        caller is ROOT_ID. Side-data transfer:
        - Team memberships are re-homed via ``UserTeamManager``.
        - Every registered merge handler (see :func:`register_merge_handler`)
          is invoked; handler exceptions are caught, logged, and recorded
          on the audit row but do not abort the merge.
        - The target user is deactivated.
        """
        import json

        self._assert_can_merge(initiating_user_id, target_user_id, consent_token)
        self._validate(initiating_user_id, target_user_id)

        UserMergeDB = UserMergeModel.DB(self.model_registry.DB.manager.Base)
        merge = UserMergeDB.create(
            requester_id=self.requester.id,
            model_registry=self.model_registry,
            return_type="dto",
            override_dto=UserMergeModel,
            initiating_user_id=initiating_user_id,
            target_user_id=target_user_id,
        )

        UserTeamDB = UserTeamModel.DB(self.model_registry.DB.manager.Base)
        target_memberships = (
            UserTeamDB.list(
                requester_id=env("ROOT_ID"),
                model_registry=self.model_registry,
                filters=[UserTeamDB.user_id == target_user_id],
                return_type="dto",
                override_dto=UserTeamModel,
            )
            or []
        )
        initiating_memberships = {
            m.team_id: m
            for m in (
                UserTeamDB.list(
                    requester_id=env("ROOT_ID"),
                    model_registry=self.model_registry,
                    filters=[UserTeamDB.user_id == initiating_user_id],
                    return_type="dto",
                    override_dto=UserTeamModel,
                )
                or []
            )
        }

        ut_manager = UserTeamManager(
            requester_id=self.requester.id, model_registry=self.model_registry
        )
        for membership in target_memberships:
            if membership.team_id not in initiating_memberships:
                ut_manager.create(
                    user_id=initiating_user_id,
                    team_id=membership.team_id,
                    role_id=membership.role_id,
                )

        # Run extension-registered merge handlers. Failures don't abort the
        # merge — they're recorded so the operator can investigate without
        # leaving the audit row in an indeterminate state.
        ctx = MergeContext(
            initiating_user_id=initiating_user_id,
            target_user_id=target_user_id,
            requester_id=self.requester.id,
            model_registry=self.model_registry,
        )
        handler_errors = _run_merge_handlers(ctx)

        UserDB = UserModel.DB(self.model_registry.DB.manager.Base)
        UserDB.update(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            id=target_user_id,
            new_properties={"active": False},
        )

        UserMergeDB.update(
            requester_id=self.requester.id,
            model_registry=self.model_registry,
            id=merge.id,
            new_properties={
                "completed_at": datetime.utcnow(),
                "handler_errors": (
                    json.dumps(handler_errors) if handler_errors else None
                ),
            },
        )
        return {
            "message": (
                f"Successfully merged user {target_user_id} into {initiating_user_id}"
            ),
            "merge_id": merge.id,
            "handler_errors": json.dumps(handler_errors) if handler_errors else "",
        }

    @custom_route(
        method="POST",
        path="/merge",
        input_model=UserMergeRequest,
        output_model=UserMergeResponse,
        authentication_type="jwt",
        openapi_tags=("User Merge",),
        summary="Merge target user into initiating user",
    )
    @rate_limit(DEFAULT_AUTH_RATE_LIMIT, scope="ip")
    async def merge_route(self, body: UserMergeRequest) -> UserMergeResponse:
        result = self.merge_users(
            initiating_user_id=body.initiating_user_id,
            target_user_id=body.target_user_id,
            consent_token=body.consent_token,
        )
        return UserMergeResponse(
            message=result["message"],
            merge_id=result["merge_id"],
            handler_errors=result.get("handler_errors", ""),
        )

    @custom_route(
        method="POST",
        path="/consent",
        input_model=UserMergeConsentRequest,
        output_model=UserMergeConsentResponse,
        authentication_type="jwt",
        openapi_tags=("User Merge",),
        summary="Mint a target-user consent token for a pending merge",
    )
    @rate_limit(DEFAULT_AUTH_RATE_LIMIT, scope="ip")
    async def consent_route(
        self, body: UserMergeConsentRequest
    ) -> UserMergeConsentResponse:
        """The *target* user calls this from their own session to authorise
        being merged into ``initiating_user_id``. The returned token is
        single-use and short-lived (10 minutes); the initiating user
        submits it on POST /merge to prove consent (C-1)."""
        if self.requester is None or self.requester.id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        if self.requester.id == body.initiating_user_id:
            raise HTTPException(
                status_code=400,
                detail="Cannot consent to a merge into yourself",
            )
        token = _mint_merge_consent_token(
            target_user_id=self.requester.id,
            initiating_user_id=body.initiating_user_id,
        )
        return UserMergeConsentResponse(
            consent_token=token,
            expires_in_seconds=_MERGE_CONSENT_TTL_SECONDS,
        )


UserMergeModel.Manager = UserMergeManager
