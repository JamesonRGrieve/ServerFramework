# SPDX-License-Identifier: AGPL-3.0-or-later
"""auth_oauth2_client extension — external-IdP SSO (the OAuth2 client role).

Lets a logged-in user link external identity providers (Google / GitHub /
Microsoft / Amazon) to their account. The entities and the SSO flow verbs
(``/v1/oauth2_client/{providers,connections,connect,callback,disconnect}``)
live in ``BLL_Auth_OAuth2Client``; the per-provider code<->token<->userinfo
exchange is delegated to the ``Google`` / ``GitHub`` / ``Microsoft`` / ``Amazon``
adapter modules.

The OAuth2 *authorization-server* role that this module used to carry in-memory
now lives in the separate ``auth_oauth2_server`` extension. Opt-in via
``APP_EXTENSIONS``; not loaded by default.
"""

from typing import ClassVar, List, Type

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension


class EXT_Auth_OAuth2Client(AbstractStaticExtension):
    name: ClassVar[str] = "auth_oauth2_client"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "External-IdP SSO client: link Google/GitHub/Microsoft/Amazon identities "
        "to a user account (connect / callback / disconnect)"
    )
    extension_dependencies: ClassVar[List[str]] = []

    @classmethod
    def models(cls) -> List[Type]:
        from zephyrex.extensions.auth_oauth2_client.BLL_Auth_OAuth2Client import (
            OAuthExternalScopeModel,
            OAuthProviderModel,
            UserOAuthModel,
        )

        return [UserOAuthModel, OAuthProviderModel, OAuthExternalScopeModel]
