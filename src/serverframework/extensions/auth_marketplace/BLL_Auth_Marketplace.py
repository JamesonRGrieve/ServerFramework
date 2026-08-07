"""Marketplace BLL: links users and teams to payment providers.

Owns ``UserPaymentPortalModel`` and ``TeamPaymentPortalModel``, the
side-tables that record which payment provider holds each tenant's
payment portal (Stripe customer ID, etc.). The actual payment processing
lives in the ``payment`` extension; this extension only owns the linkage
state.

Pattern reference: ``auth_invitations/BLL_Invitations.py``.
"""

from typing import ClassVar, List, Optional, Type

from pydantic import BaseModel, Field

from serverframework.lib.Pydantic2FastAPI import AuthType, RouterMixin
from serverframework.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    ModelMeta,
    StringSearchModel,
    UpdateMixinModel,
)
from serverframework.logic.BLL_Auth import (
    TeamModel,
    UserModel,
)


class UserPaymentPortalModel(
    ApplicationModel,
    UpdateMixinModel,
    UserModel.Reference,
    metaclass=ModelMeta,
):
    """Per-user link to a payment provider (e.g. Stripe customer)."""

    Manager: ClassVar[Type["UserPaymentPortalManager"]] = None  # type: ignore[assignment]
    payment_portal_id: str = Field(
        ..., description="Reference to a registered payment provider"
    )
    customer_id: Optional[str] = Field(
        None, description="Customer ID at the payment provider, if assigned"
    )

    table_comment: ClassVar[str] = (
        "Per-user link to a payment provider; one row per (user, provider) pair"
    )

    class Create(BaseModel, UserModel.Reference.ID):
        payment_portal_id: str
        customer_id: Optional[str] = None

    class Update(BaseModel):
        customer_id: Optional[str] = None

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        UserModel.Reference.ID.Search,
    ):
        payment_portal_id: Optional[StringSearchModel] = None
        customer_id: Optional[StringSearchModel] = None


class TeamPaymentPortalModel(
    ApplicationModel,
    UpdateMixinModel,
    TeamModel.Reference,
    UserModel.Reference.Optional,
    metaclass=ModelMeta,
):
    """Per-team link to a payment provider (creator user_id is optional)."""

    Manager: ClassVar[Type["TeamPaymentPortalManager"]] = None  # type: ignore[assignment]
    payment_portal_id: str = Field(...)
    customer_id: Optional[str] = Field(None)

    table_comment: ClassVar[str] = (
        "Per-team link to a payment provider; one row per (team, provider) pair"
    )

    class Create(
        BaseModel,
        TeamModel.Reference.ID,
        UserModel.Reference.ID.Optional,
    ):
        payment_portal_id: str
        customer_id: Optional[str] = None

    class Update(BaseModel):
        customer_id: Optional[str] = None

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        TeamModel.Reference.ID.Search,
        UserModel.Reference.ID.Search,
    ):
        payment_portal_id: Optional[StringSearchModel] = None
        customer_id: Optional[StringSearchModel] = None


class UserPaymentPortalManager(AbstractBLLManager, RouterMixin):
    _model = UserPaymentPortalModel
    prefix: ClassVar[Optional[str]] = "/v1/marketplace/user-portals"
    tags: ClassVar[Optional[List[str]]] = ["Marketplace"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    # Refuse cross-user payment-portal claims at the BLL layer; a user
    # can only attach a payment portal to their own account.
    _CALLER_OWNED_FIELDS: ClassVar[tuple] = ("user_id",)


class TeamPaymentPortalManager(AbstractBLLManager, RouterMixin):
    _model = TeamPaymentPortalModel
    prefix: ClassVar[Optional[str]] = "/v1/marketplace/team-portals"
    tags: ClassVar[Optional[List[str]]] = ["Marketplace"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    # ``user_id`` is the creating user; team_id is the resource owner and
    # must be checked against membership at the route layer (TODO).
    _CALLER_OWNED_FIELDS: ClassVar[tuple] = ("user_id",)


UserPaymentPortalModel.Manager = UserPaymentPortalManager
TeamPaymentPortalModel.Manager = TeamPaymentPortalManager
