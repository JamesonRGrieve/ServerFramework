# SPDX-License-Identifier: AGPL-3.0-or-later
"""Meta's Graph APIs (Facebook, Instagram, WhatsApp, Messenger, Threads),
through a provider's HTTP client.

Meta answers a refused or expired token with HTTP 400 and error code 190
(OAuthException), not 401; it is typed here as an ``AuthExternalError``
so the rotation marks the instance unhealthy and moves on.
"""

import json
from typing import Any, Dict, Optional, Protocol

from zephyrex.extensions.ExternalErrors import (
    AuthExternalError,
    InvalidInputExternalError,
)

# v26.0 was released 2026-07-29; Meta keeps a version about two years.
META_GRAPH_VERSION = "v26.0"
FACEBOOK_GRAPH = f"https://graph.facebook.com/{META_GRAPH_VERSION}"
THREADS_GRAPH = "https://graph.threads.net/v1.0"
_INVALID_TOKEN = 190


class GraphCaller(Protocol):
    """A provider class: its name and its HTTP client."""

    name: str

    @classmethod
    def http(cls) -> Any: ...


def graph_error_code(payload: Any) -> Optional[int]:
    """The ``error.code`` of a Graph API error body, None when absent."""
    try:
        code = json.loads(str(payload))["error"]["code"]
    except (ValueError, KeyError, TypeError):
        return None
    return int(code) if isinstance(code, int) else None


async def meta_graph(
    provider: GraphCaller,
    method: str,
    path: str,
    token: str,
    *,
    base: str = FACEBOOK_GRAPH,
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """``method`` ``base/path`` with ``token``: the decoded answer."""
    try:
        answer: Dict[str, Any] = await provider.http().request(
            method,
            f"{base}/{path}",
            params=params,
            json=json_body,
            headers={"Authorization": f"Bearer {token}"},
        )
    except InvalidInputExternalError as exc:
        if graph_error_code(exc.upstream_payload) == _INVALID_TOKEN:
            raise AuthExternalError(
                "Meta refused the access token",
                provider=provider.name,
                upstream_status=exc.upstream_status,
            ) from exc
        raise
    return answer
