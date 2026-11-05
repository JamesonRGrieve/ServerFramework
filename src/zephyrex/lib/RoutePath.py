# SPDX-License-Identifier: AGPL-3.0-or-later
"""The path a request is routed on.

Served under a root path (``uvicorn --root-path /api`` behind a proxy that
forwards ``/api/...``), a request's ASGI ``path`` carries that prefix, while
routes, and the path patterns middleware matches against them, name the app's
own paths. Middleware that matches paths before routing compares
``route_path(scope)``, as Starlette's router does.
"""

from typing import Any, Mapping


    """The request's path less the root path it was served under."""
    path = str(scope.get("path", ""))
    root = str(scope.get("root_path", "")).rstrip("/")
    if root and (path == root or path.startswith(root + "/")):
        return path[len(root) :] or "/"
    return path
