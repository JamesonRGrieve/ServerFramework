"""auth_invitations extension definition.

Wires invitation lookup + apply hooks into `BLL_Auth.UserManager.register` so
the inline invitation-acceptance code that used to live in core can be
disabled by simply omitting this extension from APP_EXTENSIONS.
"""

from datetime import datetime, timezone
from typing import Any, ClassVar, List, Optional, Type

from serverframework.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
)


def _lookup_by_id(invitation_id: str, model_registry) -> Optional[dict]:
    from serverframework.lib.Environment import env
    from serverframework.logic.BLL_Auth import InvitationModel

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


def _lookup_by_code(invitation_code: str, model_registry) -> Optional[dict]:
    from serverframework.logic.BLL_Auth import InvitationModel

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


def _apply_to_user(invitation: dict, user_id: str, model_registry) -> None:
    """Bind a freshly-registered user to the invitation's team+role."""
    from serverframework.lib.Environment import env
    from serverframework.logic.BLL_Auth import UserTeamModel

    if not invitation or not invitation.get("team_id"):
        return
    UserTeamModel.DB(model_registry.DB.manager.Base).create(
        requester_id=env("ROOT_ID"),
        model_registry=model_registry,
        user_id=user_id,
        team_id=invitation["team_id"],
        role_id=invitation.get("role_id"),
        enabled=True,
    )


class AuthInvitationsExtension(AbstractStaticExtension):
    name: ClassVar[str] = "auth_invitations"
    description: ClassVar[str] = (
        "Team invitation workflow with role assignment (Scope #4)"
    )
    extension_dependencies: ClassVar[List[str]] = []

    @classmethod
    def models(cls) -> List[Type]:
        from serverframework.logic.BLL_Auth import InvitationModel, InviteeModel

        return [InvitationModel, InviteeModel]

    @classmethod
    def on_load(cls) -> None:
        from serverframework.logic.BLL_Auth import register_invitation_hooks

        register_invitation_hooks(
            lookup_by_id=_lookup_by_id,
            lookup_by_code=_lookup_by_code,
            apply_to_user=_apply_to_user,
        )
