# SPDX-License-Identifier: AGPL-3.0-or-later
"""changes(): a sync's read of what changed among the requester's records, against a real server
(conversations, which soft-delete): oldest change first, a deleted record included with its
deleted_at, nothing of anyone else's, paged by a cursor until it runs out, while an ordinary list
still hides what was deleted."""

import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi import HTTPException

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.conversations.BLL_Conversations import ConversationManager
from zephyrex.extensions.conversations.EXT_Conversations import EXT_Conversations

# Long enough that each step's change time differs from the last.
STEP_SECONDS = 0.02


class TestChanges(ExtensionServerMixin):
    extension_class = EXT_Conversations

    @staticmethod
    def _manager(server: Any, user: Any) -> ConversationManager:
        return ConversationManager(
            requester_id=user.id, model_registry=server.app.state.model_registry
        )

    @pytest.fixture
    def history(self, server: Any, admin_a: Any) -> dict[str, Any]:
        """admin_a's three conversations: the first renamed after the third was
        made, the second deleted last."""
        since = datetime.now(timezone.utc) - timedelta(seconds=1)
        manager = self._manager(server, admin_a)
        made = []
        for label in ("one", "two", "three"):
            made.append(
                manager.create(
                    name=f"{label} {uuid.uuid4().hex[:6]}", is_group_chat=True
                ).id
            )
            time.sleep(STEP_SECONDS)
        manager.update(made[0], name=f"one, renamed {uuid.uuid4().hex[:6]}")
        time.sleep(STEP_SECONDS)
        manager.delete(made[1])
        return {"since": since, "one": made[0], "two": made[1], "three": made[2]}

    def test_oldest_change_first_with_the_deleted_one_marked(
        self, server: Any, admin_a: Any, history: dict[str, Any]
    ) -> None:
        page = self._manager(server, admin_a).changes(since=history["since"])
        mine = [
            change
            for change in page.changes
            if change.record.id in {history["one"], history["two"], history["three"]}
        ]
        assert [change.record.id for change in mine] == [
            history["three"],
            history["one"],
            history["two"],
        ]
        assert [change.deleted_at is not None for change in mine] == [
            False,
            False,
            True,
        ]
        assert mine[2].changed_at == mine[2].deleted_at
        # An ordinary list still hides what was deleted.
        listed = {row.id for row in self._manager(server, admin_a).list()}
        assert history["two"] not in listed
        assert {history["one"], history["three"]} <= listed

    def test_nothing_of_anyone_elses(
        self, server: Any, admin_b: Any, history: dict[str, Any]
    ) -> None:
        page = self._manager(server, admin_b).changes(since=history["since"])
        seen = {change.record.id for change in page.changes}
        assert not seen & {history["one"], history["two"], history["three"]}

    def test_a_cursor_walks_every_page_then_stops(
        self, server: Any, admin_a: Any, history: dict[str, Any]
    ) -> None:
        manager = self._manager(server, admin_a)
        walked: list[str] = []
        after = None
        for _ in range(50):
            page = manager.changes(since=history["since"], after=after, limit=1)
            walked += [change.record.id for change in page.changes]
            if page.next is None:
                break
            after = page.next
        assert page.next is None
        everything = [
            change.record.id
            for change in manager.changes(since=history["since"]).changes
        ]
        assert walked == everything
        assert len(walked) == len(set(walked))

    def test_refusals(self, server: Any, admin_a: Any) -> None:
        manager = self._manager(server, admin_a)
        for limit in (0, 501):
            with pytest.raises(HTTPException) as refused:
                manager.changes(limit=limit)
            assert refused.value.status_code == 422
        with pytest.raises(HTTPException) as refused:
            manager.changes(after="not a cursor")
        assert refused.value.status_code == 422
