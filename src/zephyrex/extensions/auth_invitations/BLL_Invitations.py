"""Canonical invitation/invitee models and managers (Scope #4 — moved from core).

Owns:
- ``InvitationModel`` / ``InvitationManager`` — invitation entity + business logic.
- ``InviteeModel`` / ``InviteeManager`` — per-recipient tracking + acceptance flow.
- ``InvitationAcceptanceResponse`` — the typed response shape for the patch route.

Hooks registered with core via ``EXT_Invitations.on_load`` so
``BLL_Auth.UserManager.register`` can validate and apply invitations without
importing this module directly.
"""

import secrets
import string
from datetime import datetime, timezone
from typing import Any, ClassVar, Dict, List, Optional, Type

from fastapi import HTTPException, status
from pydantic import Field, model_validator

from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger
from zephyrex.lib.Pydantic import BaseModel
from zephyrex.lib.Pydantic2FastAPI import AuthType, RouterMixin
from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    DateSearchModel,
    ModelMeta,
    NumericalSearchModel,
    StringSearchModel,
    UpdateMixinModel,
)
from zephyrex.logic.BLL_Auth import (
    RoleModel,
    TeamManager,
    TeamModel,
    UserManager,
    UserModel,
    UserTeamManager,
)


class InvitationModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    UserModel.Reference.Optional,
    TeamModel.Reference.Optional,
    RoleModel.Reference.Optional,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["InvitationManager"]] = None  # type: ignore[assignment]
    code: Optional[str] = Field(None, description="Invitation code")
    max_uses: Optional[int] = Field(None, description="Maximum number of uses allowed")
    expires_at: Optional[datetime] = Field(None, description="Expiration date/time")

    table_comment: ClassVar[str] = (
        "Invitations to join teams, can be direct or via invitation code"
    )

    class Create(
        BaseModel,
        TeamModel.Reference.ID.Optional,
        RoleModel.Reference.ID.Optional,
        UserModel.Reference.ID.Optional,
    ):
        code: Optional[str] = Field(
            None, description="Invitation code (auto-generated if not provided)"
        )
        max_uses: Optional[int] = Field(
            None, description="Maximum number of uses allowed"
        )
        expires_at: Optional[datetime] = Field(None, description="Expiration date/time")
        email: Optional[str] = Field(
            None, description="Email address of the invitee (if known)"
        )

        @model_validator(mode="after")
        def validate_team_role_combination(self):
            has_team = self.team_id is not None
            has_role = self.role_id is not None
            team_explicitly_set = "team_id" in self.model_fields_set
            role_explicitly_set = "role_id" in self.model_fields_set
            if (
                team_explicitly_set
                and role_explicitly_set
                and not has_team
                and not has_role
            ):
                raise HTTPException(
                    status_code=422,
                    detail="team_id and role_id cannot both be explicitly set to null",
                )
            if has_team != has_role:
                raise HTTPException(
                    status_code=422,
                    detail="team_id and role_id must both be provided together, or both be null for app-level invitations",
                )
            return self

    class Patch(BaseModel):
        invitation_code: Optional[str] = Field(
            None, description="Invitation code to accept"
        )
        invitee_id: Optional[str] = Field(
            None, description="ID of existing invitee record"
        )
        action: Optional[str] = Field(
            None, description="Action to perform (e.g., 'accept', 'decline')"
        )

        @model_validator(mode="after")
        def validate_acceptance_method(self):
            methods_provided = sum(
                [self.invitation_code is not None, self.invitee_id is not None]
            )
            if methods_provided != 1:
                raise HTTPException(
                    status_code=400,
                    detail="Exactly one of invitation_code or invitee_id must be provided",
                )
            return self

    class Accept(BaseModel):
        invitation_code: Optional[str] = Field(
            None, description="Invitation code to accept"
        )
        invitee_id: Optional[str] = Field(
            None, description="ID of existing invitee record"
        )
        action: Optional[str] = Field(
            None, description="Action to perform (e.g., 'accept', 'decline')"
        )

        @model_validator(mode="after")
        def validate_acceptance_method(self):
            methods_provided = sum(
                [self.invitation_code is not None, self.invitee_id is not None]
            )
            if methods_provided != 1:
                raise HTTPException(
                    status_code=400,
                    detail="Exactly one of invitation_code or invitee_id must be provided",
                )
            return self

    class Update(BaseModel, RoleModel.Reference.ID.Optional):
        code: Optional[str] = Field(None, description="Invitation code")
        max_uses: Optional[int] = Field(
            None, description="Maximum number of uses allowed"
        )
        expires_at: Optional[datetime] = Field(None, description="Expiration date/time")

    class Search(
        ApplicationModel.Search,
        TeamModel.Reference.ID.Search,
        RoleModel.Reference.ID.Search,
    ):
        code: Optional[StringSearchModel] = None
        user_id: Optional[StringSearchModel] = None
        max_uses: Optional[NumericalSearchModel] = None
        expires_at: Optional[DateSearchModel] = None


class InvitationAcceptanceResponse(BaseModel):
    """Response model for invitation acceptance."""

    success: bool = Field(
        ..., description="Whether the invitation was accepted successfully"
    )
    message: str = Field(..., description="Success or error message")
    team_id: Optional[str] = Field(None, description="ID of the team joined")
    role_id: Optional[str] = Field(None, description="ID of the role assigned")
    user_team_id: Optional[str] = Field(
        None, description="ID of the user-team relationship created"
    )


class InvitationManager(AbstractBLLManager, RouterMixin):
    _model = InvitationModel

    prefix: ClassVar[Optional[str]] = "/v1/invitation"
    tags: ClassVar[Optional[List[str]]] = ["Team Management"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    auth_dependency: ClassVar[Optional[str]] = "get_invitation_manager"
    custom_routes: ClassVar[List[Dict[str, Any]]] = [
        {
            "path": "/{id}",
            "method": "patch",
            "function": "patch_invitation_endpoint",
            "summary": "Accept invitation",
            "description": "Accept or decline an invitation via code or invitee_id.",
            "response_model": "InvitationAcceptanceResponse",
            "status_code": 200,
        }
    ]

    def __init__(
        self,
        requester_id: str,
        target_id: Optional[str] = None,
        target_team_id: Optional[str] = None,
        model_registry: Optional[Any] = None,
    ):
        super().__init__(
            requester_id=requester_id,
            target_id=target_id,
            target_team_id=target_team_id,
            model_registry=model_registry,
        )
        self._Invitee_manager = None

    @property
    def Invitee_manager(self):
        if self._Invitee_manager is None:
            self._Invitee_manager = InviteeManager(
                requester_id=self.requester.id,
                target_team_id=self.target_team_id,
                parent=self,
                model_registry=self.model_registry,
            )
        return self._Invitee_manager

    def create_validation(self, entity):
        if entity.team_id and not entity.role_id:
            raise HTTPException(
                status_code=400, detail="team_id and role_id must both be provided"
            )
        if entity.role_id and not entity.team_id:
            raise HTTPException(
                status_code=400, detail="team_id and role_id must both be provided"
            )
        if entity.team_id:
            team = TeamModel.DB(self.model_registry.DB.manager.Base).get(
                requester_id=env("ROOT_ID"),
                model_registry=self.model_registry,
                id=entity.team_id,
            )
            if not team:
                raise HTTPException(status_code=404, detail="Team not found")
        if entity.role_id:
            role = RoleModel.DB(self.model_registry.DB.manager.Base).get(
                requester_id=env("ROOT_ID"),
                model_registry=self.model_registry,
                id=entity.role_id,
            )
            if not role:
                raise HTTPException(status_code=404, detail="Role not found")

    def create(self, **kwargs):
        has_team_role = kwargs.get("team_id") and kwargs.get("role_id")
        if has_team_role and ("code" not in kwargs or not kwargs["code"]):
            kwargs["code"] = "".join(
                secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8)
            )
        elif not has_team_role:
            kwargs.pop("code", None)

        existing_invitations = self.list(
            team_id=kwargs.get("team_id"),
            role_id=kwargs.get("role_id"),
            deleted_at=None,
        )

        if "email" in kwargs:
            email = kwargs.pop("email")
            if not email:
                raise HTTPException(status_code=400, detail="empty")
            emails = [email] if isinstance(email, str) else email

            existing_invitees = []
            for invitation in existing_invitations:
                invitees = self.Invitee_manager.list(
                    invitation_id=invitation.id, deleted_at=None, declined_at=None
                )
                for invitee in invitees:
                    existing_invitees.append(invitee.email)

            for em in emails:
                em = em.lower().strip()
                if em in existing_invitees:
                    emails.remove(em)

            if not emails:
                raise HTTPException(status_code=400, detail="already invited")

            invitation = super().create(**kwargs)
            for em in emails:
                self.add_invitee(invitation_id=invitation.id, email=em)
            return invitation

        user = None
        if "user_id" in kwargs:
            user_id = kwargs.get("user_id")
            for invitation in existing_invitations:
                if user_id == invitation.user_id:
                    raise HTTPException(
                        status_code=400, detail=f"user {user_id} already invited"
                    )
            user_manager = UserManager(
                requester_id=self.requester.id,
                target_id=user_id,
                model_registry=self.model_registry,
            )
            user = user_manager.get()

        invitation = super().create(**kwargs)
        if user is not None:
            invitation.user = user
        return invitation

    @staticmethod
    def generate_invitation_link(code: str, email: Optional[str] = None) -> str:
        base_url = env("APP_URI")
        if not email:
            return f"{base_url}/join?code={code}"
        return f"{base_url}/join?code={code}?email={email}"

    def add_invitee(self, invitation_id: str, email: str) -> Dict[str, Any]:
        if not invitation_id:
            raise HTTPException(status_code=404, detail="Invitation not found")

        invitation = self.get(id=invitation_id)

        user_manager = UserManager(
            requester_id=self.requester.id, model_registry=self.model_registry
        )
        user_id = None
        try:
            user = user_manager.list(email=email.lower().strip())
            if user:
                user_id = user[0].id
        except HTTPException as e:
            logger.warning(
                f"User lookup for invitee {email!r} failed: {e.detail}", exc_info=True
            )

        invitee = self.Invitee_manager.create(
            invitation_id=invitation_id,
            email=email.lower().strip(),
            user_id=user_id,
        )

        invitation_link = None
        if invitation.code:
            invitation_link = self.generate_invitation_link(invitation.code)

        if invitation.team_id not in (None, ""):
            with TeamManager(
                requester_id=self.requester.id,
                target_id=invitation.team_id,
                model_registry=self.model_registry,
            ) as team_manager:
                team = team_manager.get(id=invitation.team_id)
                invitation.team = team

        if invitee.invitation is None:
            invitee.invitation = invitation

        try:
            from zephyrex.extensions.email.BLL_EMail import (
                send_invitation_email_hook,
            )

            send_invitation_email_hook(manager=self, entity=invitee)
        except Exception as e:
            logger.error(
                f"Failed to send invitation email for invitation {invitation.id}: {str(e)}"
            )

        return {
            "invitation_id": invitation_id,
            "invitation_code": invitation.code,
            "invitation_link": invitation_link,
            "user_id": user_id,
            "email": email.lower().strip(),
        }

    def get(
        self,
        include: Optional[List[str]] = None,
        fields: Optional[List[str]] = [],
        **kwargs,
    ) -> Any:
        options = []
        fields = self.validate_fields(fields)
        include_list = self.validate_includes(include)
        if include_list:
            options = self.generate_joins(self.DB, include_list)
        invitation = self.DB.get(
            requester_id=self.requester.id,
            fields=fields,
            model_registry=self.model_registry,
            return_type="dto" if not fields else "dict",
            override_dto=self.Model if not fields else None,
            options=options,
            **kwargs,
        )
        if invitation is None:
            invitation_id = kwargs.get("id") or kwargs.get("invitation_id") or "unknown"
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Invitation with ID '{invitation_id}' not found",
            )
        return invitation

    def accept_invitation_unified(
        self, accept_data: "InvitationModel.Accept", user_id: str
    ) -> Dict[str, Any]:
        patch_data = InvitationModel.Patch(**accept_data.model_dump())
        return self.patch_invitation_unified(patch_data, user_id)

    def patch_invitation_unified(
        self, patch_data: "InvitationModel.Patch", user_id: str
    ) -> Dict[str, Any]:
        if patch_data.invitation_code:
            try:
                if patch_data.action and patch_data.action.lower() == "decline":
                    result = self.Invitee_manager.decline_invitation(
                        patch_data.invitation_code
                    )
                    return {
                        "success": True,
                        "message": "Invitation declined successfully via code",
                        "team_id": result.get("team_id"),
                        "role_id": result.get("role_id"),
                    }
                else:
                    result = self.Invitee_manager.accept_invitation(
                        patch_data.invitation_code, user_id
                    )
                    return {
                        "success": True,
                        "message": "Invitation accepted successfully via code",
                        "team_id": result.get("team_id"),
                        "role_id": result.get("role_id"),
                        "user_team_id": result.get("user_team_id"),
                    }
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(
                    status_code=404, detail=f"Failed to accept invitation: {str(e)}"
                )

        elif patch_data.invitee_id:
            try:
                invitee = self.Invitee_manager.get(id=patch_data.invitee_id)
                user_manager = UserManager(
                    requester_id=env("ROOT_ID"), model_registry=self.model_registry
                )
                user = user_manager.get(id=user_id)
                if user.email.lower() != invitee.email.lower():
                    raise HTTPException(
                        status_code=403,
                        detail="User email does not match invitation email",
                    )
                if invitee.accepted_at:
                    raise HTTPException(
                        status_code=409, detail="Invitation already accepted"
                    )
                if invitee.declined_at:
                    raise HTTPException(
                        status_code=409, detail="Invitation was previously declined"
                    )
                invitation = self.get(id=invitee.invitation_id)
                if invitation.expires_at:
                    expires_at = invitation.expires_at
                    if expires_at.tzinfo is None:
                        expires_at = expires_at.replace(tzinfo=timezone.utc)
                    if expires_at < datetime.now(timezone.utc):
                        raise HTTPException(
                            status_code=410, detail="Invitation has expired"
                        )

                if patch_data.action and patch_data.action.lower() == "decline":
                    self.Invitee_manager.update(
                        id=invitee.id,
                        declined_at=datetime.now(timezone.utc),
                        user_id=user_id,
                    )
                    if invitation.team_id and invitation.role_id:
                        return {
                            "success": True,
                            "message": "Invitation declined successfully via invitee ID",
                            "team_id": invitation.team_id,
                            "role_id": invitation.role_id,
                        }

                self.Invitee_manager.update(
                    id=invitee.id,
                    accepted_at=datetime.now(timezone.utc),
                    user_id=user_id,
                )

                user_team_id = None
                if invitation.team_id and invitation.role_id:
                    user_team_manager = UserTeamManager(
                        requester_id=env("ROOT_ID"),
                        target_id=user.id,
                        model_registry=self.model_registry,
                    )
                    existing_memberships = user_team_manager.list(
                        user_id=user_id,
                        team_id=invitation.team_id,
                    )
                    if existing_memberships:
                        user_team = user_team_manager.update(
                            id=existing_memberships[0].id,
                            role_id=invitation.role_id,
                            enabled=True,
                        )
                        user_team_id = existing_memberships[0].id
                    else:
                        user_team = user_team_manager.create(
                            user_id=user_id,
                            team_id=invitation.team_id,
                            role_id=invitation.role_id,
                            enabled=True,
                        )
                        user_team_id = user_team.id

                return {
                    "success": True,
                    "message": "Invitation accepted successfully via invitee ID",
                    "team_id": invitation.team_id,
                    "role_id": invitation.role_id,
                    "user_team_id": user_team_id,
                }
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(
                    status_code=500, detail=f"Failed to accept invitation: {str(e)}"
                )

        else:
            raise HTTPException(
                status_code=400,
                detail="Exactly one of invitation_code or invitee_id must be provided",
            )

    def accept_invitation(self, code: str, user_id: str) -> Dict[str, Any]:
        return self.Invitee_manager.accept_invitation(code, user_id)  # type: ignore[no-any-return]

    def patch_invitation_endpoint(
        self, id: str, body: Dict[str, Any]
    ) -> Dict[str, Any]:
        patch_data = body.get("invitation")
        if not patch_data:
            raise HTTPException(status_code=400, detail="Missing invitation data")
        patch_model = InvitationModel.Patch(**patch_data)
        result = self.patch_invitation_unified(patch_model, self.requester.id)
        return result


class InviteeModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    UserModel.Reference.Optional,
    InvitationModel.Reference,  # type: ignore[name-defined]
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["InviteeManager"]] = None  # type: ignore[assignment]
    email: str = Field(..., description="Email of the invitee")
    declined_at: Optional[datetime] = Field(
        None, description="When the invitation was declined"
    )
    accepted_at: Optional[datetime] = Field(
        None, description="When the invitation was accepted"
    )

    table_comment: ClassVar[str] = "Tracks specific individuals invited to join a team"

    class Create(
        BaseModel, InvitationModel.Reference.ID, UserModel.Reference.ID.Optional  # type: ignore[name-defined]
    ):
        email: str = Field(..., description="Email of the invitee")
        declined_at: Optional[datetime] = Field(None)
        accepted_at: Optional[datetime] = Field(None)

    class Update(BaseModel, UserModel.Reference.ID.Optional):
        declined_at: Optional[datetime] = Field(None)
        accepted_at: Optional[datetime] = Field(None)

    class Search(
        ApplicationModel.Search,
        InvitationModel.Reference.ID.Search,  # type: ignore[name-defined]
        UserModel.Reference.ID.Search,
    ):
        email: Optional[StringSearchModel] = None
        declined_at: Optional[DateSearchModel] = Field(None)
        accepted_at: Optional[DateSearchModel] = Field(None)


class InviteeManager(AbstractBLLManager):
    _model = InviteeModel

    def create_validation(self, entity):
        if "@" not in entity.email:
            raise HTTPException(status_code=400, detail="Invalid email format")
        existing = InviteeModel.DB(self.model_registry.DB.manager.Base).exists(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            invitation_id=entity.invitation_id,
            email=entity.email.lower().strip(),
        )
        if existing:
            raise HTTPException(
                status_code=400, detail="This email has already been invited"
            )

    def accept_invitation_by_email(self, code: str, email: str) -> Dict[str, Any]:
        invitation = InvitationModel.DB(self.model_registry.DB.manager.Base).get(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            code=code,
            return_type="dto",
            override_dto=InvitationModel,
        )
        if not invitation:
            raise HTTPException(status_code=404, detail="Invalid invitation code")
        if invitation.expires_at:
            expires_at = invitation.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at < datetime.now(timezone.utc):
                raise HTTPException(status_code=410, detail="Invitation has expired")
        if invitation.max_uses is not None:
            InviteeDB = InviteeModel.DB(self.model_registry.DB.manager.Base)
            used_count = InviteeDB.count(
                requester_id=env("ROOT_ID"),
                model_registry=self.model_registry,
                invitation_id=invitation.id,
                filters=[InviteeDB.accepted_at.isnot(None)],
            )
            if used_count >= invitation.max_uses:
                raise HTTPException(
                    status_code=410,
                    detail="Invitation has reached maximum usage limit",
                )

        existing_invitees = InviteeModel.DB(self.model_registry.DB.manager.Base).list(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            invitation_id=invitation.id,
            email=email.lower().strip(),
            override_dto=InviteeModel,
            return_type="dto",
        )
        if not existing_invitees:
            self.create(
                invitation_id=invitation.id,
                email=email.lower().strip(),
                user_id=None,
            )

        return {
            "invitation_id": invitation.id,
            "team_id": invitation.team_id,
            "role_id": invitation.role_id,
            "code": invitation.code,
        }

    def decline_invitation(self, code: str) -> Dict[str, Any]:
        invitation = InvitationModel.DB(self.model_registry.DB.manager.Base).get(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            code=code,
            return_type="dto",
            override_dto=InvitationModel,
        )
        if not invitation:
            raise HTTPException(status_code=404, detail="Invalid invitation code")
        user = UserModel.DB(self.model_registry.DB.manager.Base).get(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            id=self.requester.id,
            return_type="dto",
            override_dto=UserModel,
        )
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        inviteeDB = InviteeModel.DB(self.model_registry.DB.manager.Base)
        invitees = inviteeDB.list(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            invitation_id=invitation.id,
            email=user.email,
            override_dto=InviteeModel,
            return_type="dto",
            filters=[
                inviteeDB.accepted_at.is_(None),
                inviteeDB.declined_at.is_(None),
            ],
        )
        if invitees:
            invitee = invitees[0]
            self.update(
                id=invitee.id,
                declined_at=datetime.now(timezone.utc),
                user_id=self.requester.id,
            )

        return {
            "success": True,
            "team_id": invitation.team_id,
            "role_id": invitation.role_id,
            "message": "Invitation declined successfully",
        }

    def accept_invitation(self, code: str, user_id: str) -> Dict[str, Any]:
        invitation = InvitationModel.DB(self.model_registry.DB.manager.Base).get(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            code=code,
            return_type="dto",
            override_dto=InvitationModel,
        )
        if not invitation:
            raise HTTPException(status_code=404, detail="Invalid invitation code")
        if invitation.expires_at:
            expires_at = invitation.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at < datetime.now(timezone.utc):
                raise HTTPException(status_code=410, detail="Invitation has expired")
        if invitation.max_uses is not None:
            InviteeDB = InviteeModel.DB(self.model_registry.DB.manager.Base)
            used_count = InviteeDB.count(
                requester_id=env("ROOT_ID"),
                model_registry=self.model_registry,
                invitation_id=invitation.id,
                filters=[InviteeDB.accepted_at.isnot(None)],
            )
            if used_count >= invitation.max_uses:
                raise HTTPException(
                    status_code=410,
                    detail="Invitation has reached maximum usage limit",
                )
        user = UserModel.DB(self.model_registry.DB.manager.Base).get(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            id=user_id,
            return_type="dto",
            override_dto=UserModel,
        )
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        invitees = InviteeModel.DB(self.model_registry.DB.manager.Base).list(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            invitation_id=invitation.id,
            email=user.email,
            override_dto=InviteeModel,
            return_type="dto",
        )
        if not invitees:
            if not invitation.code:
                raise HTTPException(
                    status_code=403, detail="Your email is not invited to this team"
                )
            else:
                invitee = self.create(
                    invitation_id=invitation.id,
                    email=user.email,
                    accepted_at=datetime.now(timezone.utc),
                    user_id=user_id,
                )
        else:
            invitee = invitees[0]
            self.update(
                id=invitee.id,
                accepted_at=datetime.now(timezone.utc),
                user_id=user_id,
            )

        user_team_manager = UserTeamManager(
            requester_id=invitation.created_by_user_id,
            target_id=user_id,
            model_registry=self.model_registry,
        )
        existing_team_membership = user_team_manager.list(
            user_id=user_id,
            team_id=invitation.team_id,
        )
        if existing_team_membership:
            user_team = user_team_manager.update(
                id=existing_team_membership[0].id,
                role_id=invitation.role_id,
                enabled=True,
            )
        else:
            user_team = user_team_manager.create(
                user_id=user_id,
                team_id=invitation.team_id,
                role_id=invitation.role_id,
                enabled=True,
            )

        return {
            "success": True,
            "team_id": invitation.team_id,
            "role_id": invitation.role_id,
            "user_team_id": user_team.id,
        }


InvitationModel.Manager = InvitationManager
InviteeModel.Manager = InviteeManager


# ---------------------------------------------------------------------------
# Hook registration (Scope #4). See metadata.BLL_Metadata for the rationale
# behind module-level registration.
# ---------------------------------------------------------------------------


def _lookup_by_id(invitation_id: str, model_registry):
    db = model_registry.DB.session()
    inv = (
        db.query(InvitationModel.DB(model_registry.DB.manager.Base))
        .filter(
            InvitationModel.DB(model_registry.DB.manager.Base).id == invitation_id,
            InvitationModel.DB(model_registry.DB.manager.Base).deleted_at.is_(None),
        )
        .first()
    )
    if inv is None:
        return None
    if inv.expires_at and inv.expires_at < datetime.now(timezone.utc):
        return None
    return {
        "id": inv.id,
        "code": inv.code,
        "team_id": inv.team_id,
        "role_id": inv.role_id,
        "acceptance_type": "direct_email_invite",
    }


def _lookup_by_code(invitation_code: str, model_registry):
    db = model_registry.DB.session()
    inv = (
        db.query(InvitationModel.DB(model_registry.DB.manager.Base))
        .filter(
            InvitationModel.DB(model_registry.DB.manager.Base).code == invitation_code,
            InvitationModel.DB(model_registry.DB.manager.Base).deleted_at.is_(None),
        )
        .first()
    )
    if inv is None:
        return None
    if inv.expires_at and inv.expires_at < datetime.now(timezone.utc):
        return None
    return {
        "id": inv.id,
        "code": inv.code,
        "team_id": inv.team_id,
        "role_id": inv.role_id,
        "acceptance_type": "public_code",
    }


def _apply_to_user(invitation, user_id, model_registry):
    user_manager = UserManager(
        requester_id=env("ROOT_ID"), model_registry=model_registry
    )
    user = user_manager.get(id=user_id)
    user_email = user.email.lower() if hasattr(user, "email") else None

    invitee_manager = InviteeManager(
        requester_id=env("ROOT_ID"), model_registry=model_registry
    )

    if user_email:
        existing_invitees = invitee_manager.list(
            invitation_id=invitation["id"], email=user_email
        )
        if existing_invitees:
            invitee = existing_invitees[0]
            invitee_manager.update(
                id=invitee.id,
                accepted_at=datetime.now(timezone.utc),
                user_id=user_id,
            )
        else:
            invitee_manager.create(
                invitation_id=invitation["id"],
                email=user_email,
                accepted_at=datetime.now(timezone.utc),
                user_id=user_id,
            )

    if invitation.get("team_id") and invitation.get("role_id"):
        user_team_manager = UserTeamManager(
            requester_id=env("ROOT_ID"),
            target_id=user_id,
            model_registry=model_registry,
        )
        existing = user_team_manager.list(
            user_id=user_id, team_id=invitation["team_id"]
        )
        if existing:
            user_team_manager.update(
                id=existing[0].id,
                role_id=invitation["role_id"],
                enabled=True,
            )
        else:
            user_team_manager.create(
                user_id=user_id,
                team_id=invitation["team_id"],
                role_id=invitation["role_id"],
                enabled=True,
            )


def _invitation_manager_factory(requester_id, target_team_id, model_registry, **kw):
    return InvitationManager(
        requester_id=requester_id,
        target_team_id=target_team_id,
        model_registry=model_registry,
        **kw,
    )


def _invitee_manager_factory(requester_id, target_id, model_registry, **kw):
    return InviteeManager(
        requester_id=requester_id,
        target_id=target_id,
        model_registry=model_registry,
        **kw,
    )


def _list_invitees_for_user(user_id, email, model_registry):
    InviteeDB = InviteeModel.DB(model_registry.DB.manager.Base)
    filters = {"user_id": user_id}
    if email:
        filters["email"] = email.lower().strip()
    items = InviteeDB.list(
        requester_id=env("ROOT_ID"),
        model_registry=model_registry,
        **filters,
    )
    return [
        {
            "id": inv.id,
            "invitation_id": inv.invitation_id,
            "email": inv.email,
            "accepted_at": inv.accepted_at,
            "declined_at": inv.declined_at,
        }
        for inv in (items or [])
    ]


def _invitation_db_class(declarative_base):
    """Return the SA model bound to ``declarative_base`` for the
    ``invitations`` table. Consumed by ``StaticPermissions`` so the core
    permission filter can include invitation-driven team access without
    importing the extension directly."""
    return InvitationModel.DB(declarative_base)


def _invitee_db_class(declarative_base):
    return InviteeModel.DB(declarative_base)


try:
    from zephyrex.logic.BLL_Auth import register_invitation_hooks

    register_invitation_hooks(
        lookup_by_id=_lookup_by_id,
        lookup_by_code=_lookup_by_code,
        apply_to_user=_apply_to_user,
        invitation_manager_factory=_invitation_manager_factory,
        invitee_manager_factory=_invitee_manager_factory,
        list_invitees_for_user=_list_invitees_for_user,
        invitation_db_class=_invitation_db_class,
        invitee_db_class=_invitee_db_class,
    )
except ImportError:
    pass


__all__ = [
    "InvitationModel",
    "InvitationManager",
    "InvitationAcceptanceResponse",
    "InviteeModel",
    "InviteeManager",
]
