# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sign in with another identity provider: OAuth 2.0 and OpenID Connect.

One sign-in engine for every provider. Google, Microsoft, GitHub, Amazon
and Forgejo are named providers; any other OpenID provider (Keycloak,
Auth0, Okta, Entra ID, Cognito…) is the ``oidc`` provider, configured by
its issuer. Each provider instance is one client registration; its client
secret is a write-only setting. The flow, the identities and the routes
live in ``BLL_OAuthConsumer``; the protocol in ``IdentityProvider``.

The complementary ``oauth_provider`` extension is the server side: other
applications signing in against this server.
"""

from typing import Any, ClassVar, Dict, List, Set

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
    ability,
)
from zephyrex.lib.Dependencies import Dependencies, EXT_Dependency
from zephyrex.lib.Logging import logger


class EXT_OAuthConsumer(AbstractStaticExtension):
    name: ClassVar[str] = "oauth_consumer"
    version: ClassVar[str] = "2.0.0"
    description: ClassVar[str] = (
        "Sign in with Google, Microsoft, GitHub, Amazon, Forgejo or any "
        "OpenID Connect provider (OAuth 2.0 with PKCE)"
    )

    _env: ClassVar[Dict[str, Any]] = {"OAUTH_CONSUMER_REDIRECT_URIS": ""}

    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            EXT_Dependency(
                name="auth_session",
                friendly_name="Sessions",
                reason="A sign-in issues a persisted session",
            ),
            EXT_Dependency(
                name="auth_invitations",
                friendly_name="Invitations",
                optional=True,
                reason="REGISTRATION_MODE=invite admits a new user by invitation",
            ),
        ]
    )

    _abilities: ClassVar[Set[str]] = {
        "list_oauth_providers",
        "list_oauth_identities",
        "oauth_access_token",
    }

    @classmethod
    def on_initialize(cls) -> bool:
        """Provider tokens and PKCE verifiers are stored encrypted: without
        an encryption key the extension refuses to run."""
        from zephyrex.lib.SecretEncryption import (
            MissingFernetKeyError,
            assert_encryption_available,
        )

        try:
            assert_encryption_available()
        except MissingFernetKeyError as exc:
            logger.error("oauth_consumer refusing to initialize: %s", exc)
            return False
        from zephyrex.extensions.oauth_consumer.BLL_OAuthConsumer import (
            register_merge_participation,
        )

        register_merge_participation()
        return True

    @classmethod
    def validate_config(cls) -> List[str]:
        from zephyrex.lib.SecretEncryption import (
            MissingFernetKeyError,
            assert_encryption_available,
        )

        try:
            assert_encryption_available()
        except MissingFernetKeyError as exc:
            return [str(exc)]
        return []

    @classmethod
    @ability("list_oauth_providers")
    async def list_oauth_providers(cls) -> List[Dict[str, Any]]:
        """The providers users can sign in with: the name to pass as
        ``provider``, the provider, its display name and kind."""
        from zephyrex.extensions.oauth_consumer.BLL_OAuthConsumer import (
            OAuthConsumerManager,
        )
        from zephyrex.lib.Environment import env

        manager = cls.as_requester(OAuthConsumerManager, env("ROOT_ID"))
        listed = manager.providers_list()
        return [entry.model_dump() for entry in listed.providers]

    @classmethod
    @ability("list_oauth_identities")
    async def list_oauth_identities(cls, requester_id: str) -> List[Dict[str, Any]]:
        """The requester's identities at providers (no tokens)."""
        from zephyrex.extensions.oauth_consumer.BLL_OAuthConsumer import (
            OAuthIdentityManager,
            OAuthIdentityView,
        )

        manager = cls.as_requester(OAuthIdentityManager, requester_id)
        return [
            OAuthIdentityView.of(identity).model_dump(mode="json")
            for identity in manager.list() or []
        ]

    @classmethod
    @ability("oauth_access_token")
    async def oauth_access_token(
        cls, requester_id: str, identity_id: str
    ) -> Dict[str, Any]:
        """A current access token at the provider for one of the
        requester's identities, refreshed when it lapsed."""
        from zephyrex.extensions.oauth_consumer.BLL_OAuthConsumer import (
            OAuthIdentityManager,
            current_access_token,
        )

        manager = cls.as_requester(OAuthIdentityManager, requester_id)
        token = await current_access_token(manager, identity_id)
        return token.model_dump(mode="json")


async def delegated_access_token(
    user_id: str,
    identity_provider: str,
    required_scope: str,
    service: str,
    model_registry: Any,
) -> str:
    """A current access token at ``identity_provider`` (e.g. ``google``)
    for ``user_id``, from the most recently used of their linked identities
    there whose sign-in granted ``required_scope``: for extensions reaching a
    user's own data at a service (their Drive, their calendar). 409 when
    they never signed in with that provider; 403 when no sign-in granted
    the scope (sign in again to grant it). ``service`` names it in the
    message. Someone may have linked several accounts: the most recently
    used one that grants it is chosen."""
    from fastapi import HTTPException

    if "oauth_consumer" not in model_registry.loaded_extension_names():
        raise HTTPException(
            status_code=409, detail=f"{service} needs sign-in through oauth_consumer"
        )
    identities = [
        i
        for i in await EXT_OAuthConsumer.list_oauth_identities(user_id)
        if i["provider"] == identity_provider
    ]
    if not identities:
        raise HTTPException(
            status_code=409,
            detail=f"Sign in with {identity_provider} to reach your {service}",
        )
    identities.sort(key=lambda i: i.get("last_login_at") or "", reverse=True)
    for identity in identities:
        token = await EXT_OAuthConsumer.oauth_access_token(user_id, identity["id"])
        if required_scope in (token.get("scopes") or "").split():
            return str(token["access_token"])
    raise HTTPException(
        status_code=403,
        detail=f"Your sign-in does not allow reading your {service}; sign in again to allow it",
    )
