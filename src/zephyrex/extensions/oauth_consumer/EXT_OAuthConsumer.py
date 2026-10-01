"""OAuth consumer extension manifest.

"Login with Google/GitHub/Microsoft/Amazon". This extension wires:
- A ``UserOAuthLinkModel`` table that records (user_id, provider, provider_user_id).
- A ``PasswordlessGrantRegistry`` entry for ``"oauth_consumer"`` so issued
  sessions are tagged like every other passwordless grant.
- IdP provider modules (Amazon, GitHub, Google, Microsoft) auto-registered
  with the IdPRegistry on import.

The complementary ``oauth_provider`` extension implements the *server* side
(third parties authenticate against this server's OAuth2 issuer).
"""

from typing import Any, ClassVar, Dict, List, Set

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension
from zephyrex.lib.Dependencies import Dependencies, PIP_Dependency
from zephyrex.lib.Logging import logger


class EXT_OAuthConsumer(AbstractStaticExtension):
    name: ClassVar[str] = "oauth_consumer"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Authenticate users against external OAuth2 Identity Providers "
        "(Google, GitHub, Microsoft, Amazon Cognito)."
    )

    _env: ClassVar[Dict[str, Any]] = {
        "OAUTH_CONSUMER_REDIRECT_URI": "",
        "GOOGLE_CLIENT_ID": "",
        "GOOGLE_CLIENT_SECRET": "",
        "GITHUB_CLIENT_ID": "",
        "GITHUB_CLIENT_SECRET": "",
        "MICROSOFT_CLIENT_ID": "",
        "MICROSOFT_CLIENT_SECRET": "",
        "AWS_CLIENT_ID": "",
        "AWS_CLIENT_SECRET": "",
        "AWS_USER_POOL_ID": "",
        "AWS_REGION": "",
    }

    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            PIP_Dependency(
                name="requests",
                friendly_name="HTTP requests library",
                semver=">=2.31.0",
                reason="HTTP requests to external IdP token + userinfo endpoints",
            ),
        ]
    )

    _abilities: ClassVar[Set[str]] = {
        "oauth_consumer_authorize",
        "oauth_consumer_callback",
    }
    _providers: ClassVar[List] = []
    extension_dependencies: ClassVar[List[str]] = ["auth_session"]

    @classmethod
    def on_initialize(cls) -> bool:
        from zephyrex.lib.SecretEncryption import (
            MissingFernetKeyError,
            assert_encryption_available,
        )

        try:
            assert_encryption_available()
        except MissingFernetKeyError as e:
            logger.error(f"oauth_consumer refusing to initialize: {e}")
            return False

        # Force-import the IdP modules so they self-register with IdPRegistry.
        from zephyrex.extensions.oauth_consumer import (  # noqa: F401
            Amazon,
            GitHub,
            Google,
            Microsoft,
        )

        # Force-import BLL so the PasswordlessGrantRegistry entry is set.
        from zephyrex.extensions.oauth_consumer import (  # noqa: F401
            BLL_OAuthConsumer,
        )
        from zephyrex.extensions.oauth_consumer.BLL_OAuthConsumer import (
            register_merge_participation,
        )

        register_merge_participation()
        logger.debug("oauth_consumer initialized")
        return True

    @classmethod
    def validate_config(cls) -> List[str]:
        from zephyrex.lib.Environment import env as _env
        from zephyrex.lib.SecretEncryption import (
            MissingFernetKeyError,
            assert_encryption_available,
        )

        issues: List[str] = []
        if not _env("OAUTH_CONSUMER_REDIRECT_URI"):
            issues.append(
                "OAUTH_CONSUMER_REDIRECT_URI is unset; callback flow will use empty redirect"
            )
        try:
            assert_encryption_available()
        except MissingFernetKeyError as e:
            issues.append(str(e))
        return issues
