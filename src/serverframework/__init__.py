"""Public façade for the ServerFramework package.

This module exposes a small, stable surface so consumers can write a thin
``main.py`` like::

    from serverframework import run

    run(extensions="payment,auth_mfa", extensions_path="./my_extensions")

without depending on the layout of the internal ``lib``, ``logic``,
``database`` etc. packages. The façade is intentionally additive: it does
not replace ``app.py`` and does not touch any existing call site, so the
historical ``python app.py`` entry point keeps working unchanged.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Union

# Ensure the framework's top-level packages (``lib``, ``logic``, …) are
# importable when this façade is consumed. The legacy layout puts those
# packages directly under ``src/`` rather than under a single top-level
# package; until that rename happens, we add ``src/`` to ``sys.path`` here.
_THIS_DIR = Path(__file__).resolve().parent
_SRC_DIR = _THIS_DIR.parent
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))


# Re-exports. Importing ``app`` triggers ``lib.Logging`` etc., so callers
# need their environment configured before importing this module.
from app import build_app, instance  # noqa: E402

__all__ = [
    "build_app",
    "instance",
    "run",
    "set_extensions_root",
]


def set_extensions_root(path: Optional[Union[str, os.PathLike]]) -> None:
    """Configure the global extensions root.

    Equivalent to ``lib.Paths.set_extensions_root``; re-exported here so
    consumers don't need to know about internal modules.
    """
    from lib.Paths import set_extensions_root as _set_root

    _set_root(path)


def run(
    extensions: Optional[str] = None,
    extensions_path: Optional[Union[str, os.PathLike]] = None,
    host: str = "0.0.0.0",
    port: int = 1996,
    workers: Optional[int] = None,
    reload: Optional[bool] = None,
    log_level: Optional[str] = None,
    proxy_headers: bool = True,
    env_overrides: Optional[Dict[str, str]] = None,
    **uvicorn_kwargs: Any,
) -> None:
    """Boot the framework with uvicorn.

    This is the importable equivalent of running ``python app.py``: it
    pulls configuration from the same environment variables (``APP_NAME``,
    ``UVICORN_*``, ``LOG_LEVEL``, …) and ultimately calls
    ``uvicorn.run("app:instance", factory=True, …)``.

    Args:
        extensions: CSV string of extensions to load. If provided, sets
            ``APP_EXTENSIONS`` for this process.
        extensions_path: Filesystem location to discover extensions from.
            If provided, registered globally via ``set_extensions_root``.
        host, port, workers, reload, log_level, proxy_headers: Standard
            uvicorn knobs. ``None`` means "fall back to the value the
            existing ``app.py`` would have used".
        env_overrides: Additional environment variables to set before
            booting. Useful for one-shot configuration.
        **uvicorn_kwargs: Forwarded verbatim to ``uvicorn.run``.
    """
    if env_overrides:
        for key, value in env_overrides.items():
            os.environ[key] = str(value)

    if extensions is not None:
        os.environ["APP_EXTENSIONS"] = extensions

    if extensions_path is not None:
        set_extensions_root(extensions_path)

    # Defer heavy imports until after env vars are in place: ``lib.Environment``
    # snapshots configuration at import time in places.
    import uvicorn

    from lib.Environment import env

    if workers is None:
        workers_str = env("UVICORN_WORKERS")
        workers = int(workers_str) if workers_str.isnumeric() else 1

    if log_level is None:
        env_log_level = env("LOG_LEVEL").lower()
        log_level = (
            env_log_level
            if env_log_level in {"info", "debug", "warning", "error", "critical"}
            else "info"
        )

    if reload is None:
        reload = env("UVICORN_RELOAD").lower() == "true"

    uvicorn.run(
        "app:instance",
        host=host,
        port=port,
        workers=workers,
        log_level=log_level,
        proxy_headers=proxy_headers,
        reload=reload,
        factory=True,
        **uvicorn_kwargs,
    )
