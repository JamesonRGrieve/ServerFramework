# SPDX-License-Identifier: AGPL-3.0-or-later
"""Synchronous manager work runs off the event loop: a slow synchronous
route does not hold a concurrent request, on a custom route, a legacy
custom route or a generated CRUD route; and the loop watchdog names the code
that holds the loop when something still does."""

import asyncio
import contextvars
import inspect
import threading
import time
from typing import Any, List

import httpx
import pytest
from fastapi import APIRouter, FastAPI
from pydantic import BaseModel

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.auth_api_keys.EXT_Auth_APIKeys import EXT_Auth_APIKeys
from zephyrex.lib import LoopWatchdog as watchdog_module
from zephyrex.lib.CustomRoute import custom_route, register_custom_routes
from zephyrex.lib.LoopWatchdog import LoopWatchdog
from zephyrex.lib.OffLoop import call_off_loop, runs_on_loop
from zephyrex.logic.AbstractLogicManager import AbstractBLLManager
from zephyrex.pydantic2.fastapi.router import generate_routers_from_model_registry

SLOW_SECONDS = 1.0


class Reply(BaseModel):
    thread: str


class SlowAndFastManager(AbstractBLLManager):
    @custom_route(
        method="GET", path="/slow", output_model=Reply, authentication_type="none"
    )
    def slow(self) -> Reply:
        time.sleep(SLOW_SECONDS)  # a synchronous query that takes its time
        return Reply(thread=threading.current_thread().name)

    @custom_route(
        method="GET", path="/fast", output_model=Reply, authentication_type="none"
    )
    def fast(self) -> Reply:
        return Reply(thread=threading.current_thread().name)

    @custom_route(
        method="GET", path="/coroutine", output_model=Reply, authentication_type="none"
    )
    async def coroutine(self) -> Reply:
        return Reply(thread=threading.current_thread().name)


class TestCallOffLoop:
    async def test_a_sync_callable_runs_on_a_worker_thread_with_the_context(self):
        var: contextvars.ContextVar[str] = contextvars.ContextVar("probe")
        var.set("request")
        here = threading.get_ident()
        seen = await call_off_loop(lambda: (threading.get_ident(), var.get()))
        assert seen[0] != here
        assert seen[1] == "request"

    async def test_a_coroutine_function_is_awaited_on_the_loop(self):
        async def on_loop() -> int:
            return threading.get_ident()

        assert runs_on_loop(on_loop)
        assert await call_off_loop(on_loop) == threading.get_ident()

    def test_the_hook_wrapper_is_looked_beneath(self):
        assert not runs_on_loop(SlowAndFastManager.slow)
        assert runs_on_loop(SlowAndFastManager.coroutine)


@pytest.fixture(scope="module")
def app(model_registry: Any) -> FastAPI:
    app = FastAPI()
    app.state.model_registry = model_registry
    router = APIRouter(prefix="/v1/off-loop")
    assert register_custom_routes(router, SlowAndFastManager) == 3
    app.include_router(router)
    return app


class TestCustomRoutes:
    async def test_a_slow_sync_route_does_not_stall_a_concurrent_one(self, app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            finished: List[str] = []

            async def get(path: str) -> httpx.Response:
                response: httpx.Response = await c.get(path)
                finished.append(path)
                return response

            slow = asyncio.ensure_future(get("/v1/off-loop/slow"))
            await asyncio.sleep(0.1)  # the slow call is under way
            started = time.monotonic()
            fast = await get("/v1/off-loop/fast")
            waited = time.monotonic() - started
            assert fast.status_code == 200, fast.text
            assert waited < SLOW_SECONDS / 2, waited
            assert finished == ["/v1/off-loop/fast"]
            assert (await slow).status_code == 200
            assert finished == ["/v1/off-loop/fast", "/v1/off-loop/slow"]

    async def test_a_coroutine_route_runs_on_the_loop(self, app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            response = await c.get("/v1/off-loop/coroutine")
        assert response.status_code == 200, response.text
        assert response.json()["thread"] == threading.current_thread().name


CRUD_HANDLERS = {
    "get_resource",
    "list_resources",
    "create_resource",
    "update_resource",
    "delete_resource",
    "search_resources",
    "batch_update_resources",
    "batch_delete_resources",
}


class TestGeneratedRoutes(ExtensionServerMixin):
    extension_class = EXT_Auth_APIKeys

    def test_crud_handlers_are_run_on_the_threadpool(self, server):
        """FastAPI runs a plain ``def`` endpoint on its threadpool and an
        ``async def`` one on the loop; every CRUD handler calls synchronous
        managers, so none may be a coroutine."""
        routers = generate_routers_from_model_registry(server.app.state.model_registry)
        handlers = [
            route.endpoint
            for router in routers.values()
            for route in router.routes
            if getattr(getattr(route, "endpoint", None), "__name__", "")
            in CRUD_HANDLERS
        ]
        assert {h.__name__ for h in handlers} >= {"get_resource", "list_resources"}
        assert not [h for h in handlers if inspect.iscoroutinefunction(h)]


class _Recorder:
    def __init__(self) -> None:
        self.warnings: List[str] = []

    def warning(self, message: str, *args: Any) -> None:
        self.warnings.append(message % args)


class TestLoopWatchdog:
    async def test_a_stall_is_logged_once_with_the_stack_and_its_length(
        self, monkeypatch
    ):
        recorder = _Recorder()
        monkeypatch.setattr(watchdog_module, "logger", recorder)
        dog = LoopWatchdog(
            asyncio.get_running_loop(), threading.get_ident(), 0.2, 0.05
        ).start()
        try:
            await asyncio.sleep(0.3)
            assert recorder.warnings == []

            def hold_the_loop() -> None:
                time.sleep(0.8)

            hold_the_loop()
            await asyncio.sleep(0.3)
        finally:
            dog.stop()
        stalled, cleared = recorder.warnings
        assert "Event loop stalled" in stalled
        assert "hold_the_loop" in stalled
        assert cleared.startswith("Event loop stall cleared after")
        assert dog.stalls == 1

    async def test_zero_turns_it_off(self, monkeypatch):
        monkeypatch.setattr(
            watchdog_module,
            "env",
            lambda name, default="": "0" if name == "LOOP_STALL_WARN_SECONDS" else "",
        )
        assert watchdog_module.start_loop_watchdog() is None
