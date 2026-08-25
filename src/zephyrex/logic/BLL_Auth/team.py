import secrets
from typing import Any, ClassVar, Dict, List, Optional, Type

from fastapi import HTTPException

from pydantic import Field, field_validator

from zephyrex.lib.Environment import env
from zephyrex.pydantic2.fastapi import AuthType, RouterMixin
from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    ImageMixinModel,
    ModelMeta,
    NameMixinModel,
    ParentMixinModel,
    StringSearchModel,
    UpdateMixinModel,
)
from zephyrex.logic.BLL_Auth._shared import (
    BaseModel,
    _invitation_hooks,
    _metadata_hooks,
    _validate_team_name,
)


class TeamModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    ParentMixinModel.Optional,
    NameMixinModel.Optional,
    ImageMixinModel.Optional,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["TeamManager"]] = None  # type: ignore[assignment]
    description: Optional[str] = Field(None, description="Team description")
    encryption_salt: Optional[str] = Field(
        ..., description="Per-team salt for row-level encryption of team data"
    )
    # TODO remove these two fields
    token: Optional[str] = Field(None, description="Team token")
    training_data: Optional[str] = Field(None, description="Training data for team")

    # Database metadata for SQLAlchemy generation
    table_comment: ClassVar[str] = "Teams to which users can belong"
    seed_data: ClassVar[List[Dict[str, Any]]] = [
        {
            "id": "FFFFFFFF-FFFF-FFFF-0000-FFFFFFFFFFFF",
            "name": "System",
            "parent_id": None,
            "encryption_salt": "",
        }
    ]

    @classmethod
    def user_has_read_access(
        cls, user_id, id, db, referred=False, db_manager=None, model_registry=None
    ):
        """
        Check if user has read access to a team.
        Read access requires VIEW permission.

        Args:
            user_id: The ID of the user to check
            id: The team ID to check
            db: Database session
            referred: Whether this is a referred check
            db_manager: Database manager instance (deprecated)
            model_registry: Model registry instance (preferred)

        Returns:
            bool: True if read access is granted, False otherwise
        """
        from zephyrex.database.StaticPermissions import (
            PermissionResult,
            PermissionType,
            check_permission,
            is_root_id,
            is_system_user_id,
        )

        # ROOT_ID has read access to everything
        if is_root_id(user_id):
            return True

        # SYSTEM_ID has read access to all teams
        if is_system_user_id(user_id):
            return True

        # Get the team to check creator and deletion rules
        team = None
        if isinstance(id, str):
            team = (
                db.query(cls.DB(db_manager.Base))
                .filter(cls.DB(db_manager.Base).id == id)
                .first()
            )
            if not team:
                return False

            # Check if record is deleted - only ROOT_ID can see
            if hasattr(team, "deleted_at") and team.deleted_at is not None:
                return False

            # Teams created by ROOT_ID can only be viewed by ROOT_ID
            if team.created_by_user_id == env("ROOT_ID"):
                return False

            # Teams created by TEMPLATE_ID can be viewed by everyone
            if team.created_by_user_id == env("TEMPLATE_ID"):
                return True

        # For non-referred checks, check read permissions
        if not referred:
            result, _ = check_permission(user_id, cls.DB, id, db, PermissionType.VIEW)
            return result == PermissionResult.GRANTED

        return False

    class Create(
        BaseModel, NameMixinModel, ParentMixinModel.Optional, ImageMixinModel.Optional
    ):
        description: Optional[str] = Field(None, description="Team description")
        encryption_salt: Optional[str] = Field(
            None, description="Per-team salt for row-level encryption of team data"
        )

        @field_validator("name")
        @classmethod
        def validate_name(cls, v):
            return _validate_team_name(v)

    class Update(
        BaseModel,
        NameMixinModel.Optional,
        ParentMixinModel.Optional,
        ImageMixinModel.Optional,
    ):
        description: Optional[str] = Field(None, description="Team description")
        token: Optional[str] = Field(None, description="Team token")
        training_data: Optional[str] = Field(None, description="Training data for team")

        @field_validator("name")
        @classmethod
        def validate_name(cls, v):
            return _validate_team_name(v)

    class Search(
        ApplicationModel.Search,
        NameMixinModel.Search,
        ParentMixinModel.Search,
        ImageMixinModel.Search,
    ):
        description: Optional[StringSearchModel] | None = None


class TeamManager(AbstractBLLManager, RouterMixin):  # type: ignore[no-redef]
    _model = TeamModel
    _entity_label: ClassVar[Optional[str]] = "Team"

    # RouterMixin configuration
    prefix: ClassVar[Optional[str]] = "/v1/team"
    tags: ClassVar[Optional[List[str]]] = ["Team Management"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    factory_params: ClassVar[List[str]] = ["target_team_id"]
    auth_dependency: ClassVar[Optional[str]] = "get_auth_user"
    custom_routes: ClassVar[List[Dict[str, Any]]] = [
        {
            "path": "/{id}/user",
            "method": "get",
            "function": "get_team_users",
            "summary": "Get team users",
            "description": "Gets users belonging to a team.",
            "response_model": "UserTeamModel.Network.ResponsePlural",
            "status_code": 200,
        },
        {
            "path": "/{team_id}/user/{user_id}",
            "method": "patch",
            "function": "patch_role",
            "summary": "Update user role",
            "description": "Updates a user's role within a team.",
            "response_model": "Dict[str, str]",
            "status_code": 200,
        },
    ]
    nested_resources: ClassVar[Dict[str, Any]] = {
        "invitation": {
            "child_resource_name": "invitation",
            "manager_property": "invitations",
            "child_manager_class": lambda: getattr(
                __import__(
                    "zephyrex.extensions.auth_invitations.BLL_Invitations",
                    fromlist=["InvitationManager"],
                ),
                "InvitationManager",
            ),
            # child_network_model_cls will be inferred from the manager
            "routes_to_register": [
                "get",
                "list",
                "create",
                "search",
                "update",
                "batch_update",
            ],
            "custom_routes": [
                {
                    "path": "",
                    "method": "delete",
                    "function": "revoke_all_invitations",
                    "summary": "Revoke all invitations",
                    "description": "Revokes ALL open invitations for a team.",
                    "status_code": 204,
                },
                {
                    "path": "",
                    "method": "get",
                    "function": "list_invitations_for_team",
                    "summary": "List invitations for team",
                    "description": "Lists all invitations for a team.",
                    "status_code": 200,
                },
            ],
        },
        "metadata": {
            "child_resource_name": "metadata",
            "manager_property": "team_metadata",
            "child_manager_class": lambda: getattr(
                __import__(
                    "zephyrex.extensions.metadata.BLL_Metadata",
                    fromlist=["TeamMetadataManager"],
                ),
                "TeamMetadataManager",
            ),
            # child_network_model_cls will be inferred from the manager
        },
        "role": {
            "child_resource_name": "role",
            "manager_property": "roles",
            "child_manager_class": lambda: getattr(
                __import__("zephyrex.logic.BLL_Auth", fromlist=["RoleManager"]),
                "RoleManager",
            ),
            # child_network_model_cls will be inferred from the manager
            "routes_to_register": [
                "create",
                "list",
                "search",
                "get",
                "update",
                "delete",
                "batch_update",
                "batch_delete",
            ],
        },
    }

    def __init__(
        self,
        requester_id: str,
        target_id: Optional[str] | None = None,
        target_team_id: Optional[str] | None = None,
        model_registry: Optional[Any] | None = None,
    ):
        super().__init__(
            requester_id=requester_id,
            target_id=target_id,
            target_team_id=target_team_id,
            model_registry=model_registry,
        )
        self._team_metadata = None
        self._user_teams = None
        self._roles = None
        self._invitations = None

    @property
    def team_metadata(self):
        if self._team_metadata is None:
            factory = _metadata_hooks["team_manager_factory"]
            if factory is None:
                raise HTTPException(
                    status_code=503,
                    detail="metadata extension not loaded; team metadata unavailable",
                )
            self._team_metadata = factory(
                requester_id=self.requester.id,
                target_team_id=self.target_team_id,
                model_registry=self.model_registry,
                parent=self,
            )
            if self._team_metadata is None:
                raise HTTPException(
                    status_code=503,
                    detail="metadata extension not bound to this registry",
                )
        return self._team_metadata

    @property
    def user_teams(self):
        if self._user_teams is None:
            from zephyrex.logic.BLL_Auth.user_team import UserTeamManager

            self._user_teams = UserTeamManager(
                requester_id=self.requester.id,
                target_id=self.target_user_id,
                target_team_id=self.target_team_id,
                parent=self,
                model_registry=self.model_registry,
            )
        return self._user_teams

    @property
    def roles(self):
        if self._roles is None:
            from zephyrex.logic.BLL_Auth.role import RoleManager

            self._roles = RoleManager(
                requester_id=self.requester.id,
                target_team_id=self.target_team_id,
                parent=self,
                model_registry=self.model_registry,
            )
        return self._roles

    @property
    def invitations(self):
        if self._invitations is None:
            factory = _invitation_hooks["invitation_manager_factory"]
            if factory is None:
                raise HTTPException(
                    status_code=503,
                    detail="auth_invitations extension not loaded; "
                    "team invitations unavailable",
                )
            self._invitations = factory(
                requester_id=self.requester.id,
                target_team_id=self.target_team_id,
                model_registry=self.model_registry,
            )
        return self._invitations

    def create(self, **kwargs):
        """Create a team with metadata"""
        # Extract metadata fields (non-model fields)
        metadata_fields = {}
        model_fields = {}

        # Get the model fields for comparison
        model_fields_set = set(self.Model.Create.__annotations__.keys())
        # TODO #51 Add fields from mixins dynamically that might not be in annotations
        model_fields_set.add("name")
        model_fields_set.add("parent_id")
        model_fields_set.add("description")
        model_fields_set.add("image_url")

        for key, value in kwargs.items():
            if key in model_fields_set:
                model_fields[key] = value
            else:
                metadata_fields[key] = value

        # Mint a per-team salt if the caller did not supply one.
        if "encryption_salt" not in model_fields:
            model_fields["encryption_salt"] = secrets.token_hex(32)

        # Create the team first
        team = super().create(**model_fields)

        # Only proceed with metadata and associations if team creation succeeded
        if team:
            # Create metadata if provided
            if metadata_fields:
                for key, value in metadata_fields.items():
                    self.team_metadata.create(
                        team_id=team.id,
                        key=key,
                        value=str(value),
                    )

            # Add the creator as an admin of the team
            from zephyrex.logic.BLL_Auth.user_team import UserTeamManager

            UserTeamManager(
                requester_id=self.requester.id, model_registry=self.model_registry
            ).create(  # Must create with Root ID or can't see Team (yet).
                team_id=team.id, user_id=self.requester.id, role_id=env("ADMIN_ROLE_ID")
            )

        return team

    def update(self, id: str, **kwargs):
        """Update a team with metadata"""
        from zephyrex.database.StaticPermissions import is_root_id, is_system_id

        if not (is_root_id(self.requester.id) or is_system_id(self.requester.id)):
            db_session = self.model_registry.DB.session()
            try:
                team_db = TeamModel.DB(self.model_registry.DB.manager.Base)
                team = db_session.query(team_db).filter(team_db.id == id).first()
                if team is None:
                    raise HTTPException(status_code=404, detail="Team not found")
                is_creator = team.created_by_user_id == self.requester.id
                from zephyrex.logic.BLL_Auth.user_team import UserTeamModel

                ut_db = UserTeamModel.DB(self.model_registry.DB.manager.Base)
                is_member = (
                    db_session.query(ut_db)
                    .filter(ut_db.team_id == id, ut_db.user_id == self.requester.id)
                    .first()
                    is not None
                )
                if not is_creator and not is_member:
                    raise HTTPException(
                        status_code=403, detail="Not a member of this team"
                    )
            finally:
                db_session.close()

        # Extract metadata fields (non-model fields)
        metadata_fields = {}
        model_fields = {}

        # Get the model fields for comparison
        model_fields_set = set(self.Model.Update.__annotations__.keys())
        # TODO #51 Add fields from mixins dynamically that might not be in annotations
        model_fields_set.add("name")
        model_fields_set.add("parent_id")
        model_fields_set.add("description")
        model_fields_set.add("image_url")
        for key, value in kwargs.items():
            if key in model_fields_set:
                model_fields[key] = value
            else:
                metadata_fields[key] = value

        # Normalize and validate the team name before delegating to the base update logic.
        # This ensures that business rule violations raise ValueError instead of being
        # converted into HTTP exceptions by the abstract manager layer, allowing the
        # calling code and tests to handle them consistently.
        if "name" in model_fields:
            name = model_fields["name"]
            if name is not None:
                normalized_name = name.strip()
                if not normalized_name:
                    raise ValueError("Team name cannot be empty")
                model_fields["name"] = normalized_name

        # Update the team
        team = super().update(id, **model_fields)

        # Update metadata if provided
        if metadata_fields and team:
            existing_metadata = self.team_metadata.list(team_id=id)
            existing_metadata_dict = {item.key: item for item in existing_metadata}

            for key, value in metadata_fields.items():
                if key in existing_metadata_dict:
                    # Update existing metadata
                    self.team_metadata.update(
                        id=existing_metadata_dict[key].id,
                        value=str(value),
                    )
                else:
                    # Create new metadata
                    self.team_metadata.create(
                        team_id=id,
                        key=key,
                        value=str(value),
                    )

        return team

    def get_metadata(self) -> Dict[str, str]:
        """Get all metadata for the target team (via metadata extension)."""
        if not self.target_team_id:
            raise HTTPException(status_code=400, detail="Team ID is required")
        # Reuse the team manager factory so we get the per-team filtered view.
        factory = _metadata_hooks["team_manager_factory"]
        if factory is None:
            return {}
        with factory(
            requester_id=self.requester.id,
            target_team_id=self.target_team_id,
            model_registry=self.model_registry,
        ) as mgr:
            results = mgr.search({"team_id": {"value": self.target_team_id}}) or []
            return {row.key: row.value for row in results}

    def get_team_users(self, id: str):
        """Get users belonging to a team (custom route method)"""
        from zephyrex.logic.BLL_Auth.user import UserManager

        result = self.user_teams.list(team_id=id, include=["users"])
        user_manager = UserManager(
            self.requester.id, model_registry=self.model_registry
        )

        for record in result:
            if record.user is None:
                user = user_manager.get(id=record.user_id)
                record.user = user

        return {"user_teams": result}

    def patch_role(self, team_id: str, user_id: str, body: Dict[str, Any]):
        """Update a user's role within a team (custom route method)"""
        return self.user_teams.patch_role(user_id=user_id, team_id=team_id, body=body)

    def revoke_all_invitations(self, team_id: str):
        """Revoke all invitations for a team (nested custom route method)"""
        # Get all invitations for the team
        invitations = self.invitations.list(team_id=team_id)
        invitation_ids = [
            inv.id if hasattr(inv, "id") else inv["id"] for inv in invitations
        ]

        if invitation_ids:
            self.invitations.batch_delete(ids=invitation_ids)

        return {
            "message": f"Revoked {len(invitation_ids)} invitations for team {team_id}"
        }

    def list_invitations_for_team(self, team_id: str):
        """List all invitations for a team (nested custom route method)"""
        invitations = self.invitations.list(team_id=team_id, include=["invitation"])
        invitations_dict = []

        from zephyrex.pydantic2.registry import obj_to_dict

        for invitation in invitations:
            invitation_dict = obj_to_dict(invitation)

            invitees = self.invitations.Invitee_manager.list(
                invitation_id=invitation.id
            )
            invitees_dict = []
            for invitee in invitees:
                invitee_dict = obj_to_dict(invitee)
                invitee_dict["status"] = (
                    "declined"
                    if invitee.declined_at
                    else "accepted" if invitee.accepted_at else "pending"
                )
                invitees_dict.append(invitee_dict)
            if invitees_dict:
                invitation_dict["invitees"] = invitees_dict

            invitations_dict.append(invitation_dict)
        return {"invitations": invitations_dict}


# Unified Metadata Model
# MetadataModel/Manager moved to extension `metadata` (Scope #3).
# Canonical home: zephyrex.extensions.metadata.BLL_Metadata
# Core access goes through `_metadata_hooks` registered via `register_metadata_hooks`.


TeamModel.Manager = TeamManager
