from datetime import datetime
from typing import Any, ClassVar, Dict, List, Optional, Set, Type, Union

from fastapi import HTTPException

from pydantic import Field

from zephyrex.lib.Environment import env
from zephyrex.lib.Preconditions import expect_route_record
from zephyrex.logic.BLL_Auth.team_authority import TeamAuthority
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
    UserModel.Reference,
    TeamModel.Reference,
    RoleModel.Reference,
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
        UserModel.Reference.ID,
        TeamModel.Reference.ID,
        RoleModel.Reference.ID,
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
        UserModel.Reference.ID.Search,
        TeamModel.Reference.ID.Search,
        RoleModel.Reference.ID.Search,
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


class UserTeamManager(AbstractBLLManager, RouterMixin):
    _model = UserTeamModel
    _entity_label: ClassVar[Optional[str]] = "User Team"

    def get(
        self,
        include: Optional[Union[List[str], str]] = None,
        fields: Optional[Union[List[str], str]] = None,
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
        include: Optional[Union[List[str], str]] = None,
        fields: Optional[Union[List[str], str]] = None,
        sort_by: Optional[str] | None = None,
        sort_order: Optional[str] = "asc",
        filters: Optional[List[Any]] | None = None,
        limit: Optional[int] | None = None,
        offset: Optional[int] | None = None,
        page: Optional[int] | None = None,
        page_size: Optional[int] | None = None,
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
            page_size=page_size,
            pageSize=pageSize,
            **search_params,
        )

        return self.embed_related(records, {"team", "role"})

    def _related_managers(self) -> Dict[str, Any]:
        from zephyrex.logic.BLL_Auth.user import UserManager

        return {"user": UserManager, "team": TeamManager, "role": RoleManager}

    def embed_related(self, records: List[Any], relations: Set[str]) -> List[Any]:
        """Attach each record's ``relations`` (of user, team, role), loaded
        with one ``id IN`` query per relation under the requester's own
        permissions. Unknown relation names are a 400."""
        managers = self._related_managers()
        unknown = relations - set(managers)
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot include {', '.join(sorted(unknown))}",
            )

        def field(record: Any, name: str) -> Any:
            if isinstance(record, dict):
                return record.get(name)
            return getattr(record, name, None)

        for relation in relations:
            ids = {field(r, f"{relation}_id") for r in records} - {None}
            if not ids:
                continue
            manager = managers[relation](
                requester_id=self.requester.id, model_registry=self.model_registry
            )
            by_id = {
                row.id: row for row in manager.list(filters=[manager.DB.id.in_(ids)])
            }
            for record in records:
                related = by_id.get(field(record, f"{relation}_id"))
                if related is None:
                    continue
                if isinstance(record, dict):
                    record[relation] = related
                else:
                    setattr(record, relation, related)
        return records

    def _authority(self, team_id: str) -> TeamAuthority:
        return TeamAuthority(self.requester.id, team_id, self.model_registry)

    def _membership(self, id: str) -> "UserTeamModel":
        """The membership row, read as root: whether the requester may act on
        it is TeamAuthority's decision, not the row filter's."""
        membership = UserTeamModel.DB(self.model_registry.DB.manager.Base).get(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            id=id,
            filters=[
                UserTeamModel.DB(self.model_registry.DB.manager.Base).deleted_at.is_(
                    None
                )
            ],
            allow_nonexistent=True,
            return_type="dto",
            override_dto=UserTeamModel,
        )
        if membership is None:
            raise HTTPException(status_code=404, detail="Membership not found")
        return membership  # type: ignore[no-any-return]

    def create(self, **kwargs: Any) -> Any:
        """Adding a member grants them ``role_id``: only someone who may grant
        that role in the team (TeamAuthority) may do it."""
        self._authority(kwargs["team_id"]).assert_may_grant(kwargs["role_id"])
        return super().create(**kwargs)

    def update(self, id: str, **kwargs: Any) -> Any:
        """Changing a membership acts on its holder, and a new role is a
        grant: both are TeamAuthority's to allow."""
        membership = self._membership(id)
        authority = self._authority(membership.team_id)
        authority.assert_may_act_on(membership.role_id)
        if membership.user_id == self.requester.id and not authority.unlimited:
            # Leaving is the one change a member makes to their own
            # membership; a demotion is another admin's call, so a team
            # cannot be left without one by accident.
            raise HTTPException(
                status_code=403, detail="You cannot change your own membership"
            )
        new_role_id = kwargs.get("role_id") or membership.role_id
        if kwargs.get("role_id"):
            authority.assert_may_grant(new_role_id)
        stays_admin = kwargs.get("enabled", True) and authority.is_admin_role(
            new_role_id
        )
        if not stays_admin and self._is_last_administrator(membership):
            raise HTTPException(
                status_code=409, detail="A team must keep at least one admin"
            )
        return super().update(id, **kwargs)

    def delete(self, id: str) -> None:
        """Removing a member acts on them; a member may always leave. A
        team's last administrator can neither leave nor be removed."""
        membership = self._membership(id)
        authority = self._authority(membership.team_id)
        if membership.user_id != self.requester.id:
            authority.assert_may_act_on(membership.role_id)
        if self._is_last_administrator(membership):
            raise HTTPException(
                status_code=409, detail="A team must keep at least one admin"
            )
        # TeamAuthority has decided; the row's creator (usually the system
        # or root, when it was granted) is not who may remove it.
        self.DB.delete(
            requester_id=env("ROOT_ID"), model_registry=self.model_registry, id=id
        )

    def _is_last_administrator(self, membership: "UserTeamModel") -> bool:
        members = UserTeamModel.DB(self.model_registry.DB.manager.Base).list(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            team_id=membership.team_id,
            return_type="dto",
            override_dto=UserTeamModel,
        )
        administrators = {
            member.user_id
            for member in members
            if TeamAuthority(
                member.user_id, member.team_id, self.model_registry
            ).administers()
        }
        return administrators == {membership.user_id}

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
        with expect_route_record(self, target_user_team.id):
            self.update(id=target_user_team.id, role_id=target_role_id)

        return {"message": "Role updated successfully"}

    def remove_member(self, team_id: str, user_id: str) -> None:
        """Take ``user_id`` off the team (or leave it, for the member
        themselves); see ``delete`` for who may."""
        memberships = UserTeamModel.DB(self.model_registry.DB.manager.Base).list(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            team_id=team_id,
            user_id=user_id,
            return_type="dto",
            override_dto=UserTeamModel,
        )
        if not memberships:
            raise HTTPException(status_code=404, detail="Membership not found")
        with expect_route_record(self, memberships[0].id):
            self.delete(id=memberships[0].id)


UserTeamModel.Manager = UserTeamManager
