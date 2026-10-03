# SPDX-License-Identifier: AGPL-3.0-or-later
"""SCIM 2.0 provisioning out of the framework: this server is the identity
source, and pushes its users and teams to downstream SCIM service
providers (Slack, Zoom, GitHub Enterprise, any RFC 7644 server).

Each ``scim_target`` provider instance is one service provider: its SCIM
base URL and a write-only bearer token, plus what it is given (a scope
team, whether teams go as groups, ``deactivate`` or ``delete`` for a
removed user, and whether ``userName`` is the email or the username).

Framework changes reach every target on their own: hooks flag the change
(``BLL_SCIMProvider``) and a push runs on the app's event loop. A target
can also be pushed or fully reconciled on demand. Users go as SCIM Users
(``externalId`` is the framework id), teams as Groups whose members are
the target's ids for those users (``SCIMSync``). The complementary
``scim_consumer`` extension takes identities *in* over SCIM.
"""

import asyncio
from concurrent.futures import Future
from typing import Any, ClassVar, Dict, List, Optional, Set, Tuple

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
    ability,
)
from zephyrex.lib.Dependencies import Dependencies
from zephyrex.lib.Logging import logger
from zephyrex.logic.BLL_Providers import ProviderInstanceManager


class EXT_SCIMProvider(AbstractStaticExtension):
    name: ClassVar[str] = "scim_provider"
    version: ClassVar[str] = "2.0.0"
    description: ClassVar[str] = (
        "Push the framework's users and teams to downstream SCIM 2.0 service providers"
    )

    _env: ClassVar[Dict[str, Any]] = {}
    dependencies: ClassVar[Dependencies] = Dependencies([])
    _abilities: ClassVar[Set[str]] = {
        "list_scim_targets",
        "push_scim_target",
        "reconcile_scim_target",
    }

    # The app's event loop, while it runs: where hooks start their pushes.
    _loop: ClassVar[Optional[asyncio.AbstractEventLoop]] = None
    _pushes: ClassVar[Set["Future[Any]"]] = set()
    # One run at a time per target, on each loop.
    _locks: ClassVar[Dict[Tuple[int, str], asyncio.Lock]] = {}

    @classmethod
    def on_start(cls) -> None:
        try:
            cls._loop = asyncio.get_running_loop()
        except RuntimeError:
            cls._loop = None

    @classmethod
    def on_stop(cls) -> None:
        cls._loop = None

    @classmethod
    def _lock(cls, target_id: str) -> asyncio.Lock:
        key = (id(asyncio.get_running_loop()), target_id)
        lock = cls._locks.get(key)
        if lock is None:
            lock = cls._locks[key] = asyncio.Lock()
        return lock

    @classmethod
    async def push(cls, registry: Any, target: str) -> Dict[str, Any]:
        """Push ``target``'s pending links (its id or name)."""
        from zephyrex.extensions.scim_provider.SCIMSync import SCIMSync, target_instance

        instance = target_instance(registry, target)
        async with cls._lock(str(instance.id)):
            run = await SCIMSync.open(registry, instance)
            return (await run.drain()).as_dict()

    @classmethod
    async def reconcile(cls, registry: Any, target: str) -> Dict[str, Any]:
        """Bring ``target`` (its id or name) fully in line."""
        from zephyrex.extensions.scim_provider.SCIMSync import SCIMSync, target_instance

        instance = target_instance(registry, target)
        async with cls._lock(str(instance.id)):
            run = await SCIMSync.open(registry, instance)
            return (await run.reconcile()).as_dict()

    @classmethod
    def schedule(cls, registry: Any, targets: List[str]) -> None:
        """Start a push of each target on the app's loop. Without a running
        app the flags wait for the next push or reconcile."""
        loop = cls._loop
        if loop is None or loop.is_closed():
            return
        for target in targets:
            future = asyncio.run_coroutine_threadsafe(
                cls._pushed(registry, target), loop
            )
            cls._pushes.add(future)
            future.add_done_callback(cls._pushes.discard)

    @classmethod
    async def _pushed(cls, registry: Any, target: str) -> None:
        try:
            await cls.push(registry, target)
        except Exception:
            logger.exception("SCIM push to %s failed; it stays pending", target)

    @classmethod
    def _visible_target(cls, requester_id: str, target: str) -> Any:
        """The registry, once ``requester_id`` is shown to see ``target``."""
        from zephyrex.extensions.scim_provider.SCIMSync import target_instance

        instances = cls.as_requester(ProviderInstanceManager, requester_id)
        instance = target_instance(instances.model_registry, target)
        instances.get(id=instance.id)
        return instances.model_registry

    @classmethod
    @ability("list_scim_targets")
    async def list_scim_targets(cls) -> List[Dict[str, Any]]:
        """The configured SCIM targets: id, name and provider."""
        return cls.instances_of()

    @classmethod
    @ability("push_scim_target")
    async def push_scim_target(cls, requester_id: str, target: str) -> Dict[str, Any]:
        """Push the changes waiting for a target the requester can see."""
        return await cls.push(cls._visible_target(requester_id, target), target)

    @classmethod
    @ability("reconcile_scim_target")
    async def reconcile_scim_target(
        cls, requester_id: str, target: str
    ) -> Dict[str, Any]:
        """Bring a target the requester can see fully in line with the
        framework's users and teams."""
        return await cls.reconcile(cls._visible_target(requester_id, target), target)
