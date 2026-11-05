# SPDX-License-Identifier: AGPL-3.0-or-later
"""This server as an OAuth 2.0 authorization server and OpenID Connect
provider (see ``AuthorizationServer`` for the protocol and
``BLL_OAuthProtocol`` for its routes).

Third-party applications register as clients, send users here to sign in
and consent, and receive single-use codes they exchange (with PKCE) for
short-lived access tokens, rotating refresh tokens and signed ID tokens.

Abilities act for the user named by ``requester_id``: registering a
client they own, and listing or revoking the consents they gave. The
operator (ROOT) rotates the signing keys.

The client side ("sign in with ...") is the ``oauth_consumer`` extension.
"""

from typing import Any, ClassVar, Dict, List, Optional, Set

from fastapi import HTTPException

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
    ability,
)
from zephyrex.extensions.oauth_provider import Config
from zephyrex.extensions.oauth_provider.AuthorizationServer import SigningKeys
from zephyrex.extensions.oauth_provider.BLL_OAuthProvider import (
    ClientRegistration,
    OauthClientManager,
    OauthGrantManager,
)
from zephyrex.extensions.oauth_provider.OAuthProtocol import APPLICATION_WEB
from zephyrex.lib.Dependencies import Dependencies, EXT_Dependency
from zephyrex.lib.SecretEncryption import (
    MissingFernetKeyError,
    assert_encryption_available,
)
from zephyrex.logic.AbstractLogicManager.ownership import server_side


class EXT_OAuthProvider(AbstractStaticExtension):
    name: ClassVar[str] = "oauth_provider"
    version: ClassVar[str] = "2.0.0"
    description: ClassVar[str] = (
        "OAuth 2.0 authorization server and OpenID Connect provider "
        "(authorization code flow with PKCE, RFC 9700)"
    )

    _env: ClassVar[Dict[str, Any]] = dict(Config.DEFAULTS)
    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            EXT_Dependency(
                name="auth_session",
                friendly_name="Sessions",
                reason="Consent needs a signed-in session, and when it began "
                "(the ID token's auth_time)",
            ),
        ]
    )
    _abilities: ClassVar[Set[str]] = {
        "oauth_provider_register_client",
        "oauth_provider_list_grants",
        "oauth_provider_revoke_grant",
        "oauth_provider_rotate_signing_keys",
    }

    @classmethod
    def validate_config(cls) -> List[str]:
        issues: List[str] = []
        for setting, getter in (
            (Config.ISSUER, Config.issuer),
            (Config.CONSENT_URL, Config.consent_url),
        ):
            try:
                getter()
            except HTTPException as error:
                issues.append(f"{setting}: {error.detail}")
        try:
            assert_encryption_available()
        except MissingFernetKeyError as error:
            issues.append(f"signing keys cannot be stored: {error}")
        return issues

    @classmethod
    @ability("oauth_provider_register_client")
    async def register_client(
        cls,
        requester_id: str,
        name: str,
        redirect_uris: List[str],
        is_confidential: bool = True,
        application_type: str = APPLICATION_WEB,
        allowed_scopes: Optional[List[str]] = None,
        token_endpoint_auth_method: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Register a client the user owns. Its secret is in the answer, and
        nowhere else, ever."""
        manager: OauthClientManager = cls.as_requester(OauthClientManager, requester_id)
        registered = manager.register_client(
            ClientRegistration(
                name=name,
                redirect_uris=redirect_uris,
                is_confidential=is_confidential,
                application_type=application_type,
                allowed_scopes=allowed_scopes or [],
                token_endpoint_auth_method=token_endpoint_auth_method,
            )
        )
        return registered.model_dump(mode="json")

    @classmethod
    @ability("oauth_provider_list_grants")
    async def list_grants(cls, requester_id: str) -> List[Dict[str, Any]]:
        """The clients the user consented to, and to which scopes."""
        manager: OauthGrantManager = cls.as_requester(OauthGrantManager, requester_id)
        return [grant.model_dump(mode="json") for grant in manager.list()]

    @classmethod
    @ability("oauth_provider_revoke_grant")
    async def revoke_grant(cls, requester_id: str, grant_id: str) -> Dict[str, Any]:
        """Withdraw a consent; the client's tokens for the user stop working."""
        manager: OauthGrantManager = cls.as_requester(OauthGrantManager, requester_id)
        manager.delete(grant_id)
        return {"grant_id": grant_id, "revoked": True}

    @classmethod
    @ability("oauth_provider_rotate_signing_keys")
    async def rotate_signing_keys(cls, requester_id: str) -> Dict[str, Any]:
        """New ID-token signing keys (operator only); the old ones stay
        published until tokens they signed have expired."""
        manager: OauthClientManager = cls.as_requester(OauthClientManager, requester_id)
        if not server_side(requester_id):
            raise HTTPException(
                status_code=403, detail="Only the operator rotates keys"
            )
        return {"kids": SigningKeys(manager.model_registry).rotate()}
