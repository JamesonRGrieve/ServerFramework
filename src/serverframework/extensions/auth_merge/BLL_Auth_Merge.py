"""User-merge BLL.

Records that a *target* user account was consolidated into an *initiating*
user account. Movement of side data (team memberships, etc.) is delegated
to the canonical managers in ``serverframework.logic.BLL_Auth``; this
extension only owns the audit row.

Pattern reference: ``auth_invitations/BLL_Invitations.py``.
"""

from datetime import datetime
from typing import ClassVar, Dict, List, Optional, Type

from fastapi import HTTPException
from pydantic import BaseModel, Field

from serverframework.lib.Environment import env
from serverframework.lib.Pydantic2FastAPI import AuthType, RouterMixin
from serverframework.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    DateSearchModel,
    ModelMeta,
    StringSearchModel,
    UpdateMixinModel,
)
from serverframework.logic.BLL_Auth import (
    UserManager,
    UserModel,
    UserTeamManager,
    UserTeamModel,
)


class UserMergeModel(
    ApplicationModel,
    UpdateMixinModel,
    metaclass=ModelMeta,
):
    """Audit record of a user-account merge."""

    Manager: ClassVar[Type["UserMergeManager"]] = None
    initiating_user_id: str = Field(
        ..., description="User who survives the merge (data-owner)"
    )
    target_user_id: str = Field(
        ..., description="User being merged into the initiating user; deactivated."
    )
    completed_at: Optional[datetime] = Field(
        None, description="When the side-data transfer finished"
    )

    table_comment: ClassVar[str] = (
        "Audit record of user-account consolidation events"
    )

    class Create(BaseModel):
        initiating_user_id: str
        target_user_id: str

    class Update(BaseModel):
        completed_at: Optional[datetime] = None

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        initiating_user_id: Optional[StringSearchModel] = None
        target_user_id: Optional[StringSearchModel] = None
        completed_at: Optional[DateSearchModel] = None


class UserMergeManager(AbstractBLLManager, RouterMixin):
    _model = UserMergeModel
    prefix: ClassVar[Optional[str]] = "/v1/auth/user-merge"
    tags: ClassVar[Optional[List[str]]] = ["User Merge"]
    auth_type: ClassVar[AuthType] = AuthType.JWT

    def _validate(self, initiating_user_id: str, target_user_id: str) -> None:
        if initiating_user_id == target_user_id:
            raise HTTPException(
                status_code=400, detail="Cannot merge a user with themselves"
            )
        UserDB = UserModel.DB(self.model_registry.DB.manager.Base)
        for user_id, role in (
            (initiating_user_id, "initiating"),
            (target_user_id, "target"),
        ):
            if (
                UserDB.get(
                    requester_id=env("ROOT_ID"),
                    model_registry=self.model_registry,
                    id=user_id,
                    return_type="dto",
                    override_dto=UserModel,
                )
                is None
            ):
                raise HTTPException(
                    status_code=404, detail=f"{role} user not found: {user_id}"
                )

    def merge_users(
        self, initiating_user_id: str, target_user_id: str
    ) -> Dict[str, str]:
        """Merge ``target_user_id`` into ``initiating_user_id``.

        Side-data transfer is restricted to team memberships. Other extension
        data (notifications, OAuth links, etc.) registers its own merge
        hooks against ``UserMergeManager.merge_users`` to participate.
        """
        self._validate(initiating_user_id, target_user_id)

        merge = self.create(
            initiating_user_id=initiating_user_id,
            target_user_id=target_user_id,
        )

        UserTeamDB = UserTeamModel.DB(self.model_registry.DB.manager.Base)
        target_memberships = (
            UserTeamDB.list(
                requester_id=env("ROOT_ID"),
                model_registry=self.model_registry,
                filters=[UserTeamDB.user_id == target_user_id],
                return_type="dto",
                override_dto=UserTeamModel,
            )
            or []
        )
        initiating_memberships = {
            m.team_id: m
            for m in (
                UserTeamDB.list(
                    requester_id=env("ROOT_ID"),
                    model_registry=self.model_registry,
                    filters=[UserTeamDB.user_id == initiating_user_id],
                    return_type="dto",
                    override_dto=UserTeamModel,
                )
                or []
            )
        }

        ut_manager = UserTeamManager(
            requester_id=self.requester.id, model_registry=self.model_registry
        )
        for membership in target_memberships:
            if membership.team_id not in initiating_memberships:
                ut_manager.create(
                    user_id=initiating_user_id,
                    team_id=membership.team_id,
                    role_id=membership.role_id,
                )

        UserDB = UserModel.DB(self.model_registry.DB.manager.Base)
        UserDB.update(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            id=target_user_id,
            new_properties={"active": False},
        )

        self.update(
            id=merge.id, completed_at=datetime.utcnow()
        )
        return {
            "message": (
                f"Successfully merged user {target_user_id} into {initiating_user_id}"
            ),
            "merge_id": merge.id,
        }


UserMergeModel.Manager = UserMergeManager
