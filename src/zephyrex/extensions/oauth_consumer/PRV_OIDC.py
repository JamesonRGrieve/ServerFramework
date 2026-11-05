# SPDX-License-Identifier: AGPL-3.0-or-later
"""Any OpenID Connect provider, configured by its issuer: Keycloak, Auth0,
Okta, Entra ID, Authentik, Amazon Cognito
(``https://cognito-idp.<region>.amazonaws.com/<pool-id>``), Zitadel… Its
endpoints and keys come from the issuer's discovery document."""

from typing import ClassVar, Tuple

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.oauth_consumer.IdentityProvider import (
    AbstractOIDCProvider,
    client_settings,
    issuer_setting,
)

DEFAULT_SCOPES = "openid email profile"


class PRV_OIDC(AbstractOIDCProvider):
    name: ClassVar[str] = "oidc"
    public_name: ClassVar[str] = "oidc"
    friendly_name: ClassVar[str] = "OpenID Connect"
    description: ClassVar[str] = "Any OpenID Connect provider, found by its issuer"
    default_scopes: ClassVar[str] = DEFAULT_SCOPES
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        *client_settings(
            "OIDC_CONSUMER_CLIENT_ID", "OIDC_CONSUMER_CLIENT_SECRET", DEFAULT_SCOPES
        ),
        issuer_setting("OIDC_CONSUMER_ISSUER_URL"),
    )
