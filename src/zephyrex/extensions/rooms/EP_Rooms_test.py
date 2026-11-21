# SPDX-License-Identifier: AGPL-3.0-or-later
"""Rooms against a real server and database. Teams are the rooms here: a
team's own changes and its memberships' go to the team's room. The tests
cover joining (only the record's viewers), live changes that carry no record
data, catch-up by cursor, resets, polling pages, deletes, revocation and dry
runs."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any, Optional

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.rooms import BLL_Rooms
from zephyrex.extensions.rooms.BLL_Rooms import RoomManager
from zephyrex.extensions.rooms.EXT_Rooms import EXT_Rooms
from zephyrex.lib.Environment import env
from zephyrex.lib.Rooms import room_of
from zephyrex.logic.BLL_Auth import TeamManager, UserTeamManager

FRAME_TIMEOUT_SECONDS = 5.0
ROOM_HOOKS = ("rooms_created", "rooms_updated", "note_rooms", "_after_delete")


def _request(headers: Optional[list[tuple[bytes, bytes]]] = None) -> Request:
    """A request whose client stays connected (the test closes the stream)."""

    async def receive() -> dict[str, Any]:
        await asyncio.Event().wait()
        return {"type": "http.disconnect"}

    return Request(
        {"type": "http", "method": "GET", "headers": headers or []}, receive=receive
    )


class Stream:
    """One open room stream, read frame by frame."""

    def __init__(self, body: AsyncIterator[str]) -> None:
        self._body = body

    async def frame(self) -> str:
        return await asyncio.wait_for(
            self._body.__anext__(), timeout=FRAME_TIMEOUT_SECONDS
        )

    async def event(self) -> tuple[str, dict[str, Any], Optional[str]]:
        """The next (event, data, id), skipping comments."""
        while True:
            frame = await self.frame()
            if frame.startswith(":"):
                continue
            fields = dict(
                line.split(": ", 1)
                for line in frame.strip().splitlines()
                if ": " in line
            )
            return fields["event"], json.loads(fields["data"]), fields.get("id")

    async def close(self) -> None:
        await self._body.aclose()  # type: ignore[attr-defined]


@pytest.fixture(scope="module", autouse=True)
def team_rooms() -> Iterator[None]:
    """Map teams to their own room and memberships to their team's, for this
    module only."""
    room_of(TeamManager, lambda team: ("team", team.id))
    room_of(UserTeamManager, lambda membership: ("team", membership.team_id))
    yield
    for manager in (TeamManager, UserTeamManager):
        registry = manager._hook_registry
        for timings in registry.hooks.values():
            for timing in ("before", "after"):
                timings[timing][:] = [
                    h for h in timings[timing] if h["func"].__name__ not in ROOM_HOOKS
                ]
        registry._invalidate()


class TestRooms(ExtensionServerMixin):
    extension_class = EXT_Rooms

    @staticmethod
    def _rooms(model_registry: Any, user: Any) -> RoomManager:
        return RoomManager(requester_id=user.id, model_registry=model_registry)

    async def _join(
        self,
        model_registry: Any,
        user: Any,
        team_id: str,
        since: Optional[str] = None,
        last_event_id: Optional[str] = None,
    ) -> Stream:
        headers = [(b"last-event-id", last_event_id.encode())] if last_event_id else []
        response = await self._rooms(model_registry, user).events_route(
            request=_request(headers), room_type="team", room_id=team_id, since=since
        )
        stream = Stream(response.body_iterator)
        assert await stream.frame() == ": connected\n\n"
        return stream

    @staticmethod
    def _rename(model_registry: Any, user: Any, team_id: str) -> None:
        TeamManager(requester_id=user.id, model_registry=model_registry).update(
            team_id, description=f"changed {uuid.uuid4().hex[:6]}"
        )

    def _cursor(self, model_registry: Any, user: Any, team_id: str) -> Optional[str]:
        page = self._rooms(model_registry, user).changes_route(
            room_type="team", room_id=team_id
        )
        cursor: Optional[str] = page.next
        return cursor

    async def test_only_the_rooms_viewers_may_join(
        self, model_registry, admin_a, admin_b, team_a
    ):
        with pytest.raises(HTTPException) as err:
            await self._rooms(model_registry, admin_b).events_route(
                request=_request(), room_type="team", room_id=team_a.id
            )
        assert err.value.status_code == 404
        with pytest.raises(HTTPException) as err:
            self._rooms(model_registry, admin_b).changes_route(
                room_type="team", room_id=team_a.id
            )
        assert err.value.status_code == 404
        with pytest.raises(HTTPException) as err:
            self._rooms(model_registry, admin_a).changes_route(
                room_type="no such", room_id=team_a.id
            )
        assert err.value.status_code == 422

    async def test_a_change_reaches_the_room_without_the_records_data(
        self, model_registry, admin_a, team_a
    ):
        stream = await self._join(model_registry, admin_a, team_a.id)
        self._rename(model_registry, admin_a, team_a.id)
        event, data, change_id = await stream.event()
        await stream.close()
        assert event == "change" and change_id == data["id"]
        assert data["room"] == {"type": "team", "id": team_a.id}
        assert data["record"] == {"type": "team", "id": team_a.id}
        assert data["op"] == "updated" and data["etag"]
        assert set(data) == {"id", "room", "record", "op", "etag", "changed_at"}

    async def test_a_reconnect_catches_up_from_its_cursor(
        self, model_registry, admin_a, team_a
    ):
        cursor = self._cursor(model_registry, admin_a, team_a.id)
        self._rename(model_registry, admin_a, team_a.id)
        self._rename(model_registry, admin_a, team_a.id)

        replayed = await self._join(
            model_registry, admin_a, team_a.id, last_event_id=cursor
        )
        first, second = await replayed.event(), await replayed.event()
        assert first[0] == second[0] == "change"
        assert first[1]["changed_at"] <= second[1]["changed_at"]
        # Then live, with no duplicate of what was replayed.
        self._rename(model_registry, admin_a, team_a.id)
        live = await replayed.event()
        await replayed.close()
        assert live[2] not in {first[2], second[2]}

    async def test_an_unknown_cursor_resets(self, model_registry, admin_a, team_a):
        stream = await self._join(
            model_registry, admin_a, team_a.id, since="not-a-change"
        )
        event, data, _ = await stream.event()
        await stream.close()
        assert event == "reset" and "reason" in data

        page = self._rooms(model_registry, admin_a).changes_route(
            room_type="team", room_id=team_a.id, after="not-a-change"
        )
        assert page.reset is True and page.changes == []

    async def test_polling_pages_through_changes(self, model_registry, admin_a, team_a):
        rooms = self._rooms(model_registry, admin_a)
        start = rooms.changes_route(room_type="team", room_id=team_a.id)
        assert start.changes == []
        for _ in range(3):
            self._rename(model_registry, admin_a, team_a.id)
        page = rooms.changes_route(
            room_type="team", room_id=team_a.id, after=start.next, limit=2
        )
        assert len(page.changes) == 2 and page.reset is False
        rest = rooms.changes_route(room_type="team", room_id=team_a.id, after=page.next)
        assert len(rest.changes) == 1
        assert rest.next == rest.changes[0].id
        quiet = rooms.changes_route(
            room_type="team", room_id=team_a.id, after=rest.next
        )
        assert quiet.changes == [] and quiet.next == rest.next

    async def test_membership_create_and_delete_reach_the_team(
        self, model_registry, admin_a, user_b, team_a
    ):
        stream = await self._join(model_registry, admin_a, team_a.id)
        memberships = UserTeamManager(
            requester_id=admin_a.id, model_registry=model_registry
        )
        made = memberships.create(
            user_id=user_b.id, team_id=team_a.id, role_id=env("USER_ROLE_ID")
        )
        created = await stream.event()
        memberships.delete(made.id)
        deleted = await stream.event()
        await stream.close()
        assert created[1]["record"] == {"type": "user_team", "id": made.id}
        assert created[1]["op"] == "created"
        assert deleted[1]["record"] == {"type": "user_team", "id": made.id}
        assert deleted[1]["op"] == "deleted" and deleted[1]["etag"] is None

    async def test_a_member_who_loses_access_is_revoked(
        self, model_registry, admin_a, user_b, team_a, monkeypatch
    ):
        monkeypatch.setattr(BLL_Rooms, "ACCESS_RECHECK_SECONDS", 0.0)
        memberships = UserTeamManager(
            requester_id=admin_a.id, model_registry=model_registry
        )
        made = memberships.create(
            user_id=user_b.id, team_id=team_a.id, role_id=env("USER_ROLE_ID")
        )
        stream = await self._join(model_registry, user_b, team_a.id)
        memberships.delete(made.id)
        event, data, _ = await stream.event()
        await stream.close()
        assert event == "revoked"
        assert data == {"room": {"type": "team", "id": team_a.id}}

    async def test_a_dry_run_publishes_nothing(self, model_registry, admin_a, team_a):
        cursor = self._cursor(model_registry, admin_a, team_a.id)
        with model_registry.database_manager.rollback_scope() as scope:
            self._rename(model_registry, admin_a, team_a.id)
        assert any(hook.endswith("rooms_updated") for hook in scope.skipped_hooks)
        assert self._cursor(model_registry, admin_a, team_a.id) == cursor

    def test_the_room_events_schema_matches_its_model(self, server: Any) -> None:
        """The migrations build exactly the table the model declares, indexes
        included, so ``zephyrex migrate verify rooms`` finds nothing."""
        import io

        from zephyrex.cli import main

        out = io.StringIO()
        code = main(
            ["migrate", "verify", "rooms"],
            app=server.app,
            stdin=io.StringIO(),
            stdout=out,
            stderr=io.StringIO(),
        )
        result = json.loads(out.getvalue())["result"]
        assert code == 0, result
        assert result["blocking"] == [] and result["advisory"] == [], result
