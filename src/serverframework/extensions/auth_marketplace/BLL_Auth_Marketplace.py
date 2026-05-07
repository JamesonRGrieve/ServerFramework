from typing import Optional

from fastapi import HTTPException
from models.ModelBase import BaseMixinModel, UpdateMixinModel
from models.ModelSearch import StringSearchModel
from models.ModelTeam import TeamModel  # Added TeamModel
from models.ModelUser import UserModel  # Added UserModel
from pydantic import BaseModel, Field

from database.DB_Auth import Team, User  # Added Team
from database.DB_Ext import Provider  # Added Provider
from database.DB_Marketplace import (  # Added DB Models
    TeamPaymentPortal,
    UserPaymentPortal,
)
from logic.BLL_Base import AbstractBLLManager


# --- User Payment Portal --- #
class UserPaymentPortalModel(BaseMixinModel, UpdateMixinModel, UserModel.ReferenceID):
    # Inherits user_id
    payment_portal_id: str = Field(
        ..., description="ID of the payment portal provider"
    )  # Clarified description
    customer_id: Optional[str] = Field(
        None, description="Customer ID in the payment portal"
    )

    class ReferenceID:
        user_payment_portal_id: str = Field(
            ..., description="The ID of the related user payment portal"
        )

        class Optional:
            user_payment_portal_id: Optional[str] = None  # Added field

        class Search:
            user_payment_portal_id: Optional[StringSearchModel] = None

    class Create(BaseModel, UserModel.ReferenceID):  # Simplified Create model
        payment_portal_id: str = Field(
            ..., description="ID of the payment portal provider"
        )
        customer_id: Optional[str] = Field(
            None, description="Customer ID in the payment portal"
        )

    class Update(BaseModel):
        # UserID and PortalID should likely not be updatable
        customer_id: Optional[str] = Field(
            None, description="Customer ID in the payment portal"
        )

    class Search(
        BaseMixinModel.Search, UserModel.ReferenceID.Search
    ):  # Added Base Search Mixin
        payment_portal_id: Optional[StringSearchModel] = None
        customer_id: Optional[StringSearchModel] = None


class UserPaymentPortalReferenceModel(UserPaymentPortalModel.ReferenceID):
    user_payment_portal: Optional[UserPaymentPortalModel] = None

    class Optional(UserPaymentPortalModel.ReferenceID.Optional):
        # Corrected indentation
        user_payment_portal: Optional[UserPaymentPortalModel] = None


class UserPaymentPortalNetworkModel:
    class POST(BaseModel):
        user_payment_portal: UserPaymentPortalModel.Create  # Use Create model

    class PUT(BaseModel):  # Added PUT model
        user_payment_portal: UserPaymentPortalModel.Update

    class SEARCH(BaseModel):
        user_payment_portal: UserPaymentPortalModel.Search  # Use Search model

    class ResponseSingle(BaseModel):
        user_payment_portal: UserPaymentPortalModel

    class ResponsePlural(BaseModel):  # Added Plural response
        user_payment_portals: list[UserPaymentPortalModel]


class UserPaymentPortalManager(AbstractBLLManager):
    Model = UserPaymentPortalModel
    ReferenceModel = UserPaymentPortalReferenceModel
    NetworkModel = UserPaymentPortalNetworkModel  # Added Network Model
    DBClass = UserPaymentPortal

    # Add __init__ if needed for related managers

    def createValidation(
        self, entity: UserPaymentPortalModel.Create
    ):  # Added type hint
        # Validate user existence
        if not User.exists(
            requester_id=self.requester.id,
            db=self.db,
            id=entity.user_id,  # Closed parenthesis
        ):
            raise HTTPException(status_code=404, detail="User not found")
        # Validate payment portal provider existence
        if not Provider.exists(
            requester_id=self.requester.id,
            db=self.db,
            id=entity.payment_portal_id,  # Added condition
        ):
            raise HTTPException(
                status_code=404, detail="Payment portal provider not found"
            )


# --- Team Payment Portal --- #
class TeamPaymentPortalModel(  # Started new class definition correctly
    BaseMixinModel,
    UpdateMixinModel,
    TeamModel.ReferenceID,
    UserModel.ReferenceID.Optional,  # Added base classes, user is optional creator
):
    # Inherits team_id, optionally user_id (creator/owner)
    payment_portal_id: str = Field(
        ..., description="ID of the payment portal provider"
    )  # Clarified description
    customer_id: Optional[str] = Field(
        None, description="Customer ID in the payment portal"
    )

    class ReferenceID:
        team_payment_portal_id: str = Field(
            ..., description="The ID of the related team payment portal"
        )

        class Optional:
            team_payment_portal_id: Optional[str] = None

        class Search:
            team_payment_portal_id: Optional[StringSearchModel] = None  # Added field

    class Create(
        BaseModel, TeamModel.ReferenceID, UserModel.ReferenceID.Optional
    ):  # User is optional creator
        payment_portal_id: str = Field(
            ..., description="ID of the payment portal provider"
        )
        customer_id: Optional[str] = Field(
            None, description="Customer ID in the payment portal"
        )

    class Update(BaseModel):
        # TeamID and PortalID should likely not be updatable
        customer_id: Optional[str] = Field(
            None, description="Customer ID in the payment portal"
        )

    class Search(
        BaseMixinModel.Search,
        TeamModel.ReferenceID.Search,
        UserModel.ReferenceID.Search,  # Allow searching by creator user
    ):  # Added closing parenthesis
        payment_portal_id: Optional[StringSearchModel] = None
        customer_id: Optional[StringSearchModel] = None


class TeamPaymentPortalReferenceModel(TeamPaymentPortalModel.ReferenceID):
    team_payment_portal: Optional[TeamPaymentPortalModel] = None

    class Optional(TeamPaymentPortalModel.ReferenceID.Optional):
        # Corrected indentation
        team_payment_portal: Optional[TeamPaymentPortalModel] = None


class TeamPaymentPortalNetworkModel:
    class POST(BaseModel):
        team_payment_portal: TeamPaymentPortalModel.Create

    class PUT(BaseModel):
        team_payment_portal: TeamPaymentPortalModel.Update

    class SEARCH(BaseModel):
        team_payment_portal: TeamPaymentPortalModel.Search

    class ResponseSingle(BaseModel):
        team_payment_portal: TeamPaymentPortalModel

    class ResponsePlural(BaseModel):
        team_payment_portals: list[TeamPaymentPortalModel]  # Corrected field name


class TeamPaymentPortalManager(AbstractBLLManager):
    Model = TeamPaymentPortalModel
    ReferenceModel = TeamPaymentPortalReferenceModel
    NetworkModel = TeamPaymentPortalNetworkModel
    DBClass = TeamPaymentPortal  # Added DB Class

    # Add __init__ if needed for related managers

    def createValidation(
        self, entity: TeamPaymentPortalModel.Create
    ):  # Added type hint
        """Validate team payment portal creation"""
        # Validate team existence
        if not Team.exists(
            requester_id=self.requester.id, db=self.db, id=entity.team_id
        ):
            raise HTTPException(status_code=404, detail="Team not found")
        # Validate payment portal provider existence
        if not Provider.exists(
            requester_id=self.requester.id,
            db=self.db,
            id=entity.payment_portal_id,  # Added condition
        ):
            raise HTTPException(
                status_code=404, detail="Payment portal provider not found"
            )
        # Validate creator user existence (if provided)
        if entity.user_id and not User.exists(
            requester_id=self.requester.id,
            db=self.db,
            id=entity.user_id,  # Added condition
        ):
            raise HTTPException(status_code=404, detail="Creator user not found")
