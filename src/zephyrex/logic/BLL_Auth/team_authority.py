# SPDX-License-Identifier: AGPL-3.0-or-later
"""Who may shape a team's membership.

Roles form a tree by ``parent_id`` (user <- admin <- superadmin); a role's
rank is its depth. A team member administers the team when their role is the
admin role or extends it (the admin subtree); they may grant a role, or act on
a member, only up to their own rank. Root and system act without limit.
Invitations, role changes and member removal all ask here, so one rule
governs every way into (or up within, or out of) a team.
"""

from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from zephyrex.lib.DateTimeUtils import ensure_utc
from zephyrex.lib.Environment import env

_MAX_ROLE_DEPTH = 32


def _role_chain(
    role_id: str, model_registry: Any, cache: Dict[str, List[str]]
) -> List[str]:
    """``role_id`` and its ancestors, nearest first; 404 for an unknown role."""
    from zephyrex.logic.BLL_Auth.role import RoleModel

    if role_id not in cache:
        RoleDB = RoleModel.DB(model_registry.DB.manager.Base)
        chain: List[str] = []
        current: Optional[str] = role_id
        while current and current not in chain and len(chain) < _MAX_ROLE_DEPTH:
            chain.append(current)
            role = RoleDB.get(
                requester_id=env("ROOT_ID"),
                model_registry=model_registry,
                id=current,
                allow_nonexistent=True,
            )
            if role is None:
                raise HTTPException(status_code=404, detail="Role not found")
            current = role["parent_id"]
        cache[role_id] = chain
    return cache[role_id]


class TeamAuthority:
    """The requester's authority over one team's membership."""

    def __init__(self, requester_id: str, team_id: str, model_registry: Any) -> None:
        from zephyrex.database.StaticPermissions import is_root_id, is_system_user_id

        self._model_registry = model_registry
        self._chains: Dict[str, List[str]] = {}
        self.unlimited = is_root_id(requester_id) or is_system_user_id(requester_id)
        self.role_id = (
            None
            if self.unlimited
            else live_membership_role(requester_id, team_id, model_registry)
        )

    def rank(self, role_id: str) -> int:
        return len(_role_chain(role_id, self._model_registry, self._chains))

    def is_admin_role(self, role_id: str) -> bool:
        """``role_id`` is the admin role or extends it."""
        return env("ADMIN_ROLE_ID") in _role_chain(
            role_id, self._model_registry, self._chains
        )

    def administers(self) -> bool:
        return self.unlimited or (
            self.role_id is not None and self.is_admin_role(self.role_id)
        )

    def _own_rank(self) -> Optional[int]:
        """The requester's rank, or None for root/system (no ceiling);
        403 unless they administer the team."""
        if self.unlimited:
            return None
        if self.role_id is None or not self.administers():
            raise HTTPException(
                status_code=403, detail="Only a team admin can manage this team"
            )
        return self.rank(self.role_id)

    def assert_may_grant(self, role_id: str) -> None:
        """May hand out ``role_id`` (by invitation or role change)."""
        ceiling = self._own_rank()
        if ceiling is not None and self.rank(role_id) > ceiling:
            raise HTTPException(
                status_code=403, detail="Cannot grant a role above your own"
            )

    def assert_may_act_on(self, member_role_id: str) -> None:
        """May change or remove a member who holds ``member_role_id``."""
        ceiling = self._own_rank()
        if ceiling is not None and self.rank(member_role_id) > ceiling:
            raise HTTPException(
                status_code=403, detail="Cannot manage a member above your own role"
            )


def live_membership_role(
    user_id: str, team_id: str, model_registry: Any
) -> Optional[str]:
    """The role the user holds in the team through an enabled, unrevoked,
    unexpired membership; None when there is no such membership."""
    from datetime import datetime, timezone

    from zephyrex.logic.BLL_Auth.user_team import UserTeamModel

    UserTeamDB = UserTeamModel.DB(model_registry.DB.manager.Base)
    for membership in UserTeamDB.list(
        requester_id=env("ROOT_ID"),
        model_registry=model_registry,
        user_id=user_id,
        team_id=team_id,
        enabled=True,
        filters=[UserTeamDB.deleted_at.is_(None)],
        return_type="dto",
        override_dto=UserTeamModel,
    ):
        expires_at = membership.expires_at
        if expires_at is None or ensure_utc(expires_at) > datetime.now(timezone.utc):
            return str(membership.role_id)
    return None
