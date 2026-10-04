# SPDX-License-Identifier: AGPL-3.0-or-later
from datetime import datetime
from typing import Any, ClassVar, Dict, List, Optional, Type

from fastapi import HTTPException

from pydantic import Field

from zephyrex.lib.Environment import env
from zephyrex.pydantic2.fastapi import AuthType, RouterMixin
from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    ModelMeta,
    NameMixinModel,
    NumericalSearchModel,
    ParentMixinModel,
    StringSearchModel,
    UpdateMixinModel,
)
from zephyrex.logic.BLL_Auth._shared import BaseModel
from zephyrex.logic.BLL_Auth.team import TeamModel
from zephyrex.logic.BLL_Auth.team_authority import TeamAuthority


class RoleModel(
    ApplicationModel,
    ParentMixinModel,
    NameMixinModel,
    UpdateMixinModel,
    TeamModel.Reference.Optional,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["RoleManager"]] = None  # type: ignore[assignment]
    friendly_name: Optional[str] = Field(None, description="Human-readable role name")
    mfa_count: int = Field(1, description="Number of MFA verifications required")
    password_change_frequency_days: int = Field(
        365, description="How often password must be changed"
    )
    expires_at: Optional[datetime] = Field(None, description="Role expiration date")

    # Database metadata for SQLAlchemy generation
    table_comment: ClassVar[str] = (
        "Permission roles that define what actions users can perform"
    )

    seed_creator_id: ClassVar[str] = env("TEMPLATE_ID")
    seed_data: ClassVar[List[Dict[str, Any]]] = [
        {
            "id": env("USER_ROLE_ID"),
            "name": "user",
            "friendly_name": "User",
            "parent_id": None,
        },
        {
            "id": env("ADMIN_ROLE_ID"),
            "name": "admin",
            "friendly_name": "Admin",
            "parent_id": env("USER_ROLE_ID"),
        },
        {
            "id": env("SUPERADMIN_ROLE_ID"),
            "name": "superadmin",
            "friendly_name": "Superadmin",
            "parent_id": env("ADMIN_ROLE_ID"),
        },
    ]

    class Create(
        BaseModel,
        NameMixinModel,  # Name is required for creation
        ParentMixinModel.Optional,
        TeamModel.Reference.ID.Optional,
    ):
        friendly_name: Optional[str] = Field(
            None, description="Human-readable role name"
        )
        mfa_count: Optional[int] = Field(
            1, description="Number of MFA verifications required"
        )
        password_change_frequency_days: Optional[int] = Field(
            365, description="How often password must be changed"
        )

    class Update(BaseModel):  # Removed mixins to make all fields truly optional
        name: Optional[str] = Field(None, description="Role name")
        friendly_name: Optional[str] = Field(
            None, description="Human-readable role name"
        )
        mfa_count: Optional[int] = Field(
            None, description="Number of MFA verifications required"
        )
        password_change_frequency_days: Optional[int] = Field(
            None, description="How often password must be changed"
        )
        parent_id: Optional[str] = Field(None, description="Parent role ID")

    class Search(
        ApplicationModel.Search,
        NameMixinModel.Search,
        ParentMixinModel.Search,
        TeamModel.Reference.ID.Search,
    ):
        friendly_name: Optional[StringSearchModel] | None = None
        mfa_count: Optional[NumericalSearchModel] | None = None


class RoleManager(AbstractBLLManager, RouterMixin):
    _model = RoleModel
    _entity_label: ClassVar[Optional[str]] = "Role"

    # RouterMixin configuration
    prefix: ClassVar[Optional[str]] = "/v1/role"
    tags: ClassVar[Optional[List[str]]] = ["Role Management"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    # routes_to_register defaults to None, which includes all routes
    auth_dependency: ClassVar[Optional[str]] = "get_role_manager"

    def delete(self, id: str) -> None:
        """Delete a role and reparent its UserTeam assignments.

        Without this override, deleting a role would orphan every
        ``UserTeam`` row referencing it. Behavior:
          - Roles with no active ``UserTeam`` references are deleted as
            usual — there is nothing to orphan.
          - Roles with active references are reparented: each affected
            ``UserTeam`` is updated to the role's ``parent_id`` so
            members fall back to the inherited role.
          - If the role both has active references *and* has no parent
            (a system root role), the delete is refused with 409 —
            silently dropping the assignments would let previously-
            authorized users keep their tokens but lose all permissions.
        """
        role = self.DB.get(
            requester_id=self.requester.id,
            model_registry=self.model_registry,
            id=id,
            return_type="dto",
            override_dto=RoleModel,
        )
        if role is None:
            # Not found (or not visible): the base delete reports it.
            super().delete(id=id)
            return

        from zephyrex.logic.BLL_Auth.user_team import UserTeamModel, UserTeamManager

        ut_db = UserTeamModel.DB(self.model_registry.DB.manager.Base)
        affected = ut_db.list(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            role_id=id,
            return_type="dto",
            override_dto=UserTeamModel,
        )
        if affected:
            if role.parent_id is None:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Refusing to delete a root role that still has "
                        f"{len(affected)} UserTeam assignment(s): every "
                        "such row would be orphaned. Move members off "
                        "this role first, or delete a child role with a "
                        "defined parent."
                    ),
                )
            ut_manager = UserTeamManager(
                requester_id=env("ROOT_ID"),
                model_registry=self.model_registry,
            )
            for ut in affected:
                ut_manager.update(id=ut.id, role_id=role.parent_id)

        super().delete(id=id)

    def update(self, id: str, **kwargs: Any) -> Any:
        """A role's parent sets what everyone holding it may do, so moving
        it is a grant of its new rank to all of them: only someone who may
        grant that rank in the role's team may (TeamAuthority). Without
        this, a role's creator could make it extend admin once it was
        granted to them, though they no longer administer the team."""
        if "parent_id" in kwargs:
            role = self.get(id=id)
            if kwargs["parent_id"] != role.parent_id:
                TeamAuthority(
                    self.requester.id, role.team_id, self.model_registry
                ).assert_may_place(id, kwargs["parent_id"])
        return super().update(id, **kwargs)

    def _register_search_transformers(self):
        self.register_search_transformer("is_system", self._transform_is_system_search)

    def _transform_is_system_search(self, value):
        """Transform is_system search to filter system roles (team_id is NULL)"""
        if value:
            return [RoleModel.DB(self.model_registry.DB.manager.Base).team_id == None]
        return [RoleModel.DB(self.model_registry.DB.manager.Base).team_id != None]

    # Who reads a role is the roles rule in StaticPermissions (_role_filter),
    # which every read path asks: a team's role answers to the team's live
    # members only, its creator included, and a system role to everyone.

    def create_validation(self, entity):
        """Validate role creation."""
        # First, validate that team_id is provided (required for user-created roles)
        # System roles with team_id=None can only be created through seeding, not the API
        if entity.team_id is None:
            raise HTTPException(
                status_code=422,
                detail="team_id is required for role creation",
            )

        # Second, check if team exists (use ROOT_ID to bypass permission checks)
        # This ensures we return 404 only for genuinely non-existent teams,
        # not for teams the user can't access (which should return 403 later)
        if entity.team_id:
            team = TeamModel.DB(self.model_registry.DB.manager.Base).get(
                requester_id=env("ROOT_ID"),
                model_registry=self.model_registry,
                id=entity.team_id,
            )
            if not team:
                raise HTTPException(status_code=404, detail="Team not found")

        # Third, check if parent role exists and is accessible
        if entity.parent_id:
            try:
                parent_role = self.DB.get(
                    requester_id=self.requester.id,
                    model_registry=self.model_registry,
                    id=entity.parent_id,
                )
                if not parent_role:
                    raise HTTPException(status_code=404, detail="Parent role not found")
            except HTTPException:
                raise HTTPException(status_code=404, detail="Parent role not found")

        # Finally, a team's roles are its admins' to shape: a new role's rank
        # goes to whoever later holds it, so, as for a move, the creator must
        # be a team admin and the role may not outrank them.
        TeamAuthority(
            self.requester.id, entity.team_id, self.model_registry
        ).assert_may_create_role(entity.parent_id)

    def search_validation(self, params):
        """Validate search parameters for business logic rules"""
        if "team_id" in params:
            if params["team_id"] in [None, "", "None"]:
                raise HTTPException(status_code=400, detail="Team ID cannot be None")


RoleModel.Manager = RoleManager
