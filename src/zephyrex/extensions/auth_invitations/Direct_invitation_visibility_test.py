# SPDX-License-Identifier: AGPL-3.0-or-later
"""A direct invitation names only a user the inviter can see, on a real
database.

The hole this closes: an invitation by ``user_id`` resolved the invitee as
ROOT, so a team admin who knew any account's id could invite it, and the
invitee row written for it (``GET /v1/invitation/{id}/invitee``) showed
them that user's email. The invitee is now read as the inviter: someone
they share no live team hierarchy with (and hold no grant on) is a 404, as
a missing user is, and nothing is created. ROOT and SYSTEM see everyone.
An invitation by email names no account and is unchanged."""

import uuid
from typing import Any, List

import pytest
from fastapi import HTTPException

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.auth_invitations.BLL_Invitations import (
    InvitationManager,
    InviteeManager,
)
from zephyrex.extensions.auth_invitations.EXT_Invitations import (
    AuthInvitationsExtension,
)
from zephyrex.lib.Environment import env
from zephyrex.testing.factories import add_user_to_team, create_team, create_user


class TestDirectInvitationVisibility(ExtensionServerMixin):
    extension_class = AuthInvitationsExtension

    def _team(self, server: Any, owner: Any) -> Any:
        return create_team(server, owner.id, name=f"invite {uuid.uuid4().hex[:8]}")

    def _invite(
        self, model_registry: Any, inviter_id: str, team: Any, **kw: Any
    ) -> Any:
        return InvitationManager(
            requester_id=inviter_id, model_registry=model_registry
        ).create(team_id=team.id, role_id=env("USER_ROLE_ID"), **kw)

    def _invitations_to(self, model_registry: Any, team: Any) -> List[Any]:
        return InvitationManager(
            requester_id=env("ROOT_ID"), model_registry=model_registry
        ).list(team_id=team.id)

    def _invitees_for(self, model_registry: Any, user_id: str) -> List[Any]:
        return InviteeManager(
            requester_id=env("ROOT_ID"), model_registry=model_registry
        ).list(user_id=user_id)

    def test_a_direct_invite_to_an_invisible_user_is_404_and_creates_nothing(
        self, server, model_registry
    ):
        inviter, stranger = create_user(server), create_user(server)
        team = self._team(server, inviter)
        self._team(server, stranger)

        with pytest.raises(HTTPException) as refused:
            self._invite(model_registry, inviter.id, team, user_id=stranger.id)

        assert refused.value.status_code == 404
        assert self._invitations_to(model_registry, team) == []
        assert self._invitees_for(model_registry, stranger.id) == []

    def test_an_invisible_user_and_a_missing_one_answer_alike(
        self, server, model_registry
    ):
        inviter, stranger = create_user(server), create_user(server)
        team = self._team(server, inviter)

        answers = []
        for user_id in (stranger.id, str(uuid.uuid4())):
            with pytest.raises(HTTPException) as refused:
                self._invite(model_registry, inviter.id, team, user_id=user_id)
            answers.append(refused.value.status_code)

        assert answers == [404, 404]

    def test_the_rest_route_answers_404_without_the_invitees_email(
        self, server, model_registry
    ):
        inviter, stranger = create_user(server), create_user(server)
        team = self._team(server, inviter)

        response = server.post(
            f"/v1/team/{team.id}/invitation",
            json={
                "invitation": {
                    "team_id": team.id,
                    "role_id": env("USER_ROLE_ID"),
                    "user_id": stranger.id,
                }
            },
            headers={"Authorization": f"Bearer {inviter.jwt}"},
        )

        assert response.status_code == 404, response.text
        assert stranger.email not in response.text
        assert self._invitations_to(model_registry, team) == []

    def test_a_batch_of_direct_invites_is_held_to_the_same_rule(
        self, server, model_registry
    ):
        inviter, stranger = create_user(server), create_user(server)
        team = self._team(server, inviter)

        with pytest.raises(HTTPException) as refused:
            InvitationManager(
                requester_id=inviter.id, model_registry=model_registry
            ).create(
                entities=[
                    {
                        "team_id": team.id,
                        "role_id": env("USER_ROLE_ID"),
                        "user_id": stranger.id,
                    }
                ]
            )

        assert refused.value.status_code == 404
        assert self._invitations_to(model_registry, team) == []

    def test_an_app_level_direct_invite_to_an_invisible_user_is_404(
        self, server, model_registry
    ):
        inviter, stranger = create_user(server), create_user(server)

        with pytest.raises(HTTPException) as refused:
            InvitationManager(
                requester_id=inviter.id, model_registry=model_registry
            ).create(user_id=stranger.id, max_uses=1)

        assert refused.value.status_code == 404
        assert self._invitees_for(model_registry, stranger.id) == []

    def test_a_direct_invite_to_a_visible_user_works(self, server, model_registry):
        """Visible through another team the inviter shares with them."""
        inviter, colleague = create_user(server), create_user(server)
        team = self._team(server, inviter)
        add_user_to_team(
            server, colleague.id, self._team(server, inviter).id, env("USER_ROLE_ID")
        )

        invitation = self._invite(
            model_registry, inviter.id, team, user_id=colleague.id
        )

        assert invitation.user_id == colleague.id
        assert invitation.user.id == colleague.id
        (invitee,) = self._invitees_for(model_registry, colleague.id)
        assert (invitee.invitation_id, invitee.email) == (
            invitation.id,
            colleague.email,
        )

    def test_root_invites_anyone_directly(self, server, model_registry):
        owner, stranger = create_user(server), create_user(server)
        team = self._team(server, owner)

        invitation = self._invite(
            model_registry, env("ROOT_ID"), team, user_id=stranger.id
        )

        assert invitation.user_id == stranger.id
        assert [
            i.invitation_id for i in self._invitees_for(model_registry, stranger.id)
        ] == [invitation.id]

    def test_an_email_invite_reaches_anyone(self, server, model_registry):
        """An address names no account the inviter learns of: unchanged."""
        inviter, stranger = create_user(server), create_user(server)
        team = self._team(server, inviter)

        invitation = self._invite(
            model_registry, inviter.id, team, email=stranger.email
        )

        invitees = InviteeManager(
            requester_id=env("ROOT_ID"), model_registry=model_registry
        ).list(invitation_id=invitation.id)
        assert [i.email for i in invitees] == [stranger.email.lower()]
