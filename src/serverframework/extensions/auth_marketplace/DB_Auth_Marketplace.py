from sqlalchemy import Column, String

from serverframework.database.AbstractDatabaseEntity import BaseMixin, UpdateMixin
from serverframework.database.Base import Base
from serverframework.database.DB_Auth import TeamRefMixin, UserRefMixin
from serverframework.database.DB_Providers import ProviderRefMixin


class UserPaymentPortal(Base, BaseMixin, UpdateMixin, UserRefMixin, ProviderRefMixin):
    __tablename__ = "user_payment_portals"
    __table_args__ = {
        "comment": "Links users to payment providers for processing payments and subscriptions"
    }
    # payment_portal_id = create_foreign_key(
    #     Provider, comment="Reference to the payment portal provider"
    # )
    customer_id = Column(
        String,
        nullable=True,
        unique=True,
        comment="Unique customer ID provided by the payment processor",
    )


class TeamPaymentPortal(
    Base, BaseMixin, UpdateMixin, TeamRefMixin, ProviderRefMixin, UserRefMixin.Optional
):
    __tablename__ = "team_payment_portals"
    __table_args__ = {
        "comment": "Links teams to payment providers for processing team-level payments and subscriptions"
    }
    # Inherits team_id, payment_portal_id, and optional user_id (creator)
    customer_id = Column(
        String,
        nullable=True,
        unique=True,
        comment="Unique customer ID provided by the payment processor",
    )
