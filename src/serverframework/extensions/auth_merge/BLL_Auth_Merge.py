from typing import Dict, Optional

from fastapi import HTTPException
from pydantic import BaseModel, Field

from extensions.auth_merge.DB_Auth_Merge import UserMerge
from logic.AbstractLogicManager import (
    AbstractBLLManager,
    BaseMixinModel,
    StringSearchModel,
    UpdateMixinModel,
)
from logic.BLL_Auth import (
    User,
    UserMetadata,
    UserMetadataManager,
    UserTeam,
    UserTeamManager,
)


class UserMergeModel(BaseMixinModel, UpdateMixinModel):
    initiating_user_id: str = Field(..., description="ID of the initiating user")
    target_user_id: str = Field(..., description="ID of the target user")

    class ReferenceID:
        user_merge_id: str = Field(..., description="The ID of the related user merge")

        class Optional:
            user_merge_id: Optional[str] = None

        class Search:
            user_merge_id: Optional[StringSearchModel] = None

    class Create(BaseModel):
        initiating_user_id: str = Field(..., description="ID of the initiating user")
        target_user_id: str = Field(..., description="ID of the target user")

    class Update(BaseModel):
        # No updatable fields for UserMerge
        pass

    class Search(BaseMixinModel.Search):
        initiating_user_id: Optional[StringSearchModel] = None
        target_user_id: Optional[StringSearchModel] = None


class UserMergeReferenceModel(UserMergeModel.ReferenceID):
    user_merge: Optional[UserMergeModel] = None

    class Optional(UserMergeModel.ReferenceID.Optional):
        user_merge: Optional[UserMergeModel] = None


class UserMergeNetworkModel:
    class POST(BaseModel):
        user_merge: UserMergeModel.Create

    class SEARCH(BaseModel):
        user_merge: UserMergeModel.Search

    class ResponseSingle(BaseModel):
        user_merge: UserMergeModel


class UserMergeManager(AbstractBLLManager):
    Model = UserMergeModel
    ReferenceModel = UserMergeReferenceModel
    DBClass = UserMerge

    def createValidation(self, entity):
        if not User.exists(
            requester_id=self.requester.id, db=self.db, id=entity.initiating_user_id
        ):
            raise HTTPException(status_code=404, detail="Initiating user not found")
        if not User.exists(
            requester_id=self.requester.id, db=self.db, id=entity.target_user_id
        ):
            raise HTTPException(status_code=404, detail="Target user not found")
        if entity.initiating_user_id == entity.target_user_id:
            raise HTTPException(
                status_code=400, detail="Cannot merge a user with themselves"
            )

    def merge_users(
        self, initiating_user_id: str, target_user_id: str
    ) -> Dict[str, str]:
        """Merge two user accounts, transferring data from target to initiating user."""
        # Validate first
        self.createValidation(
            UserMergeModel(
                initiating_user_id=initiating_user_id, target_user_id=target_user_id
            )
        )

        # Create merge record
        merge = self.create(
            user_merge=UserMergeModel.Create(
                initiating_user_id=initiating_user_id, target_user_id=target_user_id
            )
        )

        user_team_manager = UserTeamManager(requester_id=self.requester.id, db=self.db)

        # Get target user team memberships
        target_teams = UserTeam.list(
            requester_id=self.requester.id,
            db=self.db,
            user_id=target_user_id,
            enabled=True,
        )

        # Get initiating user team memberships
        initiating_teams = {
            team.team_id: team
            for team in UserTeam.list(
                requester_id=self.requester.id, db=self.db, user_id=initiating_user_id
            )
        }

        # Transfer teams the initiating user doesn't have
        for team in target_teams:
            if team.team_id not in initiating_teams:
                user_team_manager.create(
                    user_id=initiating_user_id,
                    team_id=team.team_id,  # Added team_id
                    role_id=team.role_id,
                    enabled=team.enabled,
                )
            elif not initiating_teams[team.team_id].enabled:
                # Re-enable the team if disabled for initiating user
                user_team_manager.update(
                    id=initiating_teams[team.team_id].id, enabled=True
                )

        # Transfer metadata
        user_metadata_manager = UserMetadataManager(
            requester_id=self.requester.id, db=self.db
        )
        target_metadata = UserMetadata.list(
            requester_id=self.requester.id, db=self.db, user_id=target_user_id
        )
        initiating_metadata = {
            meta.key: meta
            for meta in UserMetadata.list(
                requester_id=self.requester.id, db=self.db, user_id=initiating_user_id
            )
        }
        for meta in target_metadata:
            if meta.key not in initiating_metadata:
                user_metadata_manager.create(
                    user_id=initiating_user_id, key=meta.key, value=meta.value
                )

        # Disable the target user
        User.update(
            requester_id=self.requester.id,
            db=self.db,
            id=target_user_id,
            new_properties={"active": False},
        )

        return {
            "message": f"Successfully merged user {target_user_id} into {initiating_user_id}",
            "merge_id": merge.id,
        }
