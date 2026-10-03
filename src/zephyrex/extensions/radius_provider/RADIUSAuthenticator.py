# SPDX-License-Identifier: AGPL-3.0-or-later
"""Deciding a PAP Access-Request against framework accounts.

The password is checked by the framework (``UserManager.verify_password``)
and failures feed the same lockouts a web login does:

* the always-on in-process tracker, keyed by NAS and user name (keying by
  the NAS's address alone would let one guesser lock every user behind an
  access point out);
* the durable per-user threshold of ``auth_lockout``, when it is loaded.

A user with a second factor is refused: PAP carries one password and nothing
else, and accepting it would bypass the factor.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, List, Optional, Tuple

import bcrypt
from fastapi import HTTPException
from sqlalchemy import or_

from zephyrex.extensions.radius_provider.BLL_RADIUSProvider import (
    RadiusClientModel,
    RadiusTeamPolicyManager,
    RadiusTeamPolicyModel,
    root_manager,
)
from zephyrex.extensions.radius_provider.RADIUSPackets import ReplyValue
from zephyrex.lib.DateTimeUtils import ensure_utc
from zephyrex.lib.Environment import env
from zephyrex.lib.InboundSecurity import LockoutPolicy, LockoutTracker
from zephyrex.lib.Logging import logger
from zephyrex.logic.BLL_Auth import (
    UserManager,
    UserModel,
    UserTeamModel,
    _lockout_hooks,
)
from zephyrex.logic.BLL_Auth._shared import _DUMMY_BCRYPT_HASH

LOCKOUT_FLOW = "radius_pap"
# The web login's IP-keyed policy: 10 failures in 15 minutes lock for 30.
LOCKOUT_POLICY = LockoutPolicy(
    failures_per_window=10, window_seconds=900, lockout_seconds=1800
)
# RFC 3580 §3.31 tunnel attributes for a VLAN assignment.
TUNNEL_TYPE_VLAN = "VLAN"
TUNNEL_MEDIUM_IEEE_802 = "IEEE-802"


@dataclass(frozen=True)
class Decision:
    accepted: bool
    attributes: Tuple[Tuple[str, ReplyValue], ...] = ()


REJECT = Decision(accepted=False)


def policy_attributes(
    policy: Optional[RadiusTeamPolicyModel],
) -> Tuple[Tuple[str, ReplyValue], ...]:
    if policy is None:
        return ()
    attributes: List[Tuple[str, ReplyValue]] = []
    if policy.vlan_id is not None:
        attributes += [
            ("Tunnel-Type", TUNNEL_TYPE_VLAN),
            ("Tunnel-Medium-Type", TUNNEL_MEDIUM_IEEE_802),
            ("Tunnel-Private-Group-Id", str(policy.vlan_id)),
        ]
    if policy.filter_id:
        attributes.append(("Filter-Id", policy.filter_id))
    if policy.session_timeout_seconds is not None:
        attributes.append(("Session-Timeout", policy.session_timeout_seconds))
    return tuple(attributes)


class PAPAuthenticator:
    """Decides Access-Requests carrying a User-Name and User-Password."""

    def __init__(self, model_registry: Any) -> None:
        self.model_registry = model_registry
        self.lockouts = LockoutTracker(LOCKOUT_POLICY)

    @property
    def _base(self) -> Any:
        return self.model_registry.DB.manager.Base

    def decide(
        self, client: RadiusClientModel, user_name: str, password: str, source: str
    ) -> Decision:
        identifier = UserManager._normalize_identifier(user_name)
        actor = f"{client.id}:{identifier}"
        if self.lockouts.is_locked(actor, LOCKOUT_FLOW):
            logger.info("radius_provider: %s is locked out", actor)
            return REJECT

        user = self._user(identifier)
        if user is None or user.id is None:
            # Spend a password check's time, as the web login does, so an
            # unknown name is not told apart from a wrong password.
            bcrypt.checkpw(password.encode(), _DUMMY_BCRYPT_HASH)
            self.lockouts.record_failure(actor, LOCKOUT_FLOW)
            return REJECT
        user_id: str = user.id

        threshold = _lockout_hooks["assert_within_threshold"]
        if threshold is not None:
            try:
                threshold(user_id, self.model_registry)
            except HTTPException:
                logger.info("radius_provider: user %s is locked out", user_id)
                return REJECT

        if not user.active:
            self._record_failure(user_id, actor, source)
            return REJECT

        verifier = UserManager(model_registry=self.model_registry, requester_id=user_id)
        if not verifier.verify_password(user_id, password):
            self._record_failure(user_id, actor, source)
            return REJECT
        self.lockouts.clear(actor, LOCKOUT_FLOW)

        if UserManager.mfa_challenge(user_id, self.model_registry) is not None:
            logger.info("radius_provider: user %s has a second factor", user_id)
            return REJECT

        teams = self._active_team_ids(user_id)
        if client.required_team_id and client.required_team_id not in teams:
            return REJECT

        policies: RadiusTeamPolicyManager = root_manager(
            RadiusTeamPolicyManager, self.model_registry
        )
        return Decision(
            accepted=True, attributes=policy_attributes(policies.for_teams(teams))
        )

    def _user(self, identifier: str) -> Optional[UserModel]:
        """The one undeleted account with this email or username."""
        User = UserModel.DB(self._base)
        users: List[UserModel] = User.list(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            filters=[
                or_(User.email == identifier, User.username == identifier),
                User.deleted_at.is_(None),
            ],
            return_type="dto",
            override_dto=UserModel,
        )
        return users[0] if len(users) == 1 else None

    def _record_failure(self, user_id: str, actor: str, source: str) -> None:
        self.lockouts.record_failure(actor, LOCKOUT_FLOW)
        record = _lockout_hooks["record_failure"]
        if record is not None:
            record(user_id, source, self.model_registry)

    def _active_team_ids(self, user_id: str) -> List[str]:
        """Teams the user is an enabled, unexpired member of."""
        now = datetime.now(timezone.utc)
        UserTeam = UserTeamModel.DB(self._base)
        memberships: List[UserTeamModel] = UserTeam.list(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            user_id=user_id,
            enabled=True,
            filters=[UserTeam.deleted_at.is_(None)],
            return_type="dto",
            override_dto=UserTeamModel,
        )
        return [
            m.team_id
            for m in memberships
            if m.expires_at is None or ensure_utc(m.expires_at) > now
        ]
