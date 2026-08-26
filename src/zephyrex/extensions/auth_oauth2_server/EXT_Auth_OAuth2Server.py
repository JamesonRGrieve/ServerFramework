# SPDX-License-Identifier: AGPL-3.0-or-later
"""auth_oauth2_server extension — the OAuth2 authorization server.

Owns the OAuth2 client/authorization-code/token entities (see
``BLL_Auth_OAuth2Server``). The authorization-server HTTP verbs (``/authorize``,
``/token``, ``/introspect``, ``/revoke``) are added to this class as
``@static_route`` classmethods (phase 2). Opt-in via ``APP_EXTENSIONS``; not
loaded by default.

Security posture: opaque, DB-stored, revocable tokens; PKCE S256 required for
public clients; constant-time client-secret comparison.
"""

from typing import ClassVar, List, Type

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension


class EXT_Auth_OAuth2Server(AbstractStaticExtension):
    name: ClassVar[str] = "auth_oauth2_server"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "OAuth2 authorization server: third-party client registration, the "
        "authorization-code flow with PKCE, and opaque revocable access/refresh "
        "tokens (validate / refresh / revoke / introspect)"
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
