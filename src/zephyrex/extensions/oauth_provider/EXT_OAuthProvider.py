"""OAuth2 provider extension manifest.

This server *issues* OAuth2 credentials. Third-party apps register clients,
redirect users here for consent, exchange auth codes for tokens, and call
back into the API as the user with bearer tokens.

Scopes draw from the canonical ``PermissionRegistry`` so a permission and an
OAuth scope are the same name.

The complementary ``oauth_consumer`` extension implements the *client* side
("login with Google/GitHub/...").
"""

from typing import Any, ClassVar, Dict, List, Set

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension
from zephyrex.lib.Dependencies import Dependencies
from zephyrex.lib.Logging import logger


class EXT_OAuthProvider(AbstractStaticExtension):
    name: ClassVar[str] = "oauth_provider"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Run this server as an OAuth2 issuer (auth code, refresh token grants)."
    )

    _env: ClassVar[Dict[str, Any]] = {
        "OAUTH_PROVIDER_ACCESS_TTL_MINUTES": "60",
        "OAUTH_PROVIDER_REFRESH_TTL_DAYS": "30",
        "OAUTH_PROVIDER_CODE_TTL_MINUTES": "10",
        # PKCE policy. Public clients always require PKCE; confidential
        # clients require PKCE by default (set 'false' to relax).
        "OAUTH_PROVIDER_REQUIRE_PKCE_FOR_CONFIDENTIAL": "true",
    }

    dependencies: ClassVar[Dependencies] = Dependencies([])

    _abilities: ClassVar[Set[str]] = {
        "oauth_provider_register_client",
        "oauth_provider_authorize",
        "oauth_provider_token",
        "oauth_provider_introspect",
        "oauth_provider_revoke",
    }
    _providers: ClassVar[List] = []
    extension_dependencies: ClassVar[List[str]] = ["auth_session"]

    @classmethod
    def on_initialize(cls) -> bool:
        from zephyrex.extensions.oauth_provider import (  # noqa: F401
            BLL_OAuthProvider,
        )

        logger.debug("oauth_provider initialized")
        return True
