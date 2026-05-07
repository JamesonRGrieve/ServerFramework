from datetime import datetime
from typing import List, Optional

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from logic.AbstractLogicManager import (
    AbstractBLLManager,
    BaseMixinModel,
    StringSearchModel,
    UpdateMixinModel,
)
from logic.BLL_Auth import (
    Notification,
    NotificationReference,
    TeamManager,
    TeamModel,
    TeamReferenceModel,
    User,
    UserManager,
    UserModel,
    UserNotification,
    UserReferenceModel,
    UserTeam,
)


class NotificationModel(
    BaseMixinModel, UpdateMixinModel, UserReferenceModel, TeamReferenceModel
):
    title: str = Field(..., description="Notification title")
    content: str = Field(..., description="Notification content")
    reference_type: Optional[str] = Field(None, description="Type of referenced object")
    reference_id: Optional[str] = Field(None, description="ID of referenced object")

    class ReferenceID:
        notification_id: str = Field(
            ..., description="The ID of the related notification"
        )

        class Optional:
            notification_id: Optional[str] = None

        class Search:
            notification_id: Optional[StringSearchModel] = None

    class Create(
        BaseModel, UserModel.ReferenceID.Optional, TeamModel.ReferenceID.Optional
    ):
        title: str = Field(..., description="Notification title")
        content: str = Field(..., description="Notification content")
        reference_type: Optional[str] = Field(
            None, description="Type of referenced object"
        )
        reference_id: Optional[str] = Field(None, description="ID of referenced object")

    class Update(BaseModel):
        title: Optional[str] = Field(None, description="Notification title")
        content: Optional[str] = Field(None, description="Notification content")
        reference_type: Optional[str] = Field(
            None, description="Type of referenced object"
        )
        reference_id: Optional[str] = Field(None, description="ID of referenced object")

    class Search(
        BaseMixinModel.Search,
        UserModel.ReferenceID.Search,
        TeamModel.ReferenceID.Search,
    ):
        title: Optional[StringSearchModel] = None
        content: Optional[StringSearchModel] = None
        reference_type: Optional[StringSearchModel] = None
        reference_id: Optional[StringSearchModel] = None


class NotificationReferenceModel(NotificationModel.ReferenceID):
    notification: Optional[NotificationModel] = None

    class Optional(NotificationModel.ReferenceID.Optional):
        notification: Optional[NotificationModel] = None


class NotificationNetworkModel:
    class POST(BaseModel):
        notification: NotificationModel.Create

    class SEARCH(BaseModel):
        notification: NotificationModel.Search

    class ResponseSingle(BaseModel):
        notification: NotificationModel

    class ResponsePlural(BaseModel):
        notifications: List[NotificationModel]


class NotificationManager(AbstractBLLManager):
    Model = NotificationModel
    ReferenceModel = NotificationReferenceModel
    DBClass = Notification
    NetworkModel = NotificationNetworkModel

    def __init__(
        self,
        requester_id: str,
        target_user_id: Optional[str] = None,
        target_team_id: Optional[str] = None,
        db: Optional[Session] = None,
    ):
        super().__init__(
            requester_id=requester_id,
            target_user_id=target_user_id,
            target_team_id=target_team_id,
            db=db,
        )
        self._users = None
        self._teams = None
        self._notification_references = None
        self._user_notifications = None

    @property
    def users(self):
        """Get the user manager"""
        if self._users is None:
            self._users = UserManager(
                requester_id=self.requester.id,
                target_user_id=self.target_user_id,
                target_team_id=self.target_team_id,
                db=self.db,
            )
        return self._users

    @property
    def teams(self):
        """Get the team manager"""
        if self._teams is None:
            self._teams = TeamManager(
                requester_id=self.requester.id,
                target_user_id=self.target_user_id,
                target_team_id=self.target_team_id,
                db=self.db,
            )
        return self._teams

    @property
    def notification_references(self):
        """Get the notification reference manager"""
        if self._notification_references is None:
            self._notification_references = NotificationReferenceManager(
                requester_id=self.requester.id,
                db=self.db,
            )
        return self._notification_references

    @property
    def user_notifications(self):
        """Get the user notification manager"""
        if self._user_notifications is None:
            self._user_notifications = UserNotificationManager(
                requester_id=self.requester.id,
                target_user_id=self.target_user_id,
                db=self.db,
            )
        return self._user_notifications

    def create(self, **kwargs):
        notification = super().create(**kwargs)
        # Create user notifications automatically
        if notification.team_id:
            # Create for all users in the team
            team_users = UserTeam.list(
                requester_id=self.requester.id,
                db=self.db,
                team_id=notification.team_id,
                enabled=True,
            )
            for team_user in team_users:
                self.user_notifications.create(
                    user_id=team_user.user_id, notification_id=notification.id
                )
        elif notification.user_id:
            # Create for specific user
            self.user_notifications.create(
                user_id=notification.user_id, notification_id=notification.id
            )
        else:
            # Create for all active users (global notification)
            users = User.list(requester_id=self.requester.id, db=self.db, active=True)
            for user in users:
                self.user_notifications.create(
                    user_id=user.id, notification_id=notification.id
                )
        return notification


class NotificationReferenceDataModel(
    BaseMixinModel, UpdateMixinModel, NotificationReferenceModel
):
    reference_type: str = Field(..., description="Type of reference")
    reference_id: str = Field(..., description="ID of reference")

    class ReferenceID:
        notification_reference_id: str = Field(
            ..., description="ID of the notification reference"
        )

        class Optional:
            notification_reference_id: Optional[str] = None

        class Search:
            notification_reference_id: Optional[StringSearchModel] = None

    class Create(BaseModel, NotificationModel.ReferenceID):
        reference_type: str = Field(..., description="Type of reference")
        reference_id: str = Field(..., description="ID of reference")

    class Update(BaseModel):
        reference_type: Optional[str] = Field(None, description="Type of reference")
        reference_id: Optional[str] = Field(None, description="ID of reference")

    class Search(BaseMixinModel.Search, NotificationModel.ReferenceID.Search):
        reference_type: Optional[StringSearchModel] = None
        reference_id: Optional[StringSearchModel] = None


class NotificationReferenceDataReferenceModel(
    NotificationReferenceDataModel.ReferenceID
):
    notification_reference: Optional[NotificationReferenceDataModel] = None

    class Optional(NotificationReferenceDataModel.ReferenceID.Optional):
        notification_reference: Optional[NotificationReferenceDataModel] = None


class NotificationReferenceNetworkModel:
    class POST(BaseModel):
        notification_reference: NotificationReferenceDataModel.Create

    class PUT(BaseModel):
        notification_reference: NotificationReferenceDataModel.Update

    class SEARCH(BaseModel):
        notification_reference: NotificationReferenceDataModel.Search

    class ResponseSingle(BaseModel):
        notification_reference: NotificationReferenceDataModel

    class ResponsePlural(BaseModel):
        notification_references: List[NotificationReferenceDataModel]


class NotificationReferenceManager(AbstractBLLManager):
    Model = NotificationReferenceDataModel
    ReferenceModel = NotificationReferenceDataReferenceModel
    DBClass = NotificationReference
    NetworkModel = NotificationReferenceNetworkModel

    def createValidation(self, entity):
        if not Notification.exists(
            requester_id=self.requester.id, db=self.db, id=entity.notification_id
        ):
            raise HTTPException(status_code=404, detail="Notification not found")


class UserNotificationModel(
    BaseMixinModel, UpdateMixinModel, UserReferenceModel, NotificationReferenceModel
):
    read_at: Optional[datetime] = Field(
        None, description="Timestamp when notification was read"
    )
    acknowledged: Optional[bool] = Field(
        False, description="Whether the notification has been acknowledged"
    )
    acknowledged_at: Optional[datetime] = Field(
        None, description="Timestamp when notification was acknowledged"
    )

    class ReferenceID:
        user_notification_id: str = Field(
            ..., description="The ID of the user notification"
        )

        class Optional:
            user_notification_id: Optional[str] = None

        class Search:
            user_notification_id: Optional[StringSearchModel] = None

    class Create(BaseModel, UserModel.ReferenceID, NotificationModel.ReferenceID):
        read: Optional[bool] = Field(
            False, description="Whether the notification has been read"
        )
        read_at: Optional[datetime] = Field(
            None, description="Timestamp when notification was read"
        )
        acknowledged: Optional[bool] = Field(
            False, description="Whether the notification has been acknowledged"
        )
        acknowledged_at: Optional[datetime] = Field(
            None, description="Timestamp when notification was acknowledged"
        )

    class Update(BaseModel):
        read: Optional[bool] = Field(
            None, description="Whether the notification has been read"
        )
        read_at: Optional[datetime] = Field(
            None, description="Timestamp when notification was read"
        )
        acknowledged: Optional[bool] = Field(
            None, description="Whether the notification has been acknowledged"
        )
        acknowledged_at: Optional[datetime] = Field(
            None, description="Timestamp when notification was acknowledged"
        )

    class Search(
        BaseMixinModel.Search,
        UserModel.ReferenceID.Search,
        NotificationModel.ReferenceID.Search,
    ):
        read: Optional[bool] = None
        acknowledged: Optional[bool] = None


class UserNotificationReferenceModel(UserNotificationModel.ReferenceID):
    user_notification: Optional[UserNotificationModel] = None

    class Optional(UserNotificationModel.ReferenceID.Optional):
        user_notification: Optional[UserNotificationModel] = None


class UserNotificationNetworkModel:
    class POST(BaseModel):
        user_notification: UserNotificationModel.Create

    class PUT(BaseModel):
        user_notification: UserNotificationModel.Update

    class SEARCH(BaseModel):
        user_notification: UserNotificationModel.Search

    class ResponseSingle(BaseModel):
        user_notification: UserNotificationModel

    class ResponsePlural(BaseModel):
        user_notifications: List[UserNotificationModel]


class UserNotificationManager(AbstractBLLManager):
    Model = UserNotificationModel
    ReferenceModel = UserNotificationReferenceModel
    NetworkModel = UserNotificationNetworkModel
    DBClass = UserNotification

    def __init__(
        self,
        requester_id: str,
        target_user_id: Optional[str] = None,
        target_team_id: Optional[str] = None,
        db: Optional[Session] = None,
    ):
        super().__init__(
            requester_id=requester_id,
            target_user_id=target_user_id,
            target_team_id=target_team_id,
            db=db,
        )
        self._notifications = None
        self._users = None

    @property
    def users(self):
        """Get the user manager"""
        if self._users is None:
            self._users = UserManager(
                requester_id=self.requester.id,
                target_user_id=self.target_user_id,
                target_team_id=self.target_team_id,
                db=self.db,
            )
        return self._users

    @property
    def notifications(self):
        """Get the notification manager"""
        if self._notifications is None:
            self._notifications = NotificationManager(
                requester_id=self.requester.id,
                target_user_id=self.target_user_id,
                target_team_id=self.target_team_id,
                db=self.db,
            )
        return self._notifications

    def createValidation(self, entity):
        """Validate user notification creation"""
        if not User.exists(
            requester_id=self.requester.id, db=self.db, id=entity.user_id
        ):
            raise HTTPException(status_code=404, detail="User not found")
        if not Notification.exists(
            requester_id=self.requester.id, db=self.db, id=entity.notification_id
        ):
            raise HTTPException(status_code=404, detail="Notification not found")
