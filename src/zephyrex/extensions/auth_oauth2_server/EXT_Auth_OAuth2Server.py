# SPDX-License-Identifier: AGPL-3.0-or-later
"""auth_oauth2_server extension — the OAuth2 authorization server.

Owns the OAuth2 client/authorization-code/token entities and the
authorization-server verbs, all defined in ``BLL_Auth_OAuth2Server``:
``OAuth2ClientManager`` exposes client-registration CRUD at ``/v1/oauth2/client``
and ``OAuth2TokenManager`` hosts the flow verbs (``/authorize``, ``/token``,
``/introspect``, ``/revoke``) at ``/v1/oauth2`` via ``custom_routes``.

Security posture: opaque, DB-stored, revocable tokens; PKCE S256 required for
public clients; constant-time client-secret comparison. Opt-in via
``APP_EXTENSIONS``; not loaded by default.
"""

from typing import ClassVar, List, Type

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension


class EXT_Auth_OAuth2Server(AbstractStaticExtension):
    name: ClassVar[str] = "auth_oauth2_server"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "OAuth2 authorization server: third-party client registration, the "
        "authorization-code flow with PKCE, and opaque revocable access/refresh "
        "tokens (introspect / revoke)"
    )
    extension_dependencies: ClassVar[List[str]] = []

    @classmethod
    def models(cls) -> List[Type]:
        from zephyrex.extensions.auth_oauth2_server.BLL_Auth_OAuth2Server import (
            OAuth2AuthCodeModel,
            OAuth2ClientModel,
            OAuth2TokenModel,
        )

        return [OAuth2ClientModel, OAuth2AuthCodeModel, OAuth2TokenModel]
