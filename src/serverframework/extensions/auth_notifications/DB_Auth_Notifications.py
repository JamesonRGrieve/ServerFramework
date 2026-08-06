from sqlalchemy import Boolean, Column, DateTime, String, Text
from sqlalchemy.orm import declared_attr, relationship

from serverframework.database.AbstractDatabaseEntity import BaseMixin, UpdateMixin
from serverframework.database.Base import Base
from serverframework.database.DB_Auth import TeamRefMixin, UserRefMixin


class Notification(
    Base, BaseMixin, UpdateMixin, UserRefMixin.Optional, TeamRefMixin.Optional
):
    __tablename__ = "notifications"
    __table_args__ = {
        "comment": "System and user notifications with various scoping options (user, team, user-team, or global)"
    }
    title = Column(Text, nullable=False, comment="Notification title/subject")
    content = Column(Text, nullable=False, comment="Notification body content")
    reference_type = Column(String, nullable=True, comment="Type of referenced entity")
    reference_id = Column(String, nullable=True, comment="ID of referenced entity")


class NotificationReference(Base, BaseMixin, UpdateMixin):
    __tablename__ = "notification_references"
    __table_args__ = {
        "comment": "Links notifications to various entities throughout the system"
    }
    reference_type = Column(String, nullable=False, comment="Type of referenced entity")
    reference_id = Column(String, nullable=False, comment="ID of referenced entity")

    @declared_attr
    def notification_id(cls):
        return cls.create_foreign_key(
            Notification,
            ondelete="CASCADE",
            comment="Reference to the notification this link belongs to",
        )

    notification = relationship("Notification", backref="notification_references")


class UserNotification(Base, BaseMixin, UserRefMixin):
    __tablename__ = "user_notifications"
    __table_args__ = {
        "comment": "Tracks which users have read or acknowledged which notifications"
    }

    @declared_attr
    def notification_id(cls):
        return cls.create_foreign_key(
            Notification,
            comment="Reference to the notification being tracked",
        )

    notification = relationship("Notification", backref="user_notifications")

    read = Column(
        Boolean, default=False, comment="Whether the user has read this notification"
    )
    read_at = Column(
        DateTime, nullable=True, comment="When the user read this notification"
    )
    acknowledged = Column(
        Boolean,
        default=False,
        comment="Whether the user has acknowledged this notification",
    )
    acknowledged_at = Column(
        DateTime, nullable=True, comment="When the user acknowledged this notification"
    )
