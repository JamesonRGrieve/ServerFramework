# SPDX-License-Identifier: AGPL-3.0-or-later
"""Live sync against a real server and database: joining a channel, the first
device becoming main, messages reaching the other devices but not the sender,
claiming control, handing control on when the main device leaves, channels
being private to their user, and the HTTP refusals."""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from starlette.requests import Request

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.live_sync.BLL_LiveSync import (
    DEVICE_QUEUE_SIZE,
    HUB,
    LiveMessageEvent,
    LivePost,
    LiveSyncManager,
)
from zephyrex.extensions.live_sync.EXT_LiveSync import EXT_LiveSync

# How long a test waits for a frame it expects before failing.
FRAME_TIMEOUT_SECONDS = 5.0


def _request() -> Request:
    """A request whose client stays connected (the stream ends when the test closes it)."""

    async def receive() -> dict[str, Any]:
        await asyncio.Event().wait()
        return {"type": "http.disconnect"}

    return Request({"type": "http", "method": "GET", "headers": []}, receive=receive)


class Device:
    """One open stream on a channel, read frame by frame."""

    def __init__(self, body: AsyncIterator[str]) -> None:
        self._body = body

    async def frame(self) -> str:
        return await asyncio.wait_for(
            self._body.__anext__(), timeout=FRAME_TIMEOUT_SECONDS
        )

    async def message(self) -> dict[str, Any]:
        """The next data frame, skipping comments."""
        while True:
            frame = await self.frame()
            if frame.startswith("data: "):
                parsed: dict[str, Any] = json.loads(frame[len("data: ") :])
                return parsed

    async def presence(self) -> list[dict[str, Any]]:
        while True:
            message = await self.message()
            if message["type"] == "presence":
                devices: list[dict[str, Any]] = message["data"]["devices"]
                return devices

    async def close(self) -> None:
        await self._body.aclose()  # type: ignore[attr-defined]


class TestLiveSync(ExtensionServerMixin):
    extension_class = EXT_LiveSync

    @staticmethod
    def _manager(model_registry: Any, user: Any) -> LiveSyncManager:
        return LiveSyncManager(requester_id=user.id, model_registry=model_registry)

    async def _join(
        self,
        model_registry: Any,
        user: Any,
        channel: str,
        client_id: str,
        label: str | None = None,
    ) -> Device:
        response = await self._manager(model_registry, user).events_route(
            request=_request(), channel=channel, client_id=client_id, label=label
        )
        device = Device(response.body_iterator)
        assert await device.frame() == ": connected\n\n"
        return device

    async def test_first_device_is_main_and_messages_skip_the_sender(
        self, model_registry, admin_a
    ):
        laptop = await self._join(
            model_registry, admin_a, "prompter-a", "laptop", "Laptop"
        )
        assert await laptop.presence() == [
            {"client_id": "laptop", "label": "Laptop", "main": True, "connected": True}
        ]

        phone = await self._join(model_registry, admin_a, "prompter-a", "phone")
        devices = {d["client_id"]: d["main"] for d in await phone.presence()}
        assert devices == {"laptop": True, "phone": False}
        assert {d["client_id"] for d in await laptop.presence()} == {"laptop", "phone"}

        manager = self._manager(model_registry, admin_a)
        await manager.post_route(
            channel="prompter-a",
            body=LivePost(client_id="laptop", type="scroll", data={"y": 120}),
        )
        assert await phone.message() == {
            "type": "scroll",
            "sender": "laptop",
            "data": {"y": 120},
        }

        # The sender never hears its own message: its next frame is the phone's.
        await manager.post_route(
            channel="prompter-a",
            body=LivePost(client_id="phone", type="scroll", data={"y": 7}),
        )
        assert await laptop.message() == {
            "type": "scroll",
            "sender": "phone",
            "data": {"y": 7},
        }

        await phone.close()
        await laptop.close()

    async def test_claim_moves_control_and_leaving_hands_it_on(
        self, model_registry, admin_a
    ):
        display = await self._join(model_registry, admin_a, "prompter-b", "display")
        await display.presence()
        remote = await self._join(model_registry, admin_a, "prompter-b", "remote")
        await remote.presence()
        await display.presence()

        manager = self._manager(model_registry, admin_a)
        await manager.post_route(
            channel="prompter-b", body=LivePost(client_id="remote", type="claim")
        )
        claimed = {d["client_id"]: d["main"] for d in await display.presence()}
        assert claimed == {"display": False, "remote": True}
        assert manager.devices_route(channel="prompter-b").model_dump()["devices"] == [
            {"client_id": "display", "label": None, "main": False, "connected": True},
            {"client_id": "remote", "label": None, "main": True, "connected": True},
        ]

        await remote.close()
        handed_on = {d["client_id"]: d["main"] for d in await display.presence()}
        assert handed_on == {"display": True}
        await display.close()

    async def test_a_channel_is_private_to_its_user(
        self, model_registry, admin_a, admin_b
    ):
        mine = await self._join(model_registry, admin_a, "shared-name", "a-laptop")
        await mine.presence()
        theirs = await self._join(model_registry, admin_b, "shared-name", "b-laptop")
        # admin_b's device is alone on its own channel of the same name.
        assert await theirs.presence() == [
            {"client_id": "b-laptop", "label": None, "main": True, "connected": True}
        ]

        await self._manager(model_registry, admin_b).post_route(
            channel="shared-name",
            body=LivePost(client_id="b-laptop", type="scroll", data=1),
        )
        await self._manager(model_registry, admin_a).post_route(
            channel="shared-name", body=LivePost(client_id="a-laptop", type="ping")
        )
        # admin_a's stream never saw admin_b's message or presence: the very next
        # thing it hears is its own phone joining.
        other = await self._join(model_registry, admin_a, "shared-name", "a-phone")
        await other.presence()
        heard = await mine.message()
        assert heard["type"] == "presence"
        assert {d["client_id"] for d in heard["data"]["devices"]} == {
            "a-laptop",
            "a-phone",
        }
        for device in (other, theirs, mine):
            await device.close()

    async def test_the_same_device_cannot_join_twice(self, model_registry, admin_a):
        first = await self._join(model_registry, admin_a, "prompter-c", "tablet")
        with pytest.raises(Exception) as refused:
            await self._join(model_registry, admin_a, "prompter-c", "tablet")
        assert getattr(refused.value, "status_code", None) == 409
        await first.close()

    async def test_a_device_that_falls_behind_is_dropped_not_buffered_forever(
        self, model_registry, admin_a
    ):
        slow = await self._join(model_registry, admin_a, "prompter-d", "slow")
        await slow.presence()
        for index in range(DEVICE_QUEUE_SIZE + 1):
            HUB.deliver(
                LiveMessageEvent(
                    user_id=str(admin_a.id),
                    channel="prompter-d",
                    sender="fast",
                    type="scroll",
                    data=index,
                )
            )
        await asyncio.sleep(0)
        assert "slow" not in HUB.connected(str(admin_a.id), "prompter-d")
        await slow.close()

    def test_http_refusals(self, server, admin_a):
        headers = {"Authorization": f"Bearer {admin_a.jwt}"}
        assert server.get("/v1/live/prompter/devices").status_code == 401
        assert (
            server.post(
                "/v1/live/prompter", json={"client_id": "x", "type": "scroll"}
            ).status_code
            == 401
        )
        # Posting before joining the channel.
        assert (
            server.post(
                "/v1/live/never-joined",
                json={"client_id": "ghost", "type": "scroll"},
                headers=headers,
            ).status_code
            == 409
        )
        # The server's own message types are reserved; names are validated.
        assert (
            server.post(
                "/v1/live/prompter",
                json={"client_id": "x", "type": "presence"},
                headers=headers,
            ).status_code
            == 422
        )
        assert (
            server.post(
                "/v1/live/prompter",
                json={"client_id": "not ok!", "type": "scroll"},
                headers=headers,
            ).status_code
            == 422
        )
        assert (
            server.get("/v1/live/bad%20name/devices", headers=headers).status_code
            == 422
        )
        assert server.get("/v1/live/prompter/devices", headers=headers).json() == {
            "devices": []
        }
