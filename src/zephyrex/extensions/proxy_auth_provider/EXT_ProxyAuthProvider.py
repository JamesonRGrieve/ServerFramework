# SPDX-License-Identifier: AGPL-3.0-or-later
"""This server as an authenticating reverse proxy for the services the
operator declares (``BLL_ProxyAuthProvider``): a signed-in user reaches
them through ``/v1/proxy/<upstream>/``, and they receive trusted, optionally
signed identity headers instead of the user's credentials.

Behind a reverse proxy of your own (Traefik, nginx, Caddy), use
``forward_auth_provider`` instead: the proxy forwards, and this server only
decides. ``proxy_auth_consumer`` is the other direction (trusting an
upstream proxy's headers).
"""

from typing import Any, ClassVar, Dict, Set

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension
from zephyrex.lib.Dependencies import Dependencies, EXT_Dependency


class EXT_ProxyAuthProvider(AbstractStaticExtension):
    name: ClassVar[str] = "proxy_auth_provider"
    version: ClassVar[str] = "2.0.0"
    description: ClassVar[str] = (
        "Proxy signed-in users to declared upstreams with trusted identity headers"
    )

    _env: ClassVar[Dict[str, Any]] = {}
    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            EXT_Dependency(
                name="auth_session",
                friendly_name="Sessions",
                reason="A revoked session stops reaching the upstreams too",
                optional=False,
            )
        ]
    )
    _abilities: ClassVar[Set[str]] = set()
