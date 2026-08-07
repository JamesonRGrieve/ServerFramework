from sqlalchemy import Boolean, CheckConstraint, Column, String, UniqueConstraint

from serverframework.database.AbstractDatabaseEntity import BaseMixin, UpdateMixin
from serverframework.database.Base import Base
from serverframework.database.DB_Auth import TeamRefMixin, UserRefMixin


class DataRegion(Base, BaseMixin, UpdateMixin):
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    is_available = Column(Boolean, default=True)
    compliance_level = Column(String, nullable=True)  # 'gdpr', 'hipaa', etc.


class DataResidencyPreference(
    Base, BaseMixin, UpdateMixin, UserRefMixin.Optional, TeamRefMixin.Optional
):
    # What data types this preference applies to
    data_type = Column(String, nullable=False)  # 'user_data', 'files', 'analytics'
    __table_args__ = (
        CheckConstraint(
            "(user_id IS NULL) != (team_id IS NULL)", name="chk_user_xor_team"
        ),
        UniqueConstraint("user_id", "data_type", name="uq_user_data_type"),
        UniqueConstraint("team_id", "data_type", name="uq_team_data_type"),
    )
