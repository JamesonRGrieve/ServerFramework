"""Legacy SQLAlchemy direct-ORM stubbed out during normalization.

Original ``UserPaymentPortal`` / ``TeamPaymentPortal`` models linked users
or teams to payment providers via ``Base, BaseMixin, UpdateMixin,
UserRefMixin, ProviderRefMixin, TeamRefMixin``. None of those classes exist
in the current tree, and ``serverframework.database.DB_Providers`` does not
exist either (provider registration moved to ``serverframework.logic.BLL_Providers``).

TODO(audit): rewrite payment-portal persistence in ``BLL_Auth_Marketplace.py``
using ``ApplicationModel`` + ``ModelMeta``, referencing ``ProviderInstanceModel``
from ``serverframework.logic.BLL_Providers`` instead of a separate
``DB_Providers`` module.
"""

from typing import Optional

from pydantic import BaseModel


class UserPaymentPortal(BaseModel):
    """Stub for the legacy ``UserPaymentPortal`` model. Not persisted."""

    user_id: str
    payment_portal_id: str
    customer_id: Optional[str] = None


class TeamPaymentPortal(BaseModel):
    """Stub for the legacy ``TeamPaymentPortal`` model. Not persisted."""

    team_id: str
    payment_portal_id: str
    user_id: Optional[str] = None
    customer_id: Optional[str] = None
