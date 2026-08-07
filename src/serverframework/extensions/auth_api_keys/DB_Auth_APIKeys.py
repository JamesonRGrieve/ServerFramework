from sqlalchemy import Column, String

from serverframework.database.AbstractDatabaseEntity import (
    BaseMixin,
    UpdateMixin,
    create_reference_mixin,
)
from serverframework.database.Base import Base
from serverframework.database.DB_Auth import RoleRefMixin, TeamRefMixin, UserRefMixin


class APIKey(
    Base,
    BaseMixin,
    UpdateMixin,
    UserRefMixin.Optional,
    TeamRefMixin.Optional,
    RoleRefMixin.Optional,
):
    __tablename__ = "api_keys"
    __table_args__ = {"comment": "API keys for programmatic access to the application"}
    name = Column(String, nullable=False, comment="Human-readable name for the API key")
    key_hash = Column(
        String,
        nullable=False,
        unique=True,
        comment="Hash of the API key for verification",
    )


# Create APIKey reference mixin using the standard pattern
APIKeyRefMixin = create_reference_mixin(APIKey)
