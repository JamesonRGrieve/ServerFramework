# SPDX-License-Identifier: AGPL-3.0-or-later
"""Who may do what in a conversation, over the API and the abilities.

Holes these close: any user could post into any conversation whose id
they knew; a participant could not read the others' messages; a caller
could name another user as a message's, feedback's or artifact's author
(singly or in a batch); anyone could add anyone to any conversation; and
the participant and direct-message routes ran without a database
registry."""

import base64
from typing import Any, Dict

import pytest
from fastapi import HTTPException

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.conversations.BLL_Conversations import (
    MAX_THREAD_DEPTH,
    MessageManager,
)
from zephyrex.extensions.conversations.EXT_Conversations import EXT_Conversations
from zephyrex.extensions.ExternalErrors import InvalidInputExternalError
from zephyrex.pydantic2.registry import ModelRegistry


def auth(user) -> Dict[str, str]:
    return {"Authorization": f"Bearer {user.jwt}"}


class TestConversationAccess(ExtensionServerMixin):
    extension_class = EXT_Conversations

    def _conversation(self, server, owner, name="Plans") -> Dict[str, Any]:
        response = server.post(
            "/v1/conversation",
            json={"conversation": {"name": name, "is_group_chat": True}},
            headers=auth(owner),
        )
        assert response.status_code == 201, response.text
        created: Dict[str, Any] = response.json()["conversation"]
        return created

    def _post(self, server, user, conversation_id, content="hello", **extra):
        return server.post(
            "/v1/message",
            json={
                "message": {
                    "conversation_id": conversation_id,
                    "content": content,
                    **extra,
                }
            },
            headers=auth(user),
        )

    def _add(self, server, user, conversation_id, user_id):
        return server.post(
            f"/v1/conversation/{conversation_id}/participants",
            json={"user_id": user_id},
            headers=auth(user),
        )

    def _messages(self, server, user, conversation_id):
        response = server.get(
            "/v1/message",
            params={"conversation_id": conversation_id},
            headers=auth(user),
        )
        assert response.status_code == 200, response.text
        return response.json()["messages"]

    @pytest.fixture
    def shared(self, server, admin_a, user_b) -> Dict[str, Any]:
        """admin_a's conversation, with user_b added."""
        conversation = self._conversation(server, admin_a)
        added = self._add(server, admin_a, conversation["id"], user_b.id)
        assert added.status_code == 200, added.text
        return conversation

    def test_an_outsider_cannot_post_or_read(self, server, admin_a, admin_b, shared):
        assert self._post(server, admin_a, shared["id"]).status_code == 201
        refused = self._post(server, admin_b, shared["id"])
        assert refused.status_code in (403, 404), refused.text
        assert self._messages(server, admin_b, shared["id"]) == []

    def test_participants_read_each_other(self, server, admin_a, user_b, shared):
        assert self._post(server, admin_a, shared["id"], "from a").status_code == 201
        assert self._post(server, user_b, shared["id"], "from b").status_code == 201
        for reader in (admin_a, user_b):
            contents = {
                m["content"] for m in self._messages(server, reader, shared["id"])
            }
            assert {"from a", "from b"} <= contents

    def test_a_participant_sees_the_conversation(self, server, user_b, shared):
        response = server.get(f"/v1/conversation/{shared['id']}", headers=auth(user_b))
        assert response.status_code == 200, response.text

    def test_the_author_is_the_poster(self, server, admin_a, user_b, shared):
        response = self._post(server, user_b, shared["id"], user_id=admin_a.id)
        assert response.status_code == 201, response.text
        assert response.json()["message"]["user_id"] == user_b.id
        agentless = self._post(server, user_b, shared["id"], user_id=None)
        assert agentless.json()["message"]["user_id"] == user_b.id

    def test_a_batch_cannot_name_another_author(self, server, admin_a, user_b, shared):
        response = server.post(
            "/v1/message",
            json={
                "messages": [
                    {
                        "conversation_id": shared["id"],
                        "content": "one",
                        "user_id": admin_a.id,
                    },
                    {
                        "conversation_id": shared["id"],
                        "content": "two",
                        "user_id": None,
                    },
                ]
            },
            headers=auth(user_b),
        )
        assert response.status_code == 201, response.text
        authors = {m["user_id"] for m in response.json()["messages"]}
        assert authors == {user_b.id}

    def test_only_the_author_edits(self, server, admin_a, user_b, shared):
        message = self._post(server, admin_a, shared["id"]).json()["message"]
        refused = server.put(
            f"/v1/message/{message['id']}",
            json={"message": {"content": "rewritten"}},
            headers=auth(user_b),
        )
        assert refused.status_code == 403, refused.text
        edited = server.put(
            f"/v1/message/{message['id']}",
            json={"message": {"content": "fixed typo"}},
            headers=auth(admin_a),
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()["message"]["edited_at"]

    def test_the_owner_deletes_anyones_message(self, server, admin_a, user_b, shared):
        theirs = self._post(server, user_b, shared["id"]).json()["message"]
        mine = self._post(server, admin_a, shared["id"]).json()["message"]
        refused = server.delete(f"/v1/message/{mine['id']}", headers=auth(user_b))
        assert refused.status_code == 403, refused.text
        removed = server.delete(f"/v1/message/{theirs['id']}", headers=auth(admin_a))
        assert removed.status_code == 204, removed.text

    def test_an_outsider_cannot_add_people(self, server, admin_b, user_b, shared):
        refused = self._add(server, admin_b, shared["id"], admin_b.id)
        assert refused.status_code in (403, 404), refused.text
        direct = server.post(
            "/v1/conversation/user",
            json={
                "conversation_user": {
                    "conversation_id": shared["id"],
                    "user_id": admin_b.id,
                }
            },
            headers=auth(admin_b),
        )
        assert direct.status_code in (403, 404), direct.text

    def test_removal(self, server, admin_a, user_b, admin_b, shared):
        added = self._add(server, admin_a, shared["id"], admin_b.id)
        assert added.status_code == 200, added.text
        # A participant cannot remove someone else, but can leave.
        refused = server.delete(
            f"/v1/conversation/{shared['id']}/participants/{admin_b.id}",
            headers=auth(user_b),
        )
        assert refused.status_code == 403, refused.text
        left = server.delete(
            f"/v1/conversation/{shared['id']}/participants/{admin_b.id}",
            headers=auth(admin_b),
        )
        assert left.status_code == 200, left.text
        # Leaving revokes access.
        assert self._post(server, admin_b, shared["id"]).status_code in (403, 404)
        assert self._messages(server, admin_b, shared["id"]) == []

    def test_only_the_owner_deletes_the_conversation(
        self, server, admin_a, user_b, shared
    ):
        refused = server.delete(
            f"/v1/conversation/{shared['id']}", headers=auth(user_b)
        )
        assert refused.status_code == 403, refused.text
        removed = server.delete(
            f"/v1/conversation/{shared['id']}", headers=auth(admin_a)
        )
        assert removed.status_code == 204, removed.text

    def test_feedback_is_its_authors(self, server, admin_a, user_b, shared):
        message = self._post(server, admin_a, shared["id"]).json()["message"]
        response = server.post(
            "/v1/feedback",
            json={
                "feedback": {
                    "message_id": message["id"],
                    "content": "useful",
                    "positive": True,
                    "user_id": admin_a.id,
                }
            },
            headers=auth(user_b),
        )
        assert response.status_code == 201, response.text
        feedback = response.json()["feedback"]
        assert feedback["user_id"] == user_b.id
        refused = server.put(
            f"/v1/feedback/{feedback['id']}",
            json={"feedback": {"positive": False}},
            headers=auth(admin_a),
        )
        assert refused.status_code == 403, refused.text

    def test_an_artifact_lands_in_its_messages_conversation(
        self, server, admin_a, admin_b, shared
    ):
        message = self._post(server, admin_a, shared["id"]).json()["message"]
        other = self._conversation(server, admin_b, "Elsewhere")
        response = server.post(
            "/v1/artifact",
            json={
                "artifact": {
                    "name": "notes.txt",
                    "relative_path": "notes.txt",
                    "hosted_path": "/files/notes.txt",
                    "message_id": message["id"],
                    "user_id": admin_b.id,
                }
            },
            headers=auth(admin_a),
        )
        assert response.status_code == 201, response.text
        artifact = response.json()["artifact"]
        assert artifact["conversation_id"] == shared["id"]
        assert artifact["user_id"] == admin_a.id
        mismatched = server.post(
            "/v1/artifact",
            json={
                "artifact": {
                    "name": "x",
                    "relative_path": "x",
                    "hosted_path": "/x",
                    "message_id": message["id"],
                    "conversation_id": other["id"],
                }
            },
            headers=auth(admin_a),
        )
        assert mismatched.status_code == 400, mismatched.text

    def test_a_direct_message_is_found_from_either_side(self, server, admin_a, admin_b):
        opened = server.post(
            "/v1/conversation/direct",
            json={"other_user_id": admin_b.id, "initial_message": "hi b"},
            headers=auth(admin_a),
        )
        assert opened.status_code == 200, opened.text
        first = opened.json()
        assert first["message"]["content"] == "hi b"
        again = server.post(
            "/v1/conversation/direct",
            json={"other_user_id": admin_a.id, "initial_message": "hi a"},
            headers=auth(admin_b),
        )
        assert again.status_code == 200, again.text
        assert again.json()["conversation"]["id"] == first["conversation"]["id"]
        contents = {
            m["content"]
            for m in self._messages(server, admin_b, first["conversation"]["id"])
        }
        assert {"hi b", "hi a"} <= contents

    def test_a_direct_message_needs_another_user(self, server, admin_a):
        response = server.post(
            "/v1/conversation/direct",
            json={"other_user_id": admin_a.id},
            headers=auth(admin_a),
        )
        assert response.status_code == 400, response.text

    def test_a_thread(self, server, admin_a, shared):
        root = self._post(server, admin_a, shared["id"], "root").json()["message"]
        reply = self._post(server, admin_a, shared["id"], "reply", parent_id=root["id"])
        assert reply.status_code == 201, reply.text
        nested = self._post(
            server,
            admin_a,
            shared["id"],
            "nested",
            parent_id=reply.json()["message"]["id"],
        )
        assert nested.status_code == 201, nested.text
        response = server.get(f"/v1/message/{root['id']}/thread", headers=auth(admin_a))
        assert response.status_code == 200, response.text
        assert [m["content"] for m in response.json()["messages"]] == [
            "root",
            "reply",
            "nested",
        ]
        shallow = server.get(
            f"/v1/message/{root['id']}/thread",
            params={"depth": 1},
            headers=auth(admin_a),
        )
        assert [m["content"] for m in shallow.json()["messages"]] == ["root", "reply"]
        assert MAX_THREAD_DEPTH == 10

    def test_a_reply_stays_in_its_parents_conversation(self, server, admin_a, shared):
        root = self._post(server, admin_a, shared["id"]).json()["message"]
        other = self._conversation(server, admin_a, "Other")
        stray = self._post(server, admin_a, other["id"], parent_id=root["id"])
        assert stray.status_code == 400, stray.text

    def test_a_voice_message_needs_a_transcribing_model(self, server, admin_a, shared):
        response = server.post(
            "/v1/message/voice",
            json={
                "conversation_id": shared["id"],
                "audio_base64": base64.b64encode(b"ID3").decode(),
            },
            headers=auth(admin_a),
        )
        assert response.status_code == 503, response.text

    def test_an_agent_message_has_no_author(self, server, admin_a, shared):
        message = MessageManager(
            requester_id=admin_a.id, model_registry=server.app.state.model_registry
        ).create_agent_message(conversation_id=shared["id"], content="I am an agent")
        assert message.user_id is None


class TestAbilities(ExtensionServerMixin):
    extension_class = EXT_Conversations

    @pytest.fixture(autouse=True)
    def attached(self, server, monkeypatch) -> None:
        registry = server.app.state.model_registry
        monkeypatch.setattr(
            ModelRegistry, "attached", classmethod(lambda cls: registry)
        )

    async def test_a_direct_conversation_through_the_abilities(self, admin_a, user_b):
        opened = await EXT_Conversations.start_direct_message(
            admin_a.id, user_b.id, "Lunch?"
        )
        conversation_id = opened["conversation"]["id"]
        reply = await EXT_Conversations.post_message(user_b.id, conversation_id, "Yes")
        assert reply["user_id"] == user_b.id
        read = await EXT_Conversations.read_messages(admin_a.id, conversation_id)
        assert [m["content"] for m in read][-2:] == ["Lunch?", "Yes"]
        listed = {
            c["id"] for c in await EXT_Conversations.list_conversations(user_b.id)
        }
        assert conversation_id in listed

    async def test_an_outsider_is_refused(self, admin_a, admin_b, user_b):
        opened = await EXT_Conversations.start_direct_message(admin_a.id, user_b.id)
        with pytest.raises(HTTPException):
            await EXT_Conversations.post_message(
                admin_b.id, opened["conversation"]["id"], "let me in"
            )

    async def test_checks(self, admin_a):
        with pytest.raises(InvalidInputExternalError):
            await EXT_Conversations.read_messages(admin_a.id, "any", limit=0)
        with pytest.raises(InvalidInputExternalError):
            await EXT_Conversations.post_message(admin_a.id, "any", "  ")
        with pytest.raises(HTTPException) as raised:
            await EXT_Conversations.list_conversations("")
        assert raised.value.status_code == 400
