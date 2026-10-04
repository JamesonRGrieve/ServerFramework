# SPDX-License-Identifier: AGPL-3.0-or-later
"""Conversations filed in projects: linked by whoever may edit the project,
only to conversations they see, and seen through the project."""

import uuid
from typing import Any, Dict

import pytest
from fastapi import HTTPException

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.ai_agents.BLL_AI_Agents import (
    ProjectConversationManager,
    ProjectManager,
)
from zephyrex.extensions.ai_agents.EXT_AI_Agents import EXT_AI_Agents
from zephyrex.extensions.conversations.BLL_Conversations import ConversationManager


def auth(user: Any) -> Dict[str, str]:
    return {"Authorization": f"Bearer {user.jwt}"}


class TestProjectConversations(ExtensionServerMixin):
    extension_class = EXT_AI_Agents

    def _links(self, user: Any, model_registry: Any) -> ProjectConversationManager:
        return ProjectConversationManager(
            requester_id=user.id, model_registry=model_registry
        )

    def _project(self, user: Any, model_registry: Any, **fields: Any) -> Any:
        return ProjectManager(
            requester_id=user.id, model_registry=model_registry
        ).create(name=f"Project {uuid.uuid4()}", **fields)

    def _conversation(self, user: Any, model_registry: Any) -> Any:
        return ConversationManager(
            requester_id=user.id, model_registry=model_registry
        ).create(name=f"Chat {uuid.uuid4()}")

    def test_link_list_and_unlink(self, admin_a, model_registry):
        project = self._project(admin_a, model_registry)
        conversation = self._conversation(admin_a, model_registry)
        links = self._links(admin_a, model_registry)
        link = links.link(project.id, conversation.id)
        assert links.link(project.id, conversation.id).id == link.id
        assert [
            (row.project_id, row.conversation_id)
            for row in links.list(project_id=project.id)
        ] == [(project.id, conversation.id)]
        assert links.unlink(project.id, conversation.id) == 1
        assert links.list(project_id=project.id) == []

    def test_an_invisible_conversation_is_404(self, admin_a, admin_b, model_registry):
        project = self._project(admin_a, model_registry)
        theirs = self._conversation(admin_b, model_registry)
        with pytest.raises(HTTPException) as refused:
            self._links(admin_a, model_registry).link(project.id, theirs.id)
        assert refused.value.status_code == 404
        assert self._links(admin_a, model_registry).list(project_id=project.id) == []

    def test_a_non_editor_is_refused(self, admin_a, admin_b, model_registry):
        project = self._project(admin_a, model_registry)
        own = self._conversation(admin_b, model_registry)
        with pytest.raises(HTTPException) as refused:
            self._links(admin_b, model_registry).link(project.id, own.id)
        assert refused.value.status_code in (403, 404)
        mine = self._conversation(admin_a, model_registry)
        self._links(admin_a, model_registry).link(project.id, mine.id)
        with pytest.raises(HTTPException) as refused:
            self._links(admin_b, model_registry).unlink(project.id, mine.id)
        assert refused.value.status_code in (403, 404)
        assert (
            len(self._links(admin_a, model_registry).list(project_id=project.id)) == 1
        )

    def test_a_viewer_sees_links_only_through_the_project(
        self, admin_b, team_b, user_b, admin_a, model_registry
    ):
        shared = self._project(admin_b, model_registry, team_id=team_b.id)
        private = self._project(admin_b, model_registry)
        conversation = self._conversation(admin_b, model_registry)
        links = self._links(admin_b, model_registry)
        links.link(shared.id, conversation.id)
        links.link(private.id, conversation.id)
        seen = self._links(user_b, model_registry).list(conversation_id=conversation.id)
        assert [row.project_id for row in seen] == [shared.id]
        assert (
            self._links(admin_a, model_registry).list(conversation_id=conversation.id)
            == []
        )

    def test_the_routes(self, server, admin_a, admin_b, model_registry):
        project = self._project(admin_a, model_registry)
        conversation = self._conversation(admin_a, model_registry)
        made = server.post(
            "/v1/project_conversation",
            json={
                "project_conversation": {
                    "project_id": project.id,
                    "conversation_id": conversation.id,
                }
            },
            headers=auth(admin_a),
        )
        assert made.status_code == 201, made.text
        listed = server.get(
            f"/v1/project_conversation?project_id={project.id}", headers=auth(admin_b)
        )
        assert listed.status_code == 200, listed.text
        assert listed.json()["project_conversations"] == []

    async def test_the_abilities(self, admin_a, model_registry):
        project = self._project(admin_a, model_registry)
        conversation = self._conversation(admin_a, model_registry)
        linked = await EXT_AI_Agents.link_conversation(
            admin_a.id, project.id, conversation.id
        )
        assert (linked["project_id"], linked["conversation_id"]) == (
            project.id,
            conversation.id,
        )
        assert await EXT_AI_Agents.unlink_conversation(
            admin_a.id, project.id, conversation.id
        ) == {"removed": 1}
