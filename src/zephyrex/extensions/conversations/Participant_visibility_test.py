# SPDX-License-Identifier: AGPL-3.0-or-later
"""A conversation names as a participant only a user the requester can see.

The hole this closes: every path that seats a user in a conversation (the
participants route, the ``add_participant`` ability, ``POST
/v1/conversation/user`` singly or in a batch, and a direct message)
checked the user as SYSTEM, which sees every account, and then granted as
ROOT, so anyone who knew a user's id could add them to a conversation or
open a direct message with them. The user is now read as the requester,
under the users rule (a shared live team hierarchy, either direction): a
stranger is a 404, exactly as a missing user is, and nothing is written.
ROOT and SYSTEM see everyone, which is how the server seats participants
on its own account."""

import uuid
from typing import Any, Dict, List

import pytest
from fastapi import HTTPException

from zephyrex.extensions.acl_rbac.BLL_ACL import PermissionManager
from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.conversations.BLL_Conversations import (
    ConversationManager,
    ConversationUserManager,
    MessageManager,
)
from zephyrex.extensions.conversations.EXT_Conversations import EXT_Conversations
from zephyrex.lib.Environment import env
from zephyrex.testing.factories import add_user_to_team, create_team, create_user


def auth(user: Any) -> Dict[str, str]:
    return {"Authorization": f"Bearer {user.jwt}"}


def _unique(label: str) -> str:
    return f"{label} {uuid.uuid4().hex[:8]}"


class TestParticipantVisibility(ExtensionServerMixin):
    extension_class = EXT_Conversations

    def _user_in_own_team(self, server: Any, label: str) -> Any:
        """A user whose only team is their own, so they see no one else."""
        user = create_user(server)
        create_team(server, user.id, name=_unique(label))
        return user

    def _teammates(self, server: Any) -> List[Any]:
        owner, teammate = create_user(server), create_user(server)
        team = create_team(server, owner.id, name=_unique("shared"))
        add_user_to_team(server, teammate.id, team.id, env("USER_ROLE_ID"))
        return [owner, teammate]

    def _conversation(self, server: Any, owner: Any) -> Dict[str, Any]:
        response = server.post(
            "/v1/conversation",
            json={"conversation": {"name": "Plans", "is_group_chat": True}},
            headers=auth(owner),
        )
        assert response.status_code == 201, response.text
        created: Dict[str, Any] = response.json()["conversation"]
        return created

    def _add(self, server: Any, adder: Any, conversation_id: str, user_id: str) -> Any:
        return server.post(
            f"/v1/conversation/{conversation_id}/participants",
            json={"user_id": user_id},
            headers=auth(adder),
        )

    def _direct(self, server: Any, opener: Any, other_user_id: str) -> Any:
        return server.post(
            "/v1/conversation/direct",
            json={"other_user_id": other_user_id, "initial_message": "hello"},
            headers=auth(opener),
        )

    def _seats(
        self, model_registry: Any, conversation_id: str, user_id: str
    ) -> List[Any]:
        """The user's memberships of and grants on the conversation."""
        memberships = ConversationUserManager(
            requester_id=env("ROOT_ID"), model_registry=model_registry
        ).list(conversation_id=conversation_id, user_id=user_id)
        grants = PermissionManager(
            requester_id=env("ROOT_ID"), model_registry=model_registry
        ).list(
            resource_type="conversations", resource_id=conversation_id, user_id=user_id
        )
        return [*memberships, *grants]

    def _owned(self, model_registry: Any, owner: Any) -> List[str]:
        return [
            c.id
            for c in ConversationManager(
                requester_id=env("ROOT_ID"), model_registry=model_registry
            ).list(user_id=owner.id)
        ]

    def test_adding_an_invisible_user_is_404_and_writes_nothing(
        self, server, model_registry
    ):
        owner = self._user_in_own_team(server, "owner")
        stranger = self._user_in_own_team(server, "stranger")
        conversation = self._conversation(server, owner)

        refused = self._add(server, owner, conversation["id"], stranger.id)

        assert refused.status_code == 404, refused.text
        assert self._seats(model_registry, conversation["id"], stranger.id) == []

    def test_an_invisible_user_and_a_missing_one_answer_alike(
        self, server, model_registry
    ):
        owner = self._user_in_own_team(server, "owner")
        stranger = self._user_in_own_team(server, "stranger")
        conversation = self._conversation(server, owner)

        answers = [
            (response.status_code, response.json())
            for response in (
                self._add(server, owner, conversation["id"], user_id)
                for user_id in (stranger.id, str(uuid.uuid4()))
            )
        ]

        assert answers[0] == answers[1]
        assert answers[0][0] == 404

    def test_the_ability_refuses_an_invisible_user(self, server, model_registry):
        owner = self._user_in_own_team(server, "owner")
        stranger = self._user_in_own_team(server, "stranger")
        conversation = self._conversation(server, owner)

        with pytest.raises(HTTPException) as refused:
            ConversationManager(
                requester_id=owner.id, model_registry=model_registry
            ).add_participant(conversation["id"], stranger.id)

        assert refused.value.status_code == 404
        assert self._seats(model_registry, conversation["id"], stranger.id) == []

    def test_a_membership_naming_an_invisible_user_is_404(self, server, model_registry):
        owner = self._user_in_own_team(server, "owner")
        stranger = self._user_in_own_team(server, "stranger")
        conversation = self._conversation(server, owner)

        refused = server.post(
            "/v1/conversation/user",
            json={
                "conversation_user": {
                    "conversation_id": conversation["id"],
                    "user_id": stranger.id,
                }
            },
            headers=auth(owner),
        )

        assert refused.status_code == 404, refused.text
        assert self._seats(model_registry, conversation["id"], stranger.id) == []

    def test_a_batch_with_an_invisible_user_writes_no_one(self, server, model_registry):
        """Every user in a batch is checked before any membership is
        written, so the visible teammate listed first is not seated either."""
        owner, teammate = self._teammates(server)
        stranger = self._user_in_own_team(server, "stranger")
        conversation = self._conversation(server, owner)

        with pytest.raises(HTTPException) as refused:
            ConversationUserManager(
                requester_id=owner.id, model_registry=model_registry
            ).create(
                entities=[
                    {"conversation_id": conversation["id"], "user_id": teammate.id},
                    {"conversation_id": conversation["id"], "user_id": stranger.id},
                ]
            )

        assert refused.value.status_code == 404
        for user in (teammate, stranger):
            assert self._seats(model_registry, conversation["id"], user.id) == []

    def test_a_direct_message_to_an_invisible_user_is_404_and_writes_nothing(
        self, server, model_registry
    ):
        opener = self._user_in_own_team(server, "opener")
        stranger = self._user_in_own_team(server, "stranger")

        refused = self._direct(server, opener, stranger.id)

        assert refused.status_code == 404, refused.text
        assert self._owned(model_registry, opener) == []
        assert (
            MessageManager(
                requester_id=env("ROOT_ID"), model_registry=model_registry
            ).list(user_id=opener.id)
            == []
        )

    def test_a_visible_teammate_is_added(self, server, model_registry):
        owner, teammate = self._teammates(server)
        conversation = self._conversation(server, owner)

        added = self._add(server, owner, conversation["id"], teammate.id)

        assert added.status_code == 200, added.text
        assert added.json()["user_id"] == teammate.id
        read = server.get(
            f"/v1/conversation/{conversation['id']}", headers=auth(teammate)
        )
        assert read.status_code == 200, read.text

    def test_a_direct_message_to_a_visible_teammate_opens(self, server, model_registry):
        opener, teammate = self._teammates(server)

        opened = self._direct(server, opener, teammate.id)

        assert opened.status_code == 200, opened.text
        conversation_id = opened.json()["conversation"]["id"]
        replied = server.post(
            "/v1/message",
            json={"message": {"conversation_id": conversation_id, "content": "hi"}},
            headers=auth(teammate),
        )
        assert replied.status_code == 201, replied.text

    def test_a_sub_team_member_is_visible(self, server, model_registry):
        """The users rule reaches down a team hierarchy, so a parent team's
        member may seat a sub-team's member."""
        owner, below = create_user(server), create_user(server)
        parent = create_team(server, owner.id, name=_unique("parent"))
        create_team(server, below.id, name=_unique("sub"), parent_id=parent.id)
        conversation = self._conversation(server, owner)

        added = self._add(server, owner, conversation["id"], below.id)

        assert added.status_code == 200, added.text

    def test_root_adds_anyone(self, server, model_registry):
        owner = self._user_in_own_team(server, "owner")
        stranger = self._user_in_own_team(server, "stranger")
        conversation = self._conversation(server, owner)

        added = ConversationManager(
            requester_id=env("ROOT_ID"), model_registry=model_registry
        ).add_participant(conversation["id"], stranger.id)

        assert added.user_id == stranger.id
        read = server.get(
            f"/v1/conversation/{conversation['id']}", headers=auth(stranger)
        )
        assert read.status_code == 200, read.text

    def test_the_server_seats_participants_on_its_own_account(
        self, server, model_registry
    ):
        """SYSTEM opening a conversation for a user (as a notification or
        an integration does) seats that user and anyone else it names,
        though they share no team."""
        owner = self._user_in_own_team(server, "owner")
        stranger = self._user_in_own_team(server, "stranger")
        system = ConversationManager(
            requester_id=env("SYSTEM_ID"), model_registry=model_registry
        )

        conversation = system.create(
            name="Notice", is_group_chat=True, user_id=owner.id
        )
        system.add_participant(conversation.id, stranger.id)

        for user in (owner, stranger):
            read = server.get(f"/v1/conversation/{conversation.id}", headers=auth(user))
            assert read.status_code == 200, read.text
