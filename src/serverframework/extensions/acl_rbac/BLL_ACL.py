"""Canonical ACL/RBAC model and manager (Scope #5 — moved from core).

Owns the per-record `permissions` table and the share/grant manager surface.
The `check_permission` predicate stays in core (`database/StaticPermissions`)
because it is the authorization gate consumed by every BLL operation; this
extension is the opinionated 6-verb (VIEW/EXECUTE/COPY/EDIT/DELETE/SHARE)
ACL shape.

Hooks registered with core via `EXT_ACL.on_load` so `StaticPermissions` and
any future caller can resolve the SA model lazily.
"""

from datetime import datetime
from typing import Any, ClassVar, Dict, List, Optional, Type

from fastapi import HTTPException
from pydantic import Field, model_validator

from serverframework.lib.Pydantic import BaseModel
from serverframework.lib.Pydantic2FastAPI import AuthType, RouterMixin
from serverframework.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    ModelMeta,
    StringSearchModel,
    UpdateMixinModel,
)
from serverframework.logic.BLL_Auth import (
    RoleModel,
    TeamModel,
    UserModel,
)


class PermissionModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    UserModel.Reference.Optional,
    TeamModel.Reference.Optional,
    RoleModel.Reference.Optional,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["PermissionManager"]] = None
    resource_type: str = Field(..., description="Type of resource")
    resource_id: str = Field(..., description="ID of the resource")
    can_view: bool = Field(False, description="Whether user/team can view the resource")
    can_execute: bool = Field(
        False, description="Whether user/team can execute the resource"
    )
    can_copy: bool = Field(
        False, description="Whether user/team can copy the resource"
    )
    can_edit: bool = Field(
        False, description="Whether user/team can edit the resource"
    )
    can_delete: bool = Field(
        False, description="Whether user/team can delete the resource"
    )
    can_share: bool = Field(
        False, description="Whether user/team can share the resource with others"
    )
    expires_at: Optional[datetime] = Field(None, description="Permission expiration")
    enabled: bool = Field(True, description="Whether the permission is enabled")

    table_comment: ClassVar[str] = (
        "Fine-grained permissions for specific resources and actions"
    )
    create_permission_reference: ClassVar[str] = "resource"

    class Create(
        BaseModel,
        UserModel.Reference.ID.Optional,
        TeamModel.Reference.ID.Optional,
        RoleModel.Reference.ID.Optional,
    ):
        resource_type: str = Field(..., description="Type of resource")
        resource_id: str = Field(..., description="ID of the resource")
        can_view: Optional[bool] = Field(False)
        can_execute: Optional[bool] = Field(False)
        can_copy: Optional[bool] = Field(False)
        can_edit: Optional[bool] = Field(False)
        can_delete: Optional[bool] = Field(False)
        can_share: Optional[bool] = Field(False)

        @model_validator(mode="after")
        def validate_permission_combination(self) -> "Create":
            # Case 1: User-specific permission (user_id only)
            if self.user_id and not self.team_id and not self.role_id:
                return self
            # Case 2: Team-specific permission (team_id only)
            if self.team_id and not self.user_id and not self.role_id:
                return self
            # Case 3: Team role-specific permission (team_id and role_id)
            if self.team_id and not self.user_id and self.role_id:
                return self
            # Case 4: System role-specific permission — only allowed when
            # the role itself is team-specific. Cannot validate the role
            # row here (no DB session); manager.create_validation enforces.
            if not self.user_id and not self.team_id and self.role_id:
                raise ValueError(
                    "Role must be team-specific when used without team_id"
                )
            if self.user_id and self.role_id:
                raise ValueError("Cannot have both user_id and role_id")
            if self.user_id and self.team_id:
                raise ValueError(
                    "Invalid permission combination: cannot have both user_id and team_id"
                )
            raise ValueError("Invalid permission combination")

    class Update(BaseModel, RoleModel.Reference.ID.Optional):
        can_view: Optional[bool] = Field(None)
        can_execute: Optional[bool] = Field(None)
        can_copy: Optional[bool] = Field(None)
        can_edit: Optional[bool] = Field(None)
        can_delete: Optional[bool] = Field(None)
        can_share: Optional[bool] = Field(None)

    class Search(
        ApplicationModel.Search,
        UserModel.Reference.ID.Search,
        TeamModel.Reference.ID.Search,
        RoleModel.Reference.ID.Search,
    ):
        resource_type: Optional[StringSearchModel] = None
        resource_id: Optional[StringSearchModel] = None
        can_view: Optional[bool] = None
        can_execute: Optional[bool] = None
        can_copy: Optional[bool] = None
        can_edit: Optional[bool] = None
        can_delete: Optional[bool] = None
        can_share: Optional[bool] = None


class PermissionManager(AbstractBLLManager, RouterMixin):
    _model = PermissionModel

    def create_validation(self, entity):
        if entity.user_id:
            user = UserModel.DB(self.model_registry.DB.manager.Base).get(
                requester_id=self.requester.id,
                model_registry=self.model_registry,
                id=entity.user_id,
            )
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

        if entity.team_id:
            team = TeamModel.DB(self.model_registry.DB.manager.Base).get(
                requester_id=self.requester.id,
                model_registry=self.model_registry,
                id=entity.team_id,
            )
            if not team:
                raise HTTPException(status_code=404, detail="Team not found")

        role = None
        if entity.role_id:
            role = RoleModel.DB(self.model_registry.DB.manager.Base).get(
                requester_id=self.requester.id,
                model_registry=self.model_registry,
                id=entity.role_id,
            )
            if not role:
                raise HTTPException(status_code=404, detail="Role not found")

        if role and not entity.team_id and not entity.user_id:
            if not role.team_id:
                raise ValueError(
                    "Role must be team-specific when used without team_id"
                )


PermissionModel.Manager = PermissionManager


# ---------------------------------------------------------------------------
# Hook registration (Scope #5).
# ---------------------------------------------------------------------------


def _permission_db_class(declarative_base):
    return PermissionModel.DB(declarative_base)


def _create_permission(
    *,
    resource_type,
    resource_id,
    user_id=None,
    team_id=None,
    role_id=None,
    can_view=False,
    can_execute=False,
    can_copy=False,
    can_edit=False,
    can_delete=False,
    can_share=False,
    requester_id=None,
    model_registry=None,
):
    from serverframework.lib.Environment import env

    with PermissionManager(
        requester_id=requester_id or env("ROOT_ID"),
        model_registry=model_registry,
    ) as mgr:
        return mgr.create(
            resource_type=resource_type,
            resource_id=resource_id,
            user_id=user_id,
            team_id=team_id,
            role_id=role_id,
            can_view=can_view,
            can_execute=can_execute,
            can_copy=can_copy,
            can_edit=can_edit,
            can_delete=can_delete,
            can_share=can_share,
        )


try:
    from serverframework.logic.BLL_Auth import register_acl_hooks

    register_acl_hooks(
        permission_db_class=_permission_db_class,
        create_permission=_create_permission,
    )
except ImportError:
    pass


__all__ = ["PermissionModel", "PermissionManager"]
