# SPDX-License-Identifier: AGPL-3.0-or-later
"""Login with Amazon, a plain OAuth 2.0 provider: it issues no ID token, so
its profile API names the account (``user_id``), read over TLS with the
access token the PKCE-bound exchange returned.

Amazon does not say whether a profile's email is verified, so it never
counts as verified: an Amazon identity signs in to an account it was linked
to, but does not create an account or join one by email. (An Amazon
Cognito user pool is an OpenID provider: configure it as ``oidc`` with the
pool's issuer.)"""

from typing import Any, ClassVar, Dict, Tuple

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.ExternalErrors import InvalidInputExternalError
from zephyrex.extensions.oauth_consumer.IdentityProvider import (
    AbstractIdentityProvider,
    Endpoints,
    Identity,
    TokenSet,
    client_settings,
    text,
)
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

AMAZON_AUTHORIZE = "https://www.amazon.com/ap/oa"
AMAZON_API = "https://api.amazon.com"
DEFAULT_SCOPES = "profile"


class PRV_Amazon(AbstractIdentityProvider):
    name: ClassVar[str] = "oauth_amazon"
    public_name: ClassVar[str] = "amazon"
    friendly_name: ClassVar[str] = "Amazon"
    description: ClassVar[str] = "Login with Amazon"
    default_scopes: ClassVar[str] = DEFAULT_SCOPES
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        *client_settings("AMAZON_CLIENT_ID", "AMAZON_CLIENT_SECRET", DEFAULT_SCOPES),
        InstanceSetting(
            "authorize_url", "The authorization endpoint", default=AMAZON_AUTHORIZE
        ),
        InstanceSetting("api_url", "The token and profile API", default=AMAZON_API),
    )

    @classmethod
    async def endpoints(cls, instance: ProviderInstanceModel) -> Endpoints:
        api = (cls.setting(instance, "api_url") or AMAZON_API).rstrip("/")
        return Endpoints(
            authorize=cls.setting(instance, "authorize_url") or AMAZON_AUTHORIZE,
            token=f"{api}/auth/o2/token",
            userinfo=f"{api}/user/profile",
        )

    @classmethod
    async def identity(
        cls,
        instance: ProviderInstanceModel,
        endpoints: Endpoints,
        tokens: TokenSet,
        nonce: str,
    ) -> Identity:
        url = endpoints.userinfo or f"{AMAZON_API}/user/profile"
        profile: Dict[str, Any] = await cls.api_json(
            url, tokens.access_token, "profile API"
        )
        subject = text(profile.get("user_id")) if isinstance(profile, dict) else None
        if subject is None:
            raise InvalidInputExternalError(
                "Amazon: the profile API named no account", provider=cls.name
            )
        email = text(profile.get("email"))
        name = text(profile.get("name"))
        first, _, last = (name or "").partition(" ")
        return Identity(
            subject=subject,
            email=email.strip().lower() if email else None,
            email_verified=False,
            first_name=text(first),
            last_name=text(last),
            display_name=name,
        )
