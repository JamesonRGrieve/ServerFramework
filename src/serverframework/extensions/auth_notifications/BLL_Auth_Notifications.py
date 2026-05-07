"""User notifications BLL.

Two tables:
* ``NotificationModel`` — the broadcast (title, content, optional reference
  to another entity, optional team/user scope).
* ``UserNotificationModel`` — per-user delivery state (read, acknowledged).

Pattern reference: ``auth_invitations/BLL_Invitations.py``.
"""

from datetime import datetime
from typing import ClassVar, List, Optional, Type

from pydantic import BaseModel, Field

from serverframework.lib.Pydantic2FastAPI import AuthType, RouterMixin
from serverframework.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    DateSearchModel,
    ModelMeta,
    StringSearchModel,
    UpdateMixinModel,
)
from serverframework.logic.BLL_Auth import (
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

    Manager: ClassVar[Type["NotificationManager"]] = None
    title: str = Field(..., description="Notification title")
    content: str = Field(..., description="Notification body")
    reference_type: Optional[str] = Field(
        None, description="Type of referenced entity, e.g. 'invitation'"
    )
    reference_id: Optional[str] = Field(
        None, description="ID of referenced entity"
    )

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

    Manager: ClassVar[Type["UserNotificationManager"]] = None
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

    class Update(BaseModel):
        read: Optional[bool] = None
        read_at: Optional[datetime] = None
        acknowledged: Optional[bool] = None
        acknowledged_at: Optional[datetime] = None

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        UserModel.Reference.ID.Search,
    ):
        notification_id: Optional[StringSearchModel] = None
        read: Optional[bool] = None
        acknowledged: Optional[bool] = None
        read_at: Optional[DateSearchModel] = None


class NotificationManager(AbstractBLLManager, RouterMixin):
    _model = NotificationModel
    prefix: ClassVar[Optional[str]] = "/v1/notifications"
    tags: ClassVar[Optional[List[str]]] = ["Notifications"]
    auth_type: ClassVar[AuthType] = AuthType.JWT


class UserNotificationManager(AbstractBLLManager, RouterMixin):
    _model = UserNotificationModel
    prefix: ClassVar[Optional[str]] = "/v1/user-notifications"
    tags: ClassVar[Optional[List[str]]] = ["Notifications"]
    auth_type: ClassVar[AuthType] = AuthType.JWT


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
    from serverframework.lib.Environment import env as _env

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
        from serverframework.extensions.auth_merge.BLL_Auth_Merge import (
            register_merge_handler,
        )
    except ImportError:
        # auth_merge is not loaded; nothing to register against.
        return
    register_merge_handler("auth_notifications", _merge_handler)
