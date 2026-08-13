"""User notifications BLL.

Two tables:
* ``NotificationModel`` — the broadcast (title, content, optional reference
  to another entity, optional team/user scope).
* ``UserNotificationModel`` — per-user delivery state (read, acknowledged).

Pattern reference: ``auth_invitations/BLL_Invitations.py``.
"""

from datetime import datetime, timezone
from typing import ClassVar, List, Optional, Type

from fastapi import HTTPException
from pydantic import BaseModel, Field

from zephyrex.lib.CustomRoute import custom_route
from zephyrex.lib.Environment import env
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
    TeamModel,
    UserModel,
)


class NotificationModel(
    ApplicationModel,
    UpdateMixinModel,
    UserModel.Reference.Optional,
    TeamModel.Reference.Optional,
    metaclass=ModelMeta,
):
    """A notification broadcast. ``user_id``/``team_id`` scope it to an
    audience; both null = global."""

    Manager: ClassVar[Type["NotificationManager"]] = None  # type: ignore[assignment]
    title: str = Field(..., description="Notification title")
    content: str = Field(..., description="Notification body")
    reference_type: Optional[str] = Field(
        None, description="Type of referenced entity, e.g. 'invitation'"
    )
    reference_id: Optional[str] = Field(None, description="ID of referenced entity")

    table_comment: ClassVar[str] = (
        "System and user notifications with team/user scoping"
    )

    class Create(
        BaseModel,
        UserModel.Reference.ID.Optional,
        TeamModel.Reference.ID.Optional,
    ):
        title: str
        content: str
        reference_type: Optional[str] = None
        reference_id: Optional[str] = None

    class Update(BaseModel):
        title: Optional[str] = None
        content: Optional[str] = None

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        UserModel.Reference.ID.Search,
        TeamModel.Reference.ID.Search,
    ):
        title: Optional[StringSearchModel] = None
        reference_type: Optional[StringSearchModel] = None


class UserNotificationModel(
    ApplicationModel,
    UpdateMixinModel,
    UserModel.Reference,
    metaclass=ModelMeta,
):
    """Per-user delivery state for a notification."""

    Manager: ClassVar[Type["UserNotificationManager"]] = None  # type: ignore[assignment]
    notification_id: str = Field(..., description="Reference to NotificationModel")
    read: bool = Field(False, description="Whether the user has read it")
    read_at: Optional[datetime] = Field(None, description="When read")
    acknowledged: bool = Field(False, description="Whether the user dismissed it")
    acknowledged_at: Optional[datetime] = Field(None, description="When acknowledged")

    table_comment: ClassVar[str] = "Per-user notification delivery state"

    class Create(BaseModel, UserModel.Reference.ID):
        notification_id: str
        read: bool = False
        read_at: Optional[datetime] = None
        acknowledged: bool = False
        acknowledged_at: Optional[datetime] = None

    # Update is intentionally narrow: only the boolean toggles are
    # writable. Timestamps are server-stamped via the convenience routes.
    class Update(BaseModel):
        read: Optional[bool] = None
        acknowledged: Optional[bool] = None

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        UserModel.Reference.ID.Search,
    ):
        notification_id: Optional[StringSearchModel] = None
        read: Optional[bool] = None
        acknowledged: Optional[bool] = None
        read_at: Optional[DateSearchModel] = None


class MarkReadResponse(BaseModel):
    id: str
    read: bool
    read_at: Optional[datetime] = None


class AcknowledgeResponse(BaseModel):
    id: str
    acknowledged: bool
    acknowledged_at: Optional[datetime] = None


class _MarkReadRequest(BaseModel):
    """No body required; the route uses ``{id}`` from the path. The
    framework's typed-input contract requires a model regardless, so we
    declare an empty one."""

    pass


class NotificationManager(AbstractBLLManager, RouterMixin):
    _model = NotificationModel
    prefix: ClassVar[Optional[str]] = "/v1/notifications"
    tags: ClassVar[Optional[List[str]]] = ["Notifications"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    # ``user_id`` (the recipient) cannot be impersonated. ROOT/SYSTEM
    # bypass for system-issued broadcasts.
    _CALLER_OWNED_FIELDS: ClassVar[tuple] = ("user_id",)


class UserNotificationManager(AbstractBLLManager, RouterMixin):
    _model = UserNotificationModel
    prefix: ClassVar[Optional[str]] = "/v1/user-notifications"
    tags: ClassVar[Optional[List[str]]] = ["Notifications"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    _CALLER_OWNED_FIELDS: ClassVar[tuple] = ("user_id",)

    def _assert_owns(self, user_notification_id: str) -> "UserNotificationModel":
        UNDB = UserNotificationModel.DB(self.model_registry.DB.manager.Base)
        existing = UNDB.get(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            id=user_notification_id,
            return_type="dto",
            override_dto=UserNotificationModel,
        )
        if existing is None:
            raise HTTPException(status_code=404, detail="Notification not found")
        from zephyrex.database.StaticPermissions import is_root_id

        if existing.user_id != self.requester.id and not is_root_id(self.requester.id):
            raise HTTPException(
                status_code=403,
                detail="Cannot modify another user's notification state",
            )
        return existing  # type: ignore[no-any-return]

    def mark_as_read(self, notification_id: str) -> UserNotificationModel:
        existing = self._assert_owns(notification_id)
        UNDB = UserNotificationModel.DB(self.model_registry.DB.manager.Base)
        now = datetime.now(timezone.utc)
        return UNDB.update(  # type: ignore[no-any-return]
            requester_id=self.requester.id,
            model_registry=self.model_registry,
            id=existing.id,
            new_properties={"read": True, "read_at": now},
            return_type="dto",
            override_dto=UserNotificationModel,
        )

    def acknowledge(self, notification_id: str) -> UserNotificationModel:
        existing = self._assert_owns(notification_id)
        UNDB = UserNotificationModel.DB(self.model_registry.DB.manager.Base)
        now = datetime.now(timezone.utc)
        return UNDB.update(  # type: ignore[no-any-return]
            requester_id=self.requester.id,
            model_registry=self.model_registry,
            id=existing.id,
            new_properties={"acknowledged": True, "acknowledged_at": now},
            return_type="dto",
            override_dto=UserNotificationModel,
        )

    @custom_route(
        method="PATCH",
        path="/{id}/read",
        input_model=_MarkReadRequest,
        output_model=MarkReadResponse,
        authentication_type="jwt",
        openapi_tags=("Notifications",),
        summary="Mark a user-notification as read (server stamps the time)",
    )
    async def mark_read_route(
        self, id: str, body: _MarkReadRequest
    ) -> MarkReadResponse:
        del body
        result = self.mark_as_read(id)
        return MarkReadResponse(id=result.id, read=True, read_at=result.read_at)

    @custom_route(
        method="PATCH",
        path="/{id}/acknowledge",
        input_model=_MarkReadRequest,
        output_model=AcknowledgeResponse,
        authentication_type="jwt",
        openapi_tags=("Notifications",),
        summary="Acknowledge a user-notification (server stamps the time)",
    )
    async def acknowledge_route(
        self, id: str, body: _MarkReadRequest
    ) -> AcknowledgeResponse:
        del body
        result = self.acknowledge(id)
        return AcknowledgeResponse(
            id=result.id,
            acknowledged=True,
            acknowledged_at=result.acknowledged_at,
        )


NotificationModel.Manager = NotificationManager
UserNotificationModel.Manager = UserNotificationManager


# ---------------------------------------------------------------------------
# Merge participation
# ---------------------------------------------------------------------------


def _merge_handler(ctx) -> None:
    """Re-home target's per-user delivery rows onto the initiating user.

    Notification broadcasts (``NotificationModel``) are not migrated:
    they're scoped at issuance time and should not retroactively apply to
    a different user. Only the per-user receipts move so the surviving
    account doesn't lose its history.
    """
    from zephyrex.lib.Environment import env as _env

    UNDB = UserNotificationModel.DB(ctx.model_registry.DB.manager.Base)
    target_rows = (
        UNDB.list(
            requester_id=_env("ROOT_ID"),
            model_registry=ctx.model_registry,
            filters=[UNDB.user_id == ctx.target_user_id],
            return_type="dto",
            override_dto=UserNotificationModel,
        )
        or []
    )
    for row in target_rows:
        UNDB.update(
            requester_id=_env("ROOT_ID"),
            model_registry=ctx.model_registry,
            id=row.id,
            new_properties={"user_id": ctx.initiating_user_id},
        )


def register_merge_participation() -> None:
    """Register the auth_notifications merge handler with auth_merge.

    Called from ``EXT_Auth_Notifications.on_initialize`` once auth_merge
    is loaded. Safe to call repeatedly: the registry overwrites by name.
    """
    try:
        from zephyrex.extensions.auth_merge.BLL_Auth_Merge import make_merge_registrar
    except ImportError:
        return
    make_merge_registrar("auth_notifications", _merge_handler)()
