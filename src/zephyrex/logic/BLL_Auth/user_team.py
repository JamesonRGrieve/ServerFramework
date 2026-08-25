from datetime import datetime
from typing import Any, ClassVar, Dict, List, Optional, Type

from fastapi import HTTPException

from pydantic import Field

from zephyrex.lib.Environment import env
from zephyrex.pydantic2.fastapi import RouterMixin
from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    ModelMeta,
    UpdateMixinModel,
)
from zephyrex.logic.BLL_Auth._shared import BaseModel
from zephyrex.logic.BLL_Auth.user import UserModel
from zephyrex.logic.BLL_Auth.team import TeamModel, TeamManager
from zephyrex.logic.BLL_Auth.role import RoleModel, RoleManager


class UserTeamModel(
    ApplicationModel,
    UpdateMixinModel,
    UserModel.Reference,  # type: ignore[name-defined]
    TeamModel.Reference,  # type: ignore[name-defined]
    RoleModel.Reference,  # type: ignore[name-defined]
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["UserTeamManager"]] = None  # type: ignore[assignment]
    enabled: bool = Field(True, description="Whether this membership is enabled")
    expires_at: Optional[datetime] = Field(
        None, description="When this membership expires"
    )

    # Database metadata for SQLAlchemy generation
    table_comment: ClassVar[str] = (
        "Junction table linking users to teams with assigned roles"
    )

    class Create(
        BaseModel,
        UserModel.Reference.ID,  # type: ignore[name-defined]
        TeamModel.Reference.ID,  # type: ignore[name-defined]
        RoleModel.Reference.ID,  # type: ignore[name-defined]
    ):
        enabled: Optional[bool] = Field(
            True, description="Whether this membership is enabled"
        )

    class Update(BaseModel):
        role_id: Optional[str] = Field(
            None, description="Role ID assigned to the user in this team"
        )
        enabled: Optional[bool] = Field(
            None, description="Whether this membership is enabled"
        )

    class Patch(BaseModel):
        role_id: str = Field(  # type: ignore[assignment]
            None, description="Role ID to be assigned to the user in this team"
        )

    class Search(
        ApplicationModel.Search,
        UserModel.Reference.ID.Search,  # type: ignore[name-defined]
        TeamModel.Reference.ID.Search,  # type: ignore[name-defined]
        RoleModel.Reference.ID.Search,  # type: ignore[name-defined]
    ):
        enabled: Optional[bool] | None = None

    @classmethod
    def user_has_read_access(
        cls,
        user_id,
        team_id,
        db,
        minimum_role=None,
        referred=False,
        db_manager=None,
        model_registry=None,
    ):
        """
        Custom read access logic for user team records:
        Users can see user team record if they belong to the team.

        Args:
            user_id: The ID of the user requesting access
            team_id: The ID of the team that the user should belong to
            db: Database session
            minimum_role: Minimum role required (if applicable)
            referred: Whether this check is part of a referred access check
            db_manager: Database manager instance (deprecated)
            model_registry: Model registry instance (preferred)

        Returns:
            bool: True if access is granted, False otherwise
        """
        # Get Base from either model_registry or db_manager
        if model_registry:
            Base = model_registry.DB.manager.Base
        elif db_manager:
            Base = db_manager.Base
        else:
            # For backward compatibility, if neither is provided, we'll need it later
            Base = None
        from zephyrex.database.StaticPermissions import is_root_id, is_system_user_id

        # ROOT_ID can access everything
        if is_root_id(user_id):
            return True

        # SYSTEM_ID can access most things
        if is_system_user_id(user_id):
            return True

        if Base is None and db_manager:
            Base = db_manager.Base

        record = (
            db.query(cls.DB(Base))
            .filter(
                cls.DB(Base).user_id == user_id,
                cls.DB(Base).team_id == team_id,
            )
            .first()
        )
        if record is None:
            return False

        if hasattr(record, "deleted_at") and record.deleted_at is not None:
            return is_root_id(user_id)

        return True

    @classmethod
    def user_has_admin_access(
        cls, user_id, team_id, db, db_manager=None, model_registry=None
    ):
        """
        Overrides the default admin access check for UserTeam records with better error handling.
        Checks if the user is an admin in the team.

        Args:
            user_id: The ID of the user requesting access
            team_id: The ID of the team that the user should belong to
            db: Database session
            db_manager: Database manager instance (deprecated)
            model_registry: Model registry instance (preferred)

        Returns:
            bool: True if access is granted, False otherwise

        Raises:
            ValueError: If neither model_registry nor db_manager is provided
            Exception: If there are database access issues
        """
        # Get Base from either model_registry or db_manager
        if model_registry:
            Base = model_registry.DB.manager.Base
        elif db_manager:
            Base = db_manager.Base
        else:
            raise ValueError("Either model_registry or db_manager is required")

        from zephyrex.database.StaticPermissions import is_root_id, is_system_user_id
        from zephyrex.lib.Logging import logger

        # Root and system users always have admin access
        if is_root_id(user_id) or is_system_user_id(user_id):
            return True

        try:
            # Query for the specific user-team relationship
            user_team = (
                db.query(cls.DB(Base))
                .filter(
                    cls.DB(Base).user_id == user_id,
                    cls.DB(Base).team_id == team_id,
                )
                .first()
            )

            if user_team is None:
                logger.warning(
                    f"No UserTeam relationship found for user_id={user_id}, team_id={team_id}"
                )
                return False

            # Check if membership is deleted
            if hasattr(user_team, "deleted_at") and user_team.deleted_at is not None:
                logger.warning(
                    f"UserTeam relationship is deleted for user_id={user_id}, team_id={team_id}"
                )
                return False

            # Check if membership is enabled
            if hasattr(user_team, "enabled") and not user_team.enabled:
                logger.warning(
                    f"UserTeam relationship is disabled for user_id={user_id}, team_id={team_id}"
                )
                return False

            # Check if membership has expired
            if hasattr(user_team, "expires_at") and user_team.expires_at:
                from datetime import datetime

                if datetime.utcnow() > user_team.expires_at:
                    logger.warning(
                        f"UserTeam relationship has expired for user_id={user_id}, team_id={team_id}"
                    )
                    return False

            admin_role_id = env("ADMIN_ROLE_ID")
            is_admin = user_team.role_id == admin_role_id

            logger.debug(
                f"Admin access check: user_id={user_id}, team_id={team_id}, "
                f"role_id={user_team.role_id}, admin_role_id={admin_role_id}, is_admin={is_admin}"
            )

            return is_admin

        except Exception as e:
            logger.error(
                f"Database error during admin access check for user_id={user_id}, team_id={team_id}: {str(e)}"
            )
            # Re-raise the exception to be handled by the caller
            raise


class UserTeamManager(AbstractBLLManager, RouterMixin):  # type: ignore[no-redef]
    _model = UserTeamModel
    _entity_label: ClassVar[Optional[str]] = "User Team"

    def get(
        self,
        include: Optional[List[str]] | None = None,
        fields: Optional[List[str]] = [],
        **kwargs,
    ) -> Any:
        """Get a user-team with optional included relationships."""
        result = super().get(include=include, fields=fields, **kwargs)

        # Only check permissions after confirming the record exists
        if "team_id" in kwargs:
            if not self.DB.user_has_read_access(
                self.requester.id, kwargs.get("team_id"), self.db
            ):
                raise HTTPException(status_code=403, detail="get - not permissable")

        return result

    def search(
        self,
        include: Optional[List[str]] | None = None,
        fields: Optional[List[str]] | None = None,
        sort_by: Optional[str] | None = None,
        sort_order: Optional[str] = "asc",
        filters: Optional[List[Any]] | None = None,
        limit: Optional[int] | None = None,
        offset: Optional[int] | None = None,
        page: Optional[int] | None = None,
        pageSize: Optional[int] | None = None,
        **search_params,
    ) -> List[Any]:
        records = super().search(
            include=include,
            fields=fields,
            sort_by=sort_by,
            sort_order=sort_order,
            filters=filters,
            limit=limit,
            offset=offset,
            page=page,
            pageSize=pageSize,
            **search_params,
        )

        if not records:
            return records  # type: ignore[no-any-return]

        def _get_attr(record, attr):
            if isinstance(record, dict):
                return record.get(attr)
            return getattr(record, attr, None)

        def _set_attr(record, attr, value):
            if isinstance(record, dict):
                record[attr] = value
            else:
                setattr(record, attr, value)

        team_ids = {
            team_id
            for team_id in (_get_attr(record, "team_id") for record in records)
            if team_id
        }
        role_ids = {
            role_id
            for role_id in (_get_attr(record, "role_id") for record in records)
            if role_id
        }

        team_map: Dict[str, Any] = {}
        role_map: Dict[str, Any] = {}

        if team_ids:
            team_manager = TeamManager(
                requester_id=self.requester.id,
                model_registry=self.model_registry,
            )
            teams = team_manager.list(filters=[team_manager.DB.id.in_(team_ids)])
            team_map = {team.id: team for team in teams}

        if role_ids:
            role_manager = RoleManager(
                requester_id=self.requester.id,
                model_registry=self.model_registry,
            )
            roles = role_manager.list(filters=[role_manager.DB.id.in_(role_ids)])
            role_map = {role.id: role for role in roles}

        for record in records:
            team_id = _get_attr(record, "team_id")
            if team_id and team_id in team_map:
                _set_attr(record, "team", team_map[team_id])

            role_id = _get_attr(record, "role_id")
            if role_id and role_id in role_map:
                _set_attr(record, "role", role_map[role_id])

        return records  # type: ignore[no-any-return]

    def update(
        self, id: str, team_id: str | None = None, db=None, db_manager=None, **kwargs
    ):
        """Update user team record with improved error handling"""
        db = db or self.db

        # Ensure db_manager is set
        if db_manager is None:
            # Try to get from model_registry if available
            if hasattr(self, "model_registry") and hasattr(self.model_registry, "DB"):
                db_manager = getattr(self.model_registry.DB, "manager", None)
        if db_manager is None:
            raise RuntimeError(
                "db_manager is required for permission checks but was not provided or found."
            )

        if team_id is not None:
            # First check if the requester is a member of the team at all
            try:
                user_team_membership = (
                    db.query(self.Model.DB(db_manager.Base))
                    .filter(
                        self.Model.DB(db_manager.Base).user_id == self.requester.id,
                        self.Model.DB(db_manager.Base).team_id == team_id,
                    )
                    .first()
                )
            except Exception as e:
                from zephyrex.lib.Logging import logger

                logger.error(
                    f"Database error checking team membership for user {self.requester.id} in team {team_id}: {str(e)}"
                )
                raise HTTPException(
                    status_code=500,
                    detail="Internal error while checking team membership",
                )

            if user_team_membership is None:
                raise HTTPException(
                    status_code=403,
                    detail=f"Access denied: You must be a member of team '{team_id}' to modify user roles",
                )

            # Check if user has deleted membership
            if (
                hasattr(user_team_membership, "deleted_at")
                and user_team_membership.deleted_at is not None
            ):
                from zephyrex.database.StaticPermissions import is_root_id

                if not is_root_id(self.requester.id):
                    raise HTTPException(
                        status_code=403,
                        detail="Access denied: Your team membership has been revoked",
                    )

            # Check admin access using the existing method signature
            try:
                has_admin_access = self.DB.user_has_admin_access(
                    self.requester.id,
                    team_id,
                    db,
                    db_manager=db_manager,  # Only pass db_manager, not model_registry
                )
            except Exception as e:
                # Log the specific error for debugging
                from zephyrex.lib.Logging import logger

                logger.error(
                    f"Error checking admin access for user {self.requester.id} in team {team_id}: {str(e)}"
                )
                raise HTTPException(
                    status_code=500, detail="Internal error while checking permissions"
                )

            if not has_admin_access:
                raise HTTPException(
                    status_code=403,
                    detail="Access denied: You must have administrator privileges in this team to modify user roles",
                )

        return super().update(id, **kwargs)

    def validate(self, user_id: str, team_id: str, body: Dict[str, str]):
        try:
            UserModel.DB(self.model_registry.DB.manager.Base).get(
                requester_id=self.requester.id,
                model_registry=self.model_registry,
                id=user_id,
            )
        except Exception:
            raise HTTPException(
                status_code=404,
                detail="Request searched UserModel and could not find the required record.",
            )

        try:
            TeamModel.DB(self.model_registry.DB.manager.Base).get(
                requester_id=self.requester.id,
                model_registry=self.model_registry,
                id=team_id,
            )
        except Exception:
            raise HTTPException(
                status_code=404,
                detail="Request searched TeamModel and could not find the required record.",
            )

        role_id = body["user_team"]["role_id"]  # type: ignore[index]
        try:
            RoleModel.DB(self.model_registry.DB.manager.Base).get(
                requester_id=self.requester.id,
                model_registry=self.model_registry,
                id=role_id,
            )
        except Exception:
            raise HTTPException(
                status_code=404,
                detail="Request searched RoleModel and could not find the required record.",
            )

    def patch_role(self, user_id: str, team_id: str, body: Dict[str, str]):

        self.validate(user_id=user_id, team_id=team_id, body=body)

        # Find the UserTeam record by user_id and team_id
        user_team_list = self.list(team_id=team_id, user_id=user_id)
        if not user_team_list:
            raise HTTPException(
                status_code=404,
                detail=f"User Team with ID 'user_id={user_id}, team_id={team_id}' not found",
            )

        target_user_team = user_team_list[0]

        target_role_id = body["user_team"]["role_id"]  # type: ignore[index]
        updated_data = {"role_id": target_role_id}

        self.update(id=target_user_team.id, team_id=team_id, **updated_data)

        return {"message": "Role updated successfully"}


UserTeamModel.Manager = UserTeamManager
