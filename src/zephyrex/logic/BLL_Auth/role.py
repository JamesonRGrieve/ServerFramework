from datetime import datetime
from typing import Any, ClassVar, Dict, List, Optional, Type

from fastapi import HTTPException

from pydantic import Field

from zephyrex.database.StaticPermissions import can_manage_permissions
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


class RoleModel(
    ApplicationModel,
    ParentMixinModel,
    NameMixinModel,
    UpdateMixinModel,
    TeamModel.Reference.Optional,  # type: ignore[name-defined]
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
        TeamModel.Reference.ID.Optional,  # type: ignore[name-defined]
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
        TeamModel.Reference.ID.Search,  # type: ignore[name-defined]
    ):
        friendly_name: Optional[StringSearchModel] | None = None
        mfa_count: Optional[NumericalSearchModel] | None = None

    create_permission_reference: ClassVar[str] = "resource"

    @classmethod
    def user_can_create(cls, user_id, db, **kwargs):
        """
        Check if a user can create a permission record.
        Users need SHARE permission on the resource they're creating a permission for.
        """
        from zephyrex.database.StaticPermissions import (
            can_manage_permissions,
            is_root_id,
            is_system_user_id,
        )

        # Root and system users can create permissions
        if is_root_id(user_id) or is_system_user_id(user_id):
            return True

        # Check if user can manage permissions for this resource
        resource_type = kwargs.get("resource_type")
        resource_id = kwargs.get("resource_id")

        if not resource_type or not resource_id:
            return False

        # Check if the user has permission to manage permissions on this resource
        can_manage, _ = can_manage_permissions(user_id, resource_type, resource_id, db)
        return can_manage

    @classmethod
    def user_has_admin_access(
        cls, user_id, id, db, db_manager=None, model_registry=None
    ):
        """
        Overrides the default admin access check for Permission records.
        Allow users with explicit permission to edit this record or with SHARE access to the target resource.
        """
        # Get Base from either model_registry or db_manager
        if model_registry:
            Base = model_registry.DB.manager.Base
        elif db_manager:
            Base = db_manager.Base
        else:
            raise ValueError("Either model_registry or db_manager is required")
        from zephyrex.database.StaticPermissions import (
            PermissionResult,
            PermissionType,
            check_permission,
            is_root_id,
            is_system_user_id,
        )

        # Root and system users always have admin access
        if is_root_id(user_id) or is_system_user_id(user_id):
            return True

        # First check standard permission on this record
        result, _ = check_permission(user_id, cls.DB, id, db, PermissionType.EDIT)
        if result == PermissionResult.GRANTED:
            return True

        # If that fails, check if the user can manage permissions for the target resource
        permission = db.query(cls.DB(Base)).filter(cls.DB(Base).id == id).first()
        if permission:
            can_manage, _ = can_manage_permissions(
                user_id, permission.resource_type, permission.resource_id, db
            )
            return can_manage

        return False


class RoleManager(AbstractBLLManager, RouterMixin):  # type: ignore[no-redef]
    _model = RoleModel
    _entity_label: ClassVar[Optional[str]] = "Role"

    # RouterMixin configuration
    prefix: ClassVar[Optional[str]] = "/v1/role"
    tags: ClassVar[Optional[List[str]]] = ["Role Management"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    # routes_to_register defaults to None, which includes all routes
    auth_dependency: ClassVar[Optional[str]] = "get_role_manager"

    def delete(self, id: str | None = None, **kwargs: Any):
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
        target_id = id
        if not target_id:
            return super().delete(id=target_id, **kwargs)

        role = self.DB.get(
            requester_id=self.requester.id,
            model_registry=self.model_registry,
            id=target_id,
            return_type="dto",
            override_dto=RoleModel,
        )
        if role is None:
            return super().delete(id=target_id, **kwargs)

        from zephyrex.logic.BLL_Auth.user_team import UserTeamModel, UserTeamManager

        ut_db = UserTeamModel.DB(self.model_registry.DB.manager.Base)
        affected = ut_db.list(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            role_id=target_id,
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

        return super().delete(id=target_id, **kwargs)

    def _register_search_transformers(self):
        self.register_search_transformer("is_system", self._transform_is_system_search)

    def _transform_is_system_search(self, value):
        """Transform is_system search to filter system roles (team_id is NULL)"""
        if value:
            return [RoleModel.DB(self.model_registry.DB.manager.Base).team_id == None]
        return [RoleModel.DB(self.model_registry.DB.manager.Base).team_id != None]

    def get(
        self,
        include: Optional[List[str]] | None = None,
        fields: Optional[List[str]] = [],
        **kwargs,
    ) -> Any:
        """Get a role with optional included relationships. Returns 404 if not found."""
        role = super().get(include=include, fields=fields, **kwargs)

        # Handle both dict and object access patterns (dict when fields is specified)
        created_by_user_id = (
            role.get("created_by_user_id")
            if isinstance(role, dict)
            else role.created_by_user_id
        )
        team_id = role.get("team_id") if isinstance(role, dict) else role.team_id

        if created_by_user_id != self.requester.id:
            # Business logic validation: if accessing a team-specific role, validate team membership
            if team_id:
                self.validate_user_team(self.requester.id, team_id)

        return role

    def validate_user_team(self, user_id: str, team_id: str):
        """
        Validate that the user has exactly one UserTeam relationship with the specified team.
        This is business logic validation, not permission validation.
        """
        # Use the UserTeamManager class defined later in this file instead of importing it
        from zephyrex.logic.BLL_Auth.user_team import UserTeamManager, UserTeamModel

        user_team_manager = UserTeamManager(
            requester_id=self.requester.id, model_registry=self.model_registry
        )

        # Check that user has exactly one UserTeam relationship with this team
        # Use the database class directly to avoid parameter conflicts
        user_teams = UserTeamModel.DB(self.model_registry.DB.manager.Base).list(
            requester_id=self.requester.id,
            model_registry=self.model_registry,
            user_id=user_id,
            team_id=team_id,
        )

        if len(user_teams) == 0:
            raise HTTPException(
                status_code=403,
                detail="Access denied",
            )
        elif len(user_teams) > 1:
            raise HTTPException(
                status_code=409,
                detail="Request uncovered multiple UserTeam when only one was expected.",
            )

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

        # Finally, validate user-team relationship (business logic, not permissions)
        # Only validate if team_id is provided and not null
        if entity.team_id:
            self.validate_user_team(self.requester.id, entity.team_id)

    def search_validation(self, params):
        """Validate search parameters for business logic rules"""
        if "team_id" in params:
            if params["team_id"] in [None, "", "None"]:
                raise HTTPException(status_code=400, detail="Team ID cannot be None")


RoleModel.Manager = RoleManager
