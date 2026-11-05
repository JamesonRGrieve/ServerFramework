from sqlalchemy.orm import declared_attr, relationship

from zephyrex.database.AbstractDatabaseEntity import BaseMixin, UpdateMixin
from zephyrex.database.Base import Base
from zephyrex.database.DB_Auth import User


class UserMerge(Base, BaseMixin, UpdateMixin):
    __tablename__ = "user_merges"
    __table_args__ = {
        "comment": "Tracks user account merges when a user has multiple accounts that need to be consolidated"
    }

    @declared_attr
    def initiating_user_id(cls):
        return cls.create_foreign_key(
            User,
            comment="User who initiated the merge (account to keep)",
            constraint_name="fk_user_merge_initiating",
        )

    @declared_attr
    def target_user_id(cls):
        return cls.create_foreign_key(
            User,
            comment="User to be merged into the initiating user (account to disable)",
            constraint_name="fk_user_merge_target",
        )


UserMerge.initiating_user = relationship(
    User.__name__,
    foreign_keys=[UserMerge.initiating_user_id],
    backref="merges_initiated",
)

UserMerge.target_user = relationship(
    User.__name__,
    foreign_keys=[UserMerge.target_user_id],
    backref="merges_targetted",
)
# TODO Add TeamMerge.
