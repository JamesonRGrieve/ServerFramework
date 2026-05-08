"""User-merge BLL.

Records that a *target* user account was consolidated into an *initiating*
user account. Movement of side data is delegated to the canonical managers
in ``serverframework.logic.BLL_Auth`` (team memberships, here) and to
extension-registered merge handlers (notifications, OAuth links, etc.).

Other extensions participate via :func:`register_merge_handler`. A handler
receives ``(ctx)`` with ``initiating_user_id``, ``target_user_id``,
``model_registry``, and ``requester_id``; it re-homes its own side data
and returns ``None``. Handler exceptions are caught and logged so one
extension's failure does not abort the merge — the audit row is then
marked with the handler errors and the operator can rerun.

Pattern reference: ``auth_invitations/BLL_Invitations.py``.
"""

from datetime import datetime
from typing import Any, Callable, ClassVar, Dict, List, Optional, Type

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from serverframework.lib.CustomRoute import custom_route
from serverframework.lib.Environment import env
from serverframework.lib.InboundSecurity import DEFAULT_AUTH_RATE_LIMIT, rate_limit
from serverframework.lib.Logging import logger
from serverframework.lib.Pydantic2FastAPI import AuthType, RouteType, RouterMixin
from serverframework.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    DateSearchModel,
    ModelMeta,
    StringSearchModel,
    UpdateMixinModel,
)
from serverframework.logic.BLL_Auth import (
    UserManager,
    UserModel,
    UserTeamManager,
    UserTeamModel,
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

    Manager: ClassVar[Type["UserMergeManager"]] = None
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


class UserMergeResponse(BaseModel):
    message: str
    merge_id: str
    handler_errors: str = ""


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
        from serverframework.database.StaticPermissions import is_root_id

        if not is_root_id(self.requester.id):
            raise HTTPException(
                status_code=403,
                detail="Direct CREATE on /v1/auth/user-merge is not permitted; "
                "use POST /v1/auth/user-merge/merge",
            )

    def _assert_can_merge(
        self, initiating_user_id: str, target_user_id: str
    ) -> None:
        """A user may only merge into themselves (initiating == self) or
        ROOT_ID may merge anyone. Tenant admins are not granted here
        because cross-tenant merges have system-wide consequences and
        belong to the operator role."""
        from serverframework.database.StaticPermissions import is_root_id

        if is_root_id(self.requester.id):
            return
        if (
            self.requester.id != initiating_user_id
            and self.requester.id != target_user_id
        ):
            raise HTTPException(
                status_code=403,
                detail="Caller must be one of the merge participants or ROOT",
            )
        # Even the participants cannot drive a merge that absorbs another
        # user's data without that user's consent. We require the caller
        # to be the *initiating* user (the one whose data wins) so that
        # an attacker who somehow steals the *target* account cannot
        # also steal whatever exists at the initiating account.
        if self.requester.id != initiating_user_id:
            raise HTTPException(
                status_code=403,
                detail=(
                    "Only the initiating (surviving) user can drive the merge"
                ),
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
        self, initiating_user_id: str, target_user_id: str
    ) -> Dict[str, str]:
        """Merge ``target_user_id`` into ``initiating_user_id``.

        Authorization: caller must be the initiating user, or ROOT_ID.
        Side-data transfer:
        - Team memberships are re-homed via ``UserTeamManager``.
        - Every registered merge handler (see :func:`register_merge_handler`)
          is invoked; handler exceptions are caught, logged, and recorded
          on the audit row but do not abort the merge.
        - The target user is deactivated.
        """
        import json

        self._assert_can_merge(initiating_user_id, target_user_id)
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
        )
        return UserMergeResponse(
            message=result["message"],
            merge_id=result["merge_id"],
            handler_errors=result.get("handler_errors", ""),
        )


UserMergeModel.Manager = UserMergeManager
