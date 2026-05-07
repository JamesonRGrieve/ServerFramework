"""Legacy SQLAlchemy direct-ORM stubbed out during normalization.

Original ``Notification`` / ``NotificationReference`` / ``UserNotification``
models referenced ``Base, BaseMixin, UpdateMixin, UserRefMixin, TeamRefMixin``
on the long-gone ``database`` package layout.

TODO(audit): rewrite the notification persistence layer in
``BLL_Auth_Notifications.py`` against the current ``ApplicationModel`` +
``ModelMeta`` pattern.
"""

from typing import Optional

from pydantic import BaseModel


class Notification(BaseModel):
    """Stub for the legacy ``Notification`` model. Not persisted."""

    title: str
    content: str
    reference_type: Optional[str] = None
    reference_id: Optional[str] = None
    user_id: Optional[str] = None
    team_id: Optional[str] = None


class NotificationReference(BaseModel):
    """Stub for the legacy ``NotificationReference`` model. Not persisted."""

    reference_type: str
    reference_id: str
    notification_id: str


class UserNotification(BaseModel):
    """Stub for the legacy ``UserNotification`` model. Not persisted."""

    user_id: str
    notification_id: str
    read: bool = False
    read_at: Optional[str] = None
    acknowledged: bool = False
    acknowledged_at: Optional[str] = None
