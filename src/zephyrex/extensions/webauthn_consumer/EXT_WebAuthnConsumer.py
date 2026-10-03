# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sign in to this server with passkeys and security keys: this server is
the WebAuthn Relying Party for its own users (see BLL_WebAuthnConsumer).

The sibling ``webauthn_provider`` extension serves WebAuthn to other
applications' users instead.

Abilities act for the user named by ``requester_id``, on that user's own
credentials."""

from typing import Any, ClassVar, Dict, List, Set

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
    ability,
)
from zephyrex.extensions.webauthn_consumer.BLL_WebAuthnConsumer import (
    WebAuthnCredentialManager,
)
from zephyrex.extensions.webauthn_consumer.RelyingParty import (
    SETTINGS,
    config_issues,
)
from zephyrex.lib.Dependencies import Dependencies, EXT_Dependency, PIP_Dependency


def _row(model: Any) -> Dict[str, Any]:
    dumped: Dict[str, Any] = model.model_dump(mode="json")
    return dumped


class EXT_WebAuthnConsumer(AbstractStaticExtension):
    name: ClassVar[str] = "webauthn_consumer"
    version: ClassVar[str] = "2.0.0"
    description: ClassVar[str] = "Sign in with passkeys and security keys (WebAuthn)"

    _env: ClassVar[Dict[str, Any]] = dict(SETTINGS)

    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            PIP_Dependency(
                name="webauthn",
                friendly_name="py_webauthn",
                semver=">=3.0.1",
                reason="WebAuthn ceremony options, attestation and assertion checks",
            ),
            EXT_Dependency(
                name="auth_session",
                friendly_name="Sessions",
                reason="A passkey sign-in issues a revocable session",
            ),
        ]
    )

    _abilities: ClassVar[Set[str]] = {
        "webauthn_consumer_list_credentials",
        "webauthn_consumer_remove_credential",
    }

    @classmethod
    def credentials(cls, requester_id: str) -> WebAuthnCredentialManager:
        manager: WebAuthnCredentialManager = cls.as_requester(
            WebAuthnCredentialManager, requester_id
        )
        return manager

    @classmethod
    @ability("webauthn_consumer_list_credentials")
    async def list_credentials(cls, requester_id: str) -> List[Dict[str, Any]]:
        """The user's registered passkeys and security keys."""
        return [_row(c) for c in cls.credentials(requester_id).list()]

    @classmethod
    @ability("webauthn_consumer_remove_credential")
    async def remove_credential(
        cls, requester_id: str, record_id: str
    ) -> Dict[str, Any]:
        """Remove one of the user's credentials (by its record id); it no
        longer signs in."""
        cls.credentials(requester_id).delete(id=record_id)
        return {"removed": record_id}

    @classmethod
    def validate_config(cls) -> List[str]:
        return config_issues()
