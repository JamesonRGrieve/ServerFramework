# SPDX-License-Identifier: AGPL-3.0-or-later
"""Keep synchronous manager work off the event loop.

A worker serves every request on one event loop. A synchronous database,
HTTP or password-hashing call made on it holds every other request on that
worker until it returns. The routes the framework generates therefore await
a coroutine method on the loop and run any other method on a worker thread
(``starlette.concurrency.run_in_threadpool``, which carries the request's
context variables into the thread).
"""

import inspect
from typing import Any, Callable

from starlette.concurrency import run_in_threadpool


def runs_on_loop(func: Callable[..., Any]) -> bool:
    """Whether ``func`` is a coroutine function, looking beneath a manager's
    hook wrapper (which keeps the authored method on ``_original_method``)."""
    return inspect.iscoroutinefunction(getattr(func, "_original_method", func))


async def call_off_loop(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Await a coroutine function; run any other callable on a worker thread.
    An awaitable a synchronous callable returns is awaited on the loop."""
    if runs_on_loop(func):
        result = func(*args, **kwargs)
    else:
        result = await run_in_threadpool(func, *args, **kwargs)
    if inspect.isawaitable(result):
        result = await result
    return result
