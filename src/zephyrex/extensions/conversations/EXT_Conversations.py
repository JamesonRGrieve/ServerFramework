# SPDX-License-Identifier: AGPL-3.0-or-later
"""Conversations between users and their agents: direct messages, group
chats, threads, feedback and artifacts (see BLL_Conversations).

Abilities act for the user named by ``requester_id`` under that user's
permissions, seeing and posting in the conversations the REST routes
show them."""

from typing import Any, ClassVar, Dict, List, Optional, Set

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
    ability,
)
from zephyrex.extensions.conversations.BLL_Conversations import (
    ConversationManager,
    MessageManager,
)
from zephyrex.extensions.ExternalErrors import InvalidInputExternalError
from zephyrex.lib.Dependencies import Dependencies, EXT_Dependency

MAX_RECENT_MESSAGES = 200


def _row(model: Any) -> Dict[str, Any]:
    dumped: Dict[str, Any] = model.model_dump(mode="json")
    return dumped


class EXT_Conversations(AbstractStaticExtension):
    name: ClassVar[str] = "conversations"
    version: ClassVar[str] = "2.0.0"
    description: ClassVar[str] = (
        "Direct messages and group chats between users and their agents"
    )

    _env: ClassVar[Dict[str, Any]] = {}
    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            EXT_Dependency(
                name="acl_rbac",
                friendly_name="Access control",
                reason="Participants are granted access to a conversation",
            ),
            EXT_Dependency(
                name="ai",
                friendly_name="AI",
                optional=True,
                reason="Transcribes voice messages",
            ),
        ]
    )
    _abilities: ClassVar[Set[str]] = {
        "list_conversations",
        "read_messages",
        "post_message",
        "start_direct_message",
        "add_participant",
        "remove_participant",
    }

    @classmethod
    def conversations(cls, requester_id: str) -> ConversationManager:
        manager: ConversationManager = cls.as_requester(
            ConversationManager, requester_id
        )
        return manager

    @classmethod
    def messages(cls, requester_id: str) -> MessageManager:
        manager: MessageManager = cls.as_requester(MessageManager, requester_id)
        return manager

    @classmethod
    @ability("list_conversations")
    async def list_conversations(cls, requester_id: str) -> List[Dict[str, Any]]:
        """The conversations the user owns or takes part in."""
        return [_row(c) for c in cls.conversations(requester_id).list()]

    @classmethod
    @ability("read_messages")
    async def read_messages(
        cls, requester_id: str, conversation_id: str, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """A conversation's last ``limit`` messages, oldest first."""
        if not 1 <= limit <= MAX_RECENT_MESSAGES:
            raise InvalidInputExternalError(f"limit is 1-{MAX_RECENT_MESSAGES}")
        return [
            _row(m) for m in cls.messages(requester_id).recent(conversation_id, limit)
        ]

    @classmethod
    @ability("post_message")
    async def post_message(
        cls,
        requester_id: str,
        conversation_id: str,
        content: str,
        parent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Post ``content`` as the user, optionally as a reply."""
        if not content or not content.strip():
            raise InvalidInputExternalError("a message has content")
        return _row(
            cls.messages(requester_id).create(
                conversation_id=conversation_id, content=content, parent_id=parent_id
            )
        )

    @classmethod
    @ability("start_direct_message")
    async def start_direct_message(
        cls,
        requester_id: str,
        other_user_id: str,
        initial_message: Optional[str] = None,
    ) -> Dict[str, Any]:
        """The direct conversation with another user (made if there is
        none), with ``initial_message`` posted in it."""
        found = cls.conversations(requester_id).create_direct_message(
            other_user_id, initial_message
        )
        return {
            "conversation": _row(found["conversation"]),
            "message": _row(found["message"]) if found["message"] else None,
        }

    @classmethod
    @ability("add_participant")
    async def add_participant(
        cls, requester_id: str, conversation_id: str, user_id: str
    ) -> Dict[str, Any]:
        return _row(
            cls.conversations(requester_id).add_participant(conversation_id, user_id)
        )

    @classmethod
    @ability("remove_participant")
    async def remove_participant(
        cls, requester_id: str, conversation_id: str, user_id: str
    ) -> Dict[str, Any]:
        """Remove someone (the owner may), or leave (anyone may)."""
        cls.conversations(requester_id).remove_participant(conversation_id, user_id)
        return {"conversation_id": conversation_id, "removed": user_id}
