# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sign in with Google, an OpenID provider at ``https://accounts.google.com``.

Google signs ID tokens naming either form of its issuer identifier, and
asserts ``email_verified``. ``offline`` asks for a refresh token (with
consent), for a deployment whose scopes reach Google APIs on the user's
behalf."""

from typing import Any, ClassVar, Dict, List, Tuple

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.oauth_consumer.IdentityProvider import (
    AbstractOIDCProvider,
    Endpoints,
    asserted_true,
    client_settings,
    issuer_setting,
)
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

GOOGLE_ISSUER = "https://accounts.google.com"
# The issuer identifier's other form, which Google's ID tokens may carry.
GOOGLE_ISSUER_HOST = "accounts.google.com"
DEFAULT_SCOPES = "openid email profile"


class PRV_Google(AbstractOIDCProvider):
    name: ClassVar[str] = "oauth_google"
    public_name: ClassVar[str] = "google"
    friendly_name: ClassVar[str] = "Google"
    description: ClassVar[str] = "Sign in with Google"
    default_scopes: ClassVar[str] = DEFAULT_SCOPES
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        *client_settings("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", DEFAULT_SCOPES),
        issuer_setting(None, GOOGLE_ISSUER),
        InstanceSetting(
            "offline",
            "true to ask for a refresh token (access_type=offline, with consent)",
            default="false",
        ),
    )

    @classmethod
    def authorize_params(cls, instance: ProviderInstanceModel) -> Dict[str, str]:
        if asserted_true(cls.setting(instance, "offline")):
            return {"access_type": "offline", "prompt": "consent"}
        return {}

    @classmethod
    def accepted_issuers(
        cls,
        instance: ProviderInstanceModel,
        endpoints: Endpoints,
        claims: Dict[str, Any],
    ) -> List[str]:
        accepted = super().accepted_issuers(instance, endpoints, claims)
        if endpoints.issuer == GOOGLE_ISSUER:
            accepted.append(GOOGLE_ISSUER_HOST)
        return accepted
