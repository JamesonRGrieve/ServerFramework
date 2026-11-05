import asyncio
import inspect as _inspect
from typing import Any, Dict, Hashable, List, Optional

from .contributions import (
    DataLoaderBatchFn,
    GraphQLContributionRegistry,
    _GLOBAL_CONTRIBUTION_REGISTRY,
)


class RequestDataLoader:
    """Minimal per-request DataLoader.

    ``load(key)`` returns an awaitable that resolves once the deferred batch
    fires. Batches fire on the next event-loop tick after the first ``load``,
    so all parallel resolutions of ``thing.related`` collapse into a single
    ``batch_load_fn(keys)`` call.
    """

    def __init__(self, batch_load_fn: DataLoaderBatchFn) -> None:
        self._batch_load_fn = batch_load_fn
        self._queue: List[Hashable] = []
        self._futures: Dict[Hashable, "asyncio.Future[Any]"] = {}
        self._scheduled: bool = False

    def load(self, key: Hashable) -> "asyncio.Future[Any]":
        loop = asyncio.get_event_loop()
        if key in self._futures:
            return self._futures[key]
        fut: "asyncio.Future[Any]" = loop.create_future()
        self._futures[key] = fut
        self._queue.append(key)
        if not self._scheduled:
            self._scheduled = True
            loop.call_soon(lambda: asyncio.ensure_future(self._fire()))
        return fut

    async def _fire(self) -> None:
        keys = list(self._queue)
        self._queue.clear()
        self._scheduled = False
        try:
            result = self._batch_load_fn(keys)
            if _inspect.isawaitable(result):
                result = await result
        except Exception as e:  # noqa: BLE001
            for key in keys:
                fut = self._futures.pop(key, None)
                if fut is not None and not fut.done():
                    fut.set_exception(e)
            return
        if not isinstance(result, (list, tuple)):
            err = TypeError(
                f"DataLoader batch_load_fn must return a sequence aligned with"
                f" keys; got {type(result).__name__}."
            )
            for key in keys:
                fut = self._futures.pop(key, None)
                if fut is not None and not fut.done():
                    fut.set_exception(err)
            return
        if len(result) != len(keys):
            err = ValueError(  # type: ignore[assignment]
                f"DataLoader batch_load_fn returned {len(result)} results for"
                f" {len(keys)} keys; lengths must match."
            )
            for key in keys:
                fut = self._futures.pop(key, None)
                if fut is not None and not fut.done():
                    fut.set_exception(err)
            return
        for key, value in zip(keys, result):
            fut = self._futures.pop(key, None)
            if fut is not None and not fut.done():
                fut.set_result(value)


def build_request_dataloaders(
    registry: Optional[GraphQLContributionRegistry] = None,
) -> Dict[str, RequestDataLoader]:
    """Build a fresh per-request DataLoader for every registered spec."""
    reg = registry or _GLOBAL_CONTRIBUTION_REGISTRY
    return {
        name: RequestDataLoader(spec.batch_load_fn)
        for name, spec in reg.dataloaders().items()
    }
