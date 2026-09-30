# SPDX-License-Identifier: AGPL-3.0-or-later
"""Canonical invitation/invitee models and managers (Scope #4 — moved from core).

Owns:
- ``InvitationModel`` / ``InvitationManager`` — invitation entity + business logic.
- ``InviteeModel`` / ``InviteeManager`` — per-recipient tracking + acceptance flow.
- ``InvitationAcceptanceResponse`` — the typed response shape for the patch route.

Hooks registered with core when this module is imported so
``BLL_Auth.UserManager.register`` can validate and apply invitations without
importing this module directly.
"""

import secrets
import string
from datetime import datetime, timezone
from typing import Any, ClassVar, Dict, List, Optional, Type, Union

from zephyrex.lib.DateTimeUtils import ensure_utc

from fastapi import HTTPException, status
from pydantic import Field, model_validator
from sqlalchemy import or_

from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger
from zephyrex.pydantic2.registry import BaseModel
from zephyrex.pydantic2.fastapi import AuthType, RouterMixin
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
from zephyrex.logic.BLL_Auth.team_authority import TeamAuthority


def _expired(expires_at: Optional[datetime]) -> bool:
    return expires_at is not None and ensure_utc(expires_at) < datetime.now(
        timezone.utc
    )


def _assert_unexpired(invitation: Any) -> None:
    if _expired(invitation.expires_at):
        raise HTTPException(status_code=410, detail="Invitation has expired")


def _assert_redeemable(invitation: Any, model_registry: Any) -> None:
    """410 when the invitation expired or its uses are spent."""
    _assert_unexpired(invitation)
    if invitation.max_uses is None:
        return
    InviteeDB = InviteeModel.DB(model_registry.DB.manager.Base)
    used_count = InviteeDB.count(
        requester_id=env("ROOT_ID"),
        model_registry=model_registry,
        invitation_id=invitation.id,
        filters=[InviteeDB.accepted_at.isnot(None)],
    )
    if used_count >= invitation.max_uses:
        raise HTTPException(
            status_code=410, detail="Invitation has reached maximum usage limit"
        )


def _live(model: Any, model_registry: Any, not_found: str, **match: Any) -> Any:
    """The unrevoked ``model`` row matching ``match``, else 404. Read as
    root, which would otherwise see revoked rows: the caller's right to act
    on it is checked by the caller (a code in hand, a matching email)."""
    db_cls = model.DB(model_registry.DB.manager.Base)
    row = db_cls.get(
        requester_id=env("ROOT_ID"),
        model_registry=model_registry,
        filters=[db_cls.deleted_at.is_(None)],
        allow_nonexistent=True,
        return_type="dto",
        override_dto=model,
        **match,
    )
    if row is None:
        raise HTTPException(status_code=404, detail=not_found)
    return row


def _invitation_by_code(code: str, model_registry: Any) -> "InvitationModel":
    invitation: InvitationModel = _live(
        InvitationModel, model_registry, "Invalid invitation code", code=code
    )
    return invitation


def _grant_membership(
    *,
    user_id: str,
    team_id: str,
    role_id: str,
    model_registry: Any,
) -> str:
    """Put the user on the team with the invited role, re-enabling an
    existing membership; returns the membership id. Acts as root: issuing the
    invitation was the authorized grant (InvitationManager._assert_may_grant)."""
    user_team_manager = UserTeamManager(
        requester_id=env("ROOT_ID"), target_id=user_id, model_registry=model_registry
    )
    existing = user_team_manager.list(user_id=user_id, team_id=team_id)
    if existing:
        user_team_manager.update(id=existing[0].id, role_id=role_id, enabled=True)
        return str(existing[0].id)
    created = user_team_manager.create(
        user_id=user_id, team_id=team_id, role_id=role_id, enabled=True
    )
    return str(created.id)


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
        email: Optional[Union[str, List[str]]] = Field(
            None, description="Email address(es) of the invitee(s) (if known)"
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
    # Who an invitation went to and whether each accepted or declined:
    # GET /v1/invitation/{invitation_id}/invitee.
    nested_resources: ClassVar[Dict[str, Any]] = {
        "invitee": {
            "child_resource_name": "invitee",
            "manager_property": "invitees",
            "child_manager_class": lambda: InviteeManager,
            "routes_to_register": ["list"],
        },
    }

    def __init__(
        self,
        requester_id: str,
        target_id: Optional[str] = None,
        target_team_id: Optional[str] = None,
        model_registry: Optional[Any] = None,
        parent: Optional[Any] = None,
    ):
        super().__init__(
            requester_id=requester_id,
            target_id=target_id,
            target_team_id=target_team_id,
            model_registry=model_registry,
            parent=parent,
        )
        self._invitees = None

    @property
    def invitees(self):
        if self._invitees is None:
            self._invitees = InviteeManager(
                requester_id=self.requester.id,
                target_team_id=self.target_team_id,
                parent=self,
                model_registry=self.model_registry,
            )
        return self._invitees

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
        if entity.team_id and entity.role_id:
            self._assert_may_grant(entity.team_id, entity.role_id)

    def update(self, id: str, **kwargs: Any) -> Any:
        if kwargs.get("role_id"):
            self._assert_may_grant(self.get(id=id).team_id, kwargs["role_id"])
        return super().update(id, **kwargs)

    def _assert_may_grant(self, team_id: Optional[str], role_id: str) -> None:
        """A team invitation grants membership to whoever accepts it, so it
        is a grant of ``role_id`` in that team (see TeamAuthority)."""
        if team_id:
            TeamAuthority(
                self.requester.id, team_id, self.model_registry
            ).assert_may_grant(role_id)

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

            # Fetch every existing invitee across all matching invitations in
            # ONE query (was N+1: one list() per existing invitation) and hold
            # them in a set for O(1) membership (was an O(emails x invitees)
            # scan of a list). Membership is compared against the stored invitee
            # email exactly as before.
            existing_invitees: set[str] = set()
            if existing_invitations:
                InviteeDB = self.invitees.DB
                invitation_ids = [inv.id for inv in existing_invitations]
                existing_invitee_rows = self.invitees.list(
                    filters=[InviteeDB.invitation_id.in_(invitation_ids)],
                    deleted_at=None,
                    declined_at=None,
                )
                existing_invitees = {row.email for row in existing_invitee_rows}

            # Drop already-invited emails, keeping the rest. Build a NEW list
            # rather than mutating the one being iterated: the previous
            # ``for em in emails: ... emails.remove(em)`` both skipped entries
            # (mutation during iteration) and could raise ValueError, because it
            # removed the lowercased form that need not be present in ``emails``.
            emails = [
                normalized
                for normalized in (em.lower().strip() for em in emails)
                if normalized not in existing_invitees
            ]

            if not emails:
                raise HTTPException(status_code=400, detail="already invited")

            invitation = super().create(**kwargs)
            for em in emails:
                self._add_invitee(invitation, em)
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
            # The row the invited user answers with (PATCH takes an invitee
            # id or a code, and a direct invitation may have no code).
            self._add_invitee(invitation, user.email)
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
        return self._add_invitee(self.get(id=invitation_id), email)

    def _add_invitee(self, invitation: Any, email: str) -> Dict[str, Any]:
        """Address ``invitation`` to ``email`` and send it. Takes the
        invitation itself so ``create`` need not re-read what it just wrote
        (a creator may not be able to read a team's invitations)."""
        invitation_id = invitation.id
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

        invitee = self.invitees.create(
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
        include: Optional[Union[List[str], str]] = None,
        fields: Optional[Union[List[str], str]] = None,
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
                    result = self.invitees.decline_invitation(
                        patch_data.invitation_code
                    )
                    return {
                        "success": True,
                        "message": "Invitation declined successfully via code",
                        "team_id": result.get("team_id"),
                        "role_id": result.get("role_id"),
                    }
                else:
                    result = self.invitees.accept_invitation(
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
                # The matching email is what entitles the caller to answer:
                # the row may predate their account, so it is read as root.
                invitee = _live(
                    InviteeModel,
                    self.model_registry,
                    "Invitation not found",
                    id=patch_data.invitee_id,
                )
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
                invitation = _live(
                    InvitationModel,
                    self.model_registry,
                    "Invitation not found",
                    id=invitee.invitation_id,
                )
                _assert_unexpired(invitation)
                answer = InviteeManager(
                    requester_id=env("ROOT_ID"), model_registry=self.model_registry
                )

                if patch_data.action and patch_data.action.lower() == "decline":
                    answer.update(
                        id=invitee.id,
                        declined_at=datetime.now(timezone.utc),
                        user_id=user_id,
                    )
                    return {
                        "success": True,
                        "message": "Invitation declined successfully via invitee ID",
                        "team_id": invitation.team_id,
                        "role_id": invitation.role_id,
                    }

                answer.update(
                    id=invitee.id,
                    accepted_at=datetime.now(timezone.utc),
                    user_id=user_id,
                )

                user_team_id = None
                if invitation.team_id and invitation.role_id:
                    user_team_id = _grant_membership(
                        user_id=user_id,
                        team_id=invitation.team_id,
                        role_id=invitation.role_id,
                        model_registry=self.model_registry,
                    )

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
        return self.invitees.accept_invitation(code, user_id)  # type: ignore[no-any-return]

    def patch_invitation_endpoint(
        self, id: str, body: Dict[str, Any]
    ) -> Dict[str, Any]:
        patch_data = body.get("invitation")
        if not patch_data:
            raise HTTPException(status_code=400, detail="Missing invitation data")
        patch_model = InvitationModel.Patch(**patch_data)
        # The code or invitee must belong to the invitation in the path.
        if self._invitation_id_for(patch_model) != id:
            raise HTTPException(status_code=404, detail="Invitation not found")
        return self.patch_invitation_unified(patch_model, self.requester.id)

    def _invitation_id_for(self, patch_data: "InvitationModel.Patch") -> str:
        if patch_data.invitation_code:
            return str(
                _invitation_by_code(patch_data.invitation_code, self.model_registry).id
            )
        invitee = _live(
            InviteeModel,
            self.model_registry,
            "Invitation not found",
            id=patch_data.invitee_id,
        )
        return str(invitee.invitation_id)


class InviteeModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    UserModel.Reference.Optional,
    InvitationModel.Reference,
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
        BaseModel, InvitationModel.Reference.ID, UserModel.Reference.ID.Optional
    ):
        email: str = Field(..., description="Email of the invitee")
        declined_at: Optional[datetime] = Field(None)
        accepted_at: Optional[datetime] = Field(None)

    class Update(BaseModel, UserModel.Reference.ID.Optional):
        declined_at: Optional[datetime] = Field(None)
        accepted_at: Optional[datetime] = Field(None)

    class Search(
        ApplicationModel.Search,
        InvitationModel.Reference.ID.Search,
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
        invitation = _invitation_by_code(code, self.model_registry)
        _assert_redeemable(invitation, self.model_registry)

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
        invitation = _invitation_by_code(code, self.model_registry)
        _assert_unexpired(invitation)
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
        invitation = _invitation_by_code(code, self.model_registry)
        _assert_redeemable(invitation, self.model_registry)
        # Only team invitations carry a code (see InvitationManager.create).
        if not (invitation.team_id and invitation.role_id):
            raise HTTPException(
                status_code=409, detail="Invitation grants no team membership"
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

        user_team_id = _grant_membership(
            user_id=user_id,
            team_id=invitation.team_id,
            role_id=invitation.role_id,
            model_registry=self.model_registry,
        )
        return {
            "success": True,
            "team_id": invitation.team_id,
            "role_id": invitation.role_id,
            "user_team_id": user_team_id,
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
    if inv is None or _expired(inv.expires_at):
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
    if inv is None or _expired(inv.expires_at):
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
        _grant_membership(
            user_id=user_id,
            team_id=invitation["team_id"],
            role_id=invitation["role_id"],
            model_registry=model_registry,
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


def _pending_invitations_for_user(
    user_id: str, email: Optional[str], model_registry: Any
) -> List[Dict[str, Any]]:
    """Invitations awaiting this user's answer: unrevoked, unexpired, with a
    pending invitee row addressed to them (every addressed invitation has
    one, direct invitations included). A row counts whether it matches the
    user or only their address, so invitations sent before they registered
    are included. Each item carries its ``team``, ``role`` and the user's
    pending ``invitees`` rows. Reads run as root, which sees revoked rows,
    so they are excluded explicitly."""
    if not model_registry.is_model_bound(InvitationModel):
        return []
    Base = model_registry.DB.manager.Base
    InvitationDB = InvitationModel.DB(Base)
    InviteeDB = InviteeModel.DB(Base)
    root_id = env("ROOT_ID")

    def rows(db_cls: Any, *filters: Any, **kwargs: Any) -> List[Dict[str, Any]]:
        found: List[Dict[str, Any]] = db_cls.list(
            requester_id=root_id,
            model_registry=model_registry,
            filters=[db_cls.deleted_at.is_(None), *filters],
            **kwargs,
        )
        return found

    addressed = (
        or_(InviteeDB.user_id == user_id, InviteeDB.email == email.lower().strip())
        if email
        else InviteeDB.user_id == user_id
    )
    invitee_rows = rows(InviteeDB, addressed)
    answered = {
        row["invitation_id"]
        for row in invitee_rows
        if row["accepted_at"] or row["declined_at"]
    }
    pending_by_invitation: Dict[str, List[Dict[str, Any]]] = {}
    for row in invitee_rows:
        if not (row["accepted_at"] or row["declined_at"]):
            pending_by_invitation.setdefault(row["invitation_id"], []).append(
                {**row, "status": "pending"}
            )
    invitation_ids = set(pending_by_invitation) - answered
    if not invitation_ids:
        return []

    invitations = [
        invitation.model_dump(mode="json")
        for invitation in InvitationDB.list(
            requester_id=root_id,
            model_registry=model_registry,
            filters=[
                InvitationDB.deleted_at.is_(None),
                InvitationDB.id.in_(invitation_ids),
            ],
            return_type="dto",
            override_dto=InvitationModel,
        )
        if not _expired(invitation.expires_at)
    ]
    team_ids = {i["team_id"] for i in invitations if i["team_id"]}
    role_ids = {i["role_id"] for i in invitations if i["role_id"]}
    TeamDB = TeamModel.DB(Base)
    RoleDB = RoleModel.DB(Base)
    teams = (
        {t["id"]: t for t in rows(TeamDB, TeamDB.id.in_(team_ids))} if team_ids else {}
    )
    roles = (
        {r["id"]: r for r in rows(RoleDB, RoleDB.id.in_(role_ids))} if role_ids else {}
    )

    pending = []
    for invitation in invitations:
        item = {
            **invitation,
            "team": teams.get(invitation["team_id"]),
            "role": roles.get(invitation["role_id"]),
        }
        if invitation["id"] in pending_by_invitation:
            item["invitees"] = pending_by_invitation[invitation["id"]]
        pending.append(item)
    return pending


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
        pending_invitations_for_user=_pending_invitations_for_user,
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
