# SPDX-License-Identifier: AGPL-3.0-or-later
"""Following a conversation live, against a real server and database: only
someone who can read the conversation may follow it; a follower hears each
message created, edited and deleted over REST, by id and never by content,
and the records another extension announces for the conversation; a
participant who is removed stops hearing at once, and cannot follow again."""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any, Dict

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from zephyrex.extensions.conversations.BLL_Conversations import (
    FEED,
    MESSAGE_CREATED,
    MESSAGE_DELETED,
    MESSAGE_UPDATED,
    PARTICIPANT_REMOVED,
    ConversationManager,
    announce,
)
from zephyrex.extensions.conversations.Conversations_access_test import (
    AcquaintedServer,
    auth,
)
from zephyrex.testing.factories import current_if_match, if_match_of

# How long a test waits for a frame it expects before failing.
FRAME_TIMEOUT_SECONDS = 5.0
SECRET = "The vault code is 7-1-9."


def _request() -> Request:
    """A request whose client stays connected (the stream ends when the test closes it)."""

    async def receive() -> Dict[str, Any]:
        await asyncio.Event().wait()
        return {"type": "http.disconnect"}

    return Request({"type": "http", "method": "GET", "headers": []}, receive=receive)


class Feed:
    """One open stream on a conversation, read frame by frame."""

    def __init__(self, body: AsyncIterator[str]) -> None:
        self._body = body
        self.frames: list[str] = []

    async def frame(self) -> str:
        frame = await asyncio.wait_for(
            self._body.__anext__(), timeout=FRAME_TIMEOUT_SECONDS
        )
        self.frames.append(frame)
        return frame

    async def event(self) -> Dict[str, Any]:
        """The next data frame, skipping comments."""
        while True:
            frame = await self.frame()
            if frame.startswith("data: "):
                parsed: Dict[str, Any] = json.loads(frame[len("data: ") :])
                return parsed

    async def ended(self) -> bool:
        try:
            await self.event()
        except StopAsyncIteration:
            return True
        return False

    async def close(self) -> None:
        await self._body.aclose()  # type: ignore[attr-defined]


class TestConversationFeed(AcquaintedServer):

    @pytest.fixture
    def table(self, server, admin_a, user_b) -> Dict[str, Any]:
        """admin_a's conversation, with user_b added."""
        created = server.post(
            "/v1/conversation",
            json={
                "conversation": {
                    "name": f"Table {uuid.uuid4().hex[:8]}",
                    "is_group_chat": True,
                }
            },
            headers=auth(admin_a),
        )
        assert created.status_code == 201, created.text
        conversation: Dict[str, Any] = created.json()["conversation"]
        added = server.post(
            f"/v1/conversation/{conversation['id']}/participants",
            json={"user_id": user_b.id},
            headers=auth(admin_a),
        )
        assert added.status_code == 200, added.text
        return conversation

    @staticmethod
    async def _follow(server, user, conversation_id: str) -> Feed:
        manager = ConversationManager(
            requester_id=user.id, model_registry=server.app.state.model_registry
        )
        response = await manager.events_route(
            request=_request(), conversation_id=conversation_id
        )
        feed = Feed(response.body_iterator)
        assert await feed.frame() == ": connected\n\n"
        return feed

    async def test_an_outsider_is_refused_at_join(self, server, admin_b, table):
        listening = FEED.listening(table["id"])
        with pytest.raises(HTTPException) as refused:
            await self._follow(server, admin_b, table["id"])
        # The same answer as for a conversation that doesn't exist.
        assert refused.value.status_code == 404
        with pytest.raises(HTTPException) as unknown:
            await self._follow(server, admin_b, str(uuid.uuid4()))
        assert unknown.value.status_code == 404
        assert FEED.listening(table["id"]) == listening

    async def test_a_member_hears_messages_created_edited_and_deleted(
        self, server, admin_a, user_b, table
    ):
        feed = await self._follow(server, user_b, table["id"])
        posted = server.post(
            "/v1/message",
            json={"message": {"conversation_id": table["id"], "content": SECRET}},
            headers=auth(admin_a),
        )
        assert posted.status_code == 201, posted.text
        message_id = posted.json()["message"]["id"]
        assert await feed.event() == {"type": MESSAGE_CREATED, "id": message_id}

        url = f"/v1/message/{message_id}"
        edited = server.put(
            url,
            json={"message": {"content": "The vault is open."}},
            headers={**auth(admin_a), **current_if_match(server, url, auth(admin_a))},
        )
        assert edited.status_code == 200, edited.text
        assert await feed.event() == {"type": MESSAGE_UPDATED, "id": message_id}

        deleted = server.delete(
            url,
            headers={**auth(admin_a), **current_if_match(server, url, auth(admin_a))},
        )
        assert deleted.status_code == 204, deleted.text
        assert await feed.event() == {"type": MESSAGE_DELETED, "id": message_id}

        # Another extension's records for the conversation (a game's chat
        # lines) reach the same followers when it announces them.
        announce(table["id"], "chat_message.created", "line-1")
        assert await feed.event() == {"type": "chat_message.created", "id": "line-1"}

        # Ids only: the content is read over REST, under its own access.
        assert not any("vault" in frame for frame in feed.frames)
        await feed.close()
        assert user_b.id not in FEED.listening(table["id"])

    async def test_a_removed_participant_stops_hearing(
        self, server, admin_a, user_b, table
    ):
        removed = await self._follow(server, user_b, table["id"])
        owner = await self._follow(server, admin_a, table["id"])
        # The removal names the membership row's version.
        memberships = server.get(
            "/v1/conversation/user",
            params={"conversation_id": table["id"]},
            headers=auth(admin_a),
        )
        assert memberships.status_code == 200, memberships.text
        [membership] = [
            row
            for row in memberships.json()["conversation_users"]
            if row["user_id"] == user_b.id
        ]
        response = server.delete(
            f"/v1/conversation/{table['id']}/participants/{user_b.id}",
            headers={**auth(admin_a), **if_match_of(membership)},
        )
        assert response.status_code == 200, response.text
        # The removed participant's stream ends with nothing more; the others
        # hear who left.
        assert await removed.ended()
        assert await owner.event() == {"type": PARTICIPANT_REMOVED, "id": user_b.id}

        posted = server.post(
            "/v1/message",
            json={"message": {"conversation_id": table["id"], "content": SECRET}},
            headers=auth(admin_a),
        )
        assert posted.status_code == 201, posted.text
        assert (await owner.event())["type"] == MESSAGE_CREATED
        assert FEED.listening(table["id"]) == [admin_a.id]

        with pytest.raises(HTTPException) as refused:
            await self._follow(server, user_b, table["id"])
        assert refused.value.status_code == 404
        await owner.close()
