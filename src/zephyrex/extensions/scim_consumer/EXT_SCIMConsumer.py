# SPDX-License-Identifier: AGPL-3.0-or-later
"""SCIM 2.0 consumer: this server is the SCIM service provider that
identity providers (Okta, Entra ID, OneLogin, any SCIM client) provision
users and groups into, at ``/v1/scim/v2`` (see ``BLL_SCIMConsumer``).

Root registers each identity provider as a connection
(``POST /v1/scim/connection/register``) and is shown its bearer token
once. The complementary ``scim_provider`` extension is the other
direction: this server pushing its users out to SCIM services.

Abilities act for ``requester_id`` under that user's permissions (in
practice root's, who owns the connections)."""

from typing import Any, ClassVar, Dict, List, Set

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
    ability,
)
from zephyrex.extensions.ExternalErrors import InvalidInputExternalError
from zephyrex.extensions.scim_consumer.BLL_SCIMConsumer import (
    ScimConnectionManager,
    ScimProvisioningLogManager,
)
from zephyrex.lib.Dependencies import Dependencies

MAX_LOG_ENTRIES = 500


def _row(model: Any) -> Dict[str, Any]:
    dumped: Dict[str, Any] = model.model_dump(mode="json")
    return dumped


class EXT_SCIMConsumer(AbstractStaticExtension):
    name: ClassVar[str] = "scim_consumer"
    version: ClassVar[str] = "2.0.0"
    description: ClassVar[str] = (
        "A SCIM 2.0 service identity providers provision users and groups into."
    )

    _env: ClassVar[Dict[str, Any]] = {}
    dependencies: ClassVar[Dependencies] = Dependencies([])
    _abilities: ClassVar[Set[str]] = {
        "list_scim_connections",
        "scim_provisioning_log",
    }

    @classmethod
    @ability("list_scim_connections")
    async def list_scim_connections(cls, requester_id: str) -> List[Dict[str, Any]]:
        """The identity-provider connections the user can see (tokens are
        never shown)."""
        manager: ScimConnectionManager = cls.as_requester(
            ScimConnectionManager, requester_id
        )
        return [_row(connection) for connection in manager.list()]

    @classmethod
    @ability("scim_provisioning_log")
    async def scim_provisioning_log(
        cls, requester_id: str, connection_id: str, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """A connection's ``limit`` most recent provisioning requests,
        newest first."""
        if not 1 <= limit <= MAX_LOG_ENTRIES:
            raise InvalidInputExternalError(f"limit is 1-{MAX_LOG_ENTRIES}")
        manager: ScimProvisioningLogManager = cls.as_requester(
            ScimProvisioningLogManager, requester_id
        )
        entries = sorted(
            manager.list(scim_connection_id=connection_id),
            key=lambda entry: entry.received_at,
            reverse=True,
        )
        return [_row(entry) for entry in entries[:limit]]
