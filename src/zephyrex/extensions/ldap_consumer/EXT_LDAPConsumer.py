# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sign in to this server with directory credentials: OpenLDAP, Active
Directory, FreeIPA (see BLL_LDAPConsumer for the flow, LDAPClient for the
protocol).

The abilities are the administrator's: they list the configured
directories, check one is reachable, and look a person up in one, acting
as the server (directories are its configuration, not a user's).

The complementary ``ldap_provider`` extension is the other side: this
server answering LDAP for third-party consumers."""

import asyncio
from typing import Any, ClassVar, Dict, List, Set

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
    ability,
)
from zephyrex.extensions.ldap_consumer.BLL_LDAPConsumer import (
    LdapLoginManager,
    client_for,
)
from zephyrex.extensions.ldap_consumer.LDAPClient import ALLOW_PLAINTEXT_LOOPBACK
from zephyrex.lib.Dependencies import Dependencies, EXT_Dependency, PIP_Dependency
from zephyrex.lib.Environment import env


class EXT_LDAPConsumer(AbstractStaticExtension):
    name: ClassVar[str] = "ldap_consumer"
    version: ClassVar[str] = "2.0.0"
    description: ClassVar[str] = (
        "Sign in with directory credentials (OpenLDAP, Active Directory, FreeIPA)"
    )

    _env: ClassVar[Dict[str, Any]] = {ALLOW_PLAINTEXT_LOOPBACK: "false"}
    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            PIP_Dependency(
                name="ldap3",
                friendly_name="ldap3",
                semver=">=2.9.1",
                reason="LDAP search and bind against the directory",
            ),
            EXT_Dependency(
                name="auth_session",
                friendly_name="Sessions",
                reason="A directory sign-in issues a revocable session",
            ),
            EXT_Dependency(
                name="auth_lockout",
                friendly_name="Account lockout",
                optional=True,
                reason="Failed directory sign-ins count toward a user's lockout",
            ),
        ]
    )
    _abilities: ClassVar[Set[str]] = {
        "list_ldap_directories",
        "check_ldap_directory",
        "find_ldap_account",
    }

    @classmethod
    def _registry(cls) -> Any:
        manager = cls.as_requester(LdapLoginManager, env("ROOT_ID"))
        return manager.model_registry

    @classmethod
    @ability("list_ldap_directories")
    async def list_ldap_directories(cls) -> List[Dict[str, Any]]:
        """The enabled directories: id, name, host and how each is secured."""
        manager = cls.as_requester(LdapLoginManager, env("ROOT_ID"))
        return [
            {"id": d.id, "name": d.name, "host": d.host, "security": d.security}
            for d in manager.enabled_directories()
        ]

    @classmethod
    @ability("check_ldap_directory")
    async def check_ldap_directory(cls, directory_id: str) -> Dict[str, Any]:
        """Reach the directory securely and bind as its service account."""
        client = client_for(cls._registry(), directory_id)
        await asyncio.to_thread(client.check)
        return {"reachable": True, "security": client.settings.security}

    @classmethod
    @ability("find_ldap_account")
    async def find_ldap_account(
        cls, directory_id: str, username: str
    ) -> Dict[str, Any]:
        """The person ``username`` names in the directory: DN, stable id,
        email, display name and groups."""
        client = client_for(cls._registry(), directory_id)
        account = await asyncio.to_thread(client.find, username)
        return {
            "dn": account.dn,
            "external_id": account.external_id,
            "username": account.username,
            "email": account.email,
            "display_name": account.display_name,
            "groups": list(account.groups),
        }
