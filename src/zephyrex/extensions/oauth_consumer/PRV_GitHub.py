# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sign in with GitHub (or GitHub Enterprise Server), a plain OAuth 2.0
provider: GitHub issues no ID token, so its user API names the account,
read over TLS with the access token the PKCE-bound exchange returned.

The account is the user's numeric id, which never changes (a login can be
renamed). The email is the user's primary address, verified only when
GitHub says so in ``/user/emails``; the profile's public email is never
used, since the user sets it freely."""

from typing import Any, ClassVar, Dict, Optional, Tuple

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

GITHUB_WEB = "https://github.com"
GITHUB_API = "https://api.github.com"
DEFAULT_SCOPES = "read:user user:email"


def _primary_email(entries: Any) -> Tuple[Optional[str], bool]:
    """The primary address and whether GitHub verified it."""
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, dict) and entry.get("primary") is True:
            return text(entry.get("email")), entry.get("verified") is True
    return None, False


class PRV_GitHub(AbstractIdentityProvider):
    name: ClassVar[str] = "oauth_github"
    public_name: ClassVar[str] = "github"
    friendly_name: ClassVar[str] = "GitHub"
    description: ClassVar[str] = "Sign in with GitHub"
    default_scopes: ClassVar[str] = DEFAULT_SCOPES
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        *client_settings("GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET", DEFAULT_SCOPES),
        InstanceSetting("web_url", "GitHub's web host", default=GITHUB_WEB),
        InstanceSetting("api_url", "GitHub's REST API", default=GITHUB_API),
    )

    @classmethod
    def _base(cls, instance: ProviderInstanceModel, key: str, default: str) -> str:
        return (cls.setting(instance, key) or default).rstrip("/")

    @classmethod
    async def endpoints(cls, instance: ProviderInstanceModel) -> Endpoints:
        web = cls._base(instance, "web_url", GITHUB_WEB)
        return Endpoints(
            authorize=f"{web}/login/oauth/authorize",
            token=f"{web}/login/oauth/access_token",
            userinfo=f"{cls._base(instance, 'api_url', GITHUB_API)}/user",
        )

    @classmethod
    async def identity(
        cls,
        instance: ProviderInstanceModel,
        endpoints: Endpoints,
        tokens: TokenSet,
        nonce: str,
    ) -> Identity:
        user_url = endpoints.userinfo or f"{GITHUB_API}/user"
        user: Dict[str, Any] = await cls.api_json(
            user_url, tokens.access_token, "user API"
        )
        if not isinstance(user, dict) or not isinstance(user.get("id"), int):
            raise InvalidInputExternalError(
                "GitHub: the user API named no account", provider=cls.name
            )
        email, verified = _primary_email(
            await cls.api_json(f"{user_url}/emails", tokens.access_token, "user API")
        )
        name = text(user.get("name"))
        first, _, last = (name or "").partition(" ")
        return Identity(
            subject=str(user["id"]),
            email=email.strip().lower() if email else None,
            email_verified=email is not None and verified,
            first_name=text(first),
            last_name=text(last),
            display_name=name or text(user.get("login")),
            username=text(user.get("login")),
            picture=text(user.get("avatar_url")),
        )
