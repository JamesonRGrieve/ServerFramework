"""auth_invitations extension definition.

Wires every place core BLL_Auth used to reach into Invitation/Invitee
through hook callables so the extension can be enabled/disabled via
APP_EXTENSIONS without modifying core.
"""

from datetime import datetime, timezone
from typing import Any, ClassVar, Dict, List, Optional, Type

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
)


def _lookup_by_id(invitation_id: str, model_registry) -> Optional[Dict[str, Any]]:
    from zephyrex.extensions.auth_invitations.BLL_Invitations import (
        InvitationModel,
    )
    from zephyrex.lib.Environment import env

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


def _lookup_by_code(invitation_code: str, model_registry) -> Optional[Dict[str, Any]]:
    from zephyrex.extensions.auth_invitations.BLL_Invitations import (
        InvitationModel,
    )

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


def _apply_to_user(invitation: Dict[str, Any], user_id: str, model_registry) -> None:
    """Bind a freshly-registered user to the invitation: invitee row +
    user-team membership. Idempotent — existing membership is updated.
    """
    from zephyrex.extensions.auth_invitations.BLL_Invitations import (
        InviteeManager,
    )
    from zephyrex.lib.Environment import env
    from zephyrex.logic.BLL_Auth import UserManager, UserTeamManager

    user_manager = UserManager(
        requester_id=env("ROOT_ID"), model_registry=model_registry
    )
    user = user_manager.get(id=user_id)
    user_email = user.email.lower() if hasattr(user, "email") else None

    invitee_manager = InviteeManager(
        requester_id=env("ROOT_ID"),
        model_registry=model_registry,
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
            user_id=user_id,
            team_id=invitation["team_id"],
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


def _invitation_manager_factory(
    requester_id: str,
    target_team_id: Optional[str],
    model_registry: Any,
    **kw,
):
    from zephyrex.extensions.auth_invitations.BLL_Invitations import (
        InvitationManager,
    )

    return InvitationManager(
        requester_id=requester_id,
        target_team_id=target_team_id,
        model_registry=model_registry,
        **kw,
    )


def _invitee_manager_factory(
    requester_id: str,
    target_id: Optional[str],
    model_registry: Any,
    **kw,
):
    from zephyrex.extensions.auth_invitations.BLL_Invitations import (
        InviteeManager,
    )

    return InviteeManager(
        requester_id=requester_id,
        target_id=target_id,
        model_registry=model_registry,
        **kw,
    )


def _list_invitees_for_user(
    user_id: str, email: Optional[str], model_registry
) -> List[Dict[str, Any]]:
    from zephyrex.extensions.auth_invitations.BLL_Invitations import (
        InviteeModel,
    )
    from zephyrex.lib.Environment import env

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


class AuthInvitationsExtension(AbstractStaticExtension):
    name: ClassVar[str] = "auth_invitations"
    description: ClassVar[str] = (
        "Team invitation workflow with role assignment (Scope #4)"
    )
    extension_dependencies: ClassVar[List[str]] = []

    @classmethod
    def models(cls) -> List[Type]:
        from zephyrex.extensions.auth_invitations.BLL_Invitations import (
            InvitationModel,
            InviteeModel,
        )

        return [InvitationModel, InviteeModel]

    @classmethod
    def on_load(cls) -> None:
        from zephyrex.logic.BLL_Auth import register_invitation_hooks

        register_invitation_hooks(
            lookup_by_id=_lookup_by_id,
            lookup_by_code=_lookup_by_code,
            apply_to_user=_apply_to_user,
            invitation_manager_factory=_invitation_manager_factory,
            invitee_manager_factory=_invitee_manager_factory,
            list_invitees_for_user=_list_invitees_for_user,
        )
