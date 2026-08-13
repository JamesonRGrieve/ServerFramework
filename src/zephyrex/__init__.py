"""Public façade for the ZephyrexFrameworkServer package.

This module exposes a small, stable surface so consumers can write a thin
``main.py`` like::

    from zephyrex import run

    run(extensions="payment,auth_mfa", extensions_path="./my_extensions")

without depending on the layout of the internal ``lib``, ``logic``,
``database`` etc. packages. The façade is intentionally additive: it does
not replace ``app.py`` and does not touch any existing call site, so the
historical ``python app.py`` entry point keeps working unchanged.

Public-vs-internal contract (Item 68)
-------------------------------------
**Stable surface.** ``__all__`` (defined below) enumerates the symbols
that this package commits to as part of the documented public API.
Anything in ``__all__`` is covered by the framework's compatibility
guarantees — its signature and observable behavior will not change in
breaking ways within a major version.

**Internal modules.** Importing any symbol *not* in ``__all__`` —
including but not limited to ``zephyrex.lib``,
``zephyrex.logic``, ``zephyrex.database``,
``zephyrex.endpoints``, ``zephyrex.extensions``,
``zephyrex.sdk`` — is **at the consumer's own risk**. Those
modules are framework internals; their layout, naming, and signatures
may change between versions without prior notice. If you need a
primitive that is currently only reachable via an internal module,
open an issue against the framework so the primitive can be promoted
to the public surface (and to ``EXT.Contracts.md``, see Item 52)
rather than depending on the internal path.

**Type re-exports.** Type hints against framework-defined Pydantic
models (``UserModel.Create``, ``SessionModel``, …) currently require
importing from internal modules; a future ``zephyrex.types``
re-export module is on the roadmap (Item 68 follow-up). Until that
ships, type-hint imports from internal modules carry the same
"at your own risk" caveat as runtime imports.

**CI enforcement.** The framework's CI cross-checks ``__all__`` against
``EXT.Contracts.md`` (Item 52, when populated): a symbol is in
``zephyrex.__all__`` if and only if it has a corresponding
contract entry. Drift in either direction fails the build, so adding a
new public symbol forces a matching documented contract.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Union

# Item 66 — the sys.path mutation that bridged the legacy top-level
# package layout (`lib/`, `logic/`, ...) is gone now that Item 60
# moved every package under ``zephyrex.*``. Imports resolve
# through the standard package machinery; consumers can run the
# framework from a zipapp or vendor it without surprise.

# Re-exports. Importing ``app`` triggers ``lib.Logging`` etc., so callers
# need their environment configured before importing this module.
from zephyrex.app import build_app, instance  # noqa: E402

__all__ = [
    "build_app",
    "get_framework_version",
    "instance",
    "run",
    "set_extensions_root",
]


def get_framework_version() -> str:
    """Return the installed framework version from distribution metadata.

    Tries the ``zephyrex`` distribution first, then the legacy ``server``
    name, and falls back to ``"0.0.0"`` when neither is installed (e.g.
    running directly from a source checkout without ``pip install``).
    """
    try:
        from importlib.metadata import PackageNotFoundError, version as _pkg_version

        for _dist_name in ("zephyrex", "server"):
            try:
                return _pkg_version(_dist_name)
            except PackageNotFoundError:
                continue
    except ImportError:
        pass
    return "0.0.0"


def set_extensions_root(path: Optional[Union[str, os.PathLike]]) -> None:
    """Configure the global extensions root.

    Equivalent to ``lib.Paths.set_extensions_root``; re-exported here so
    consumers don't need to know about internal modules.
    """
    from zephyrex.lib.Paths import set_extensions_root as _set_root

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
        os.environ["EXTENSIONS_PATH"] = str(extensions_path)
        set_extensions_root(extensions_path)

    # Refresh the cached settings singleton so env() picks up the values
    # we just set (APP_EXTENSIONS, EXTENSIONS_PATH, any env_overrides).
    from zephyrex.lib.Environment import refresh_settings

    refresh_settings()

    import uvicorn

    from zephyrex.lib.Environment import env

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
        "zephyrex.app:instance",
        host=host,
        port=port,
        workers=workers,
        log_level=log_level,
        proxy_headers=proxy_headers,
        reload=reload,
        factory=True,
        **uvicorn_kwargs,
    )
