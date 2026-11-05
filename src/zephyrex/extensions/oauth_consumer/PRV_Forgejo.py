# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sign in with a self-hosted Forgejo (or Gitea), an OpenID provider at
its base URL (``https://git.example.com/``). Its ``preferred_username``
fills in the local username, so a consumer such as forgejo-classroom can
match the user's Forgejo account."""

from typing import ClassVar, Tuple

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.oauth_consumer.IdentityProvider import (
    AbstractOIDCProvider,
    client_settings,
    issuer_setting,
)

DEFAULT_SCOPES = "openid email profile"


class PRV_Forgejo(AbstractOIDCProvider):
    name: ClassVar[str] = "oauth_forgejo"
    public_name: ClassVar[str] = "forgejo"
    friendly_name: ClassVar[str] = "Forgejo"
    description: ClassVar[str] = "Sign in with a Forgejo instance"
    default_scopes: ClassVar[str] = DEFAULT_SCOPES
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        *client_settings("FORGEJO_CLIENT_ID", "FORGEJO_CLIENT_SECRET", DEFAULT_SCOPES),
        issuer_setting("FORGEJO_BASE_URL"),
    )
