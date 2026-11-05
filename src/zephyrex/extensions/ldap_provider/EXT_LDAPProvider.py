# SPDX-License-Identifier: AGPL-3.0-or-later
"""This server as a read-only LDAP directory for legacy applications.

Framework users and teams are served as an LDAPv3 directory (see
``LDAPDirectory``): applications bind as a service account (configured
by the server, ``BLL_LDAPProvider``) or as a user with their framework
password, and search people and groups. Nothing is written through LDAP.

The listener starts with the app and stops with it. It serves LDAPS on
``LDAP_PROVIDER_LDAPS_PORT`` and plain LDAP that demands StartTLS on
``LDAP_PROVIDER_LISTEN_PORT``; an empty port turns that listener off.
Both need the certificate and key. Without a base DN, a certificate or a
listener the directory does not start, and ``validate_config`` says why.

The complementary ``ldap_consumer`` extension is the client side
(authenticating local users against an external directory).
"""

from typing import Any, ClassVar, Dict, List, Optional, Set

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension
from zephyrex.extensions.ldap_provider.LDAPDirectory import (
    DEFAULT_RELEASED_ATTRIBUTES,
    DirectoryConfig,
    LDAPDirectory,
    Refused,
    parse,
)
from zephyrex.extensions.ldap_provider.LDAPServer import (
    ListenerConfig,
    LDAPServerThread,
    server_tls_context,
)
from zephyrex.lib.Dependencies import Dependencies, PIP_Dependency
from zephyrex.lib.Logging import logger

DEFAULT_SIZE_LIMIT = 500
DEFAULT_TIME_LIMIT_SECONDS = 10
DEFAULT_MAX_CONNECTIONS = 100


class EXT_LDAPProvider(AbstractStaticExtension):
    name: ClassVar[str] = "ldap_provider"
    version: ClassVar[str] = "2.0.0"
    description: ClassVar[str] = (
        "Serve this server's users and teams as a read-only LDAP directory"
    )

    _env: ClassVar[Dict[str, Any]] = {
        "LDAP_PROVIDER_BASE_DN": "",
        "LDAP_PROVIDER_LISTEN_HOST": "127.0.0.1",
        "LDAP_PROVIDER_LISTEN_PORT": "3389",
        "LDAP_PROVIDER_LDAPS_PORT": "",
        "LDAP_PROVIDER_TLS_CERT_PATH": "",
        "LDAP_PROVIDER_TLS_KEY_PATH": "",
        "LDAP_PROVIDER_MAX_CONNECTIONS": str(DEFAULT_MAX_CONNECTIONS),
        "LDAP_PROVIDER_SIZE_LIMIT": str(DEFAULT_SIZE_LIMIT),
        "LDAP_PROVIDER_TIME_LIMIT_SECONDS": str(DEFAULT_TIME_LIMIT_SECONDS),
        "LDAP_PROVIDER_RELEASED_ATTRIBUTES": ",".join(DEFAULT_RELEASED_ATTRIBUTES),
    }

    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            PIP_Dependency(
                name="ldap3",
                friendly_name="LDAP v3 library",
                semver=">=2.9.1",
                reason="The LDAP protocol's ASN.1 definitions and DN syntax",
            ),
            PIP_Dependency(
                name="pyasn1",
                friendly_name="ASN.1 BER codec",
                semver=">=0.6.1",
                reason="Decoding and encoding LDAP messages",
            ),
        ]
    )
    _abilities: ClassVar[Set[str]] = set()

    _listener: ClassVar[Optional[LDAPServerThread]] = None

    @classmethod
    def _setting(cls, key: str) -> str:
        return str(cls.get_env_value(key) or "").strip()

    @classmethod
    def _number(cls, key: str, issues: List[str]) -> Optional[int]:
        raw = cls._setting(key)
        if not raw:
            return None
        try:
            value = int(raw)
        except ValueError:
            issues.append(f"{key} is not a number")
            return None
        if value < 0:
            issues.append(f"{key} is negative")
            return None
        return value

    @classmethod
    def directory_config(cls, issues: List[str]) -> Optional[DirectoryConfig]:
        """The directory's configuration, or None with ``issues`` saying
        what is wrong."""
        base_dn = cls._setting("LDAP_PROVIDER_BASE_DN")
        try:
            if not parse(base_dn):
                issues.append(
                    "LDAP_PROVIDER_BASE_DN is unset; the directory has no root"
                )
                return None
        except Refused:
            issues.append("LDAP_PROVIDER_BASE_DN is not a DN")
            return None
        size_limit = cls._number("LDAP_PROVIDER_SIZE_LIMIT", issues)
        time_limit = cls._number("LDAP_PROVIDER_TIME_LIMIT_SECONDS", issues)
        released = cls._setting("LDAP_PROVIDER_RELEASED_ATTRIBUTES").split(",")
        return DirectoryConfig(
            base_dn=base_dn,
            released=DirectoryConfig.released_from(released),
            size_limit=size_limit or DEFAULT_SIZE_LIMIT,
            time_limit_seconds=float(time_limit or DEFAULT_TIME_LIMIT_SECONDS),
        )

    @classmethod
    def listener_config(cls, issues: List[str]) -> Optional[ListenerConfig]:
        """The listeners' configuration, or None with ``issues`` saying what
        is wrong."""
        found: List[str] = []
        starttls_port = cls._number("LDAP_PROVIDER_LISTEN_PORT", found)
        ldaps_port = cls._number("LDAP_PROVIDER_LDAPS_PORT", found)
        max_connections = cls._number("LDAP_PROVIDER_MAX_CONNECTIONS", found)
        if starttls_port is None and ldaps_port is None:
            found.append("neither LDAP_PROVIDER_LISTEN_PORT nor _LDAPS_PORT is set")
        cert = cls._setting("LDAP_PROVIDER_TLS_CERT_PATH")
        key = cls._setting("LDAP_PROVIDER_TLS_KEY_PATH")
        if not cert or not key:
            found.append(
                "LDAP_PROVIDER_TLS_CERT_PATH and _TLS_KEY_PATH are required: "
                "the directory is served over TLS only"
            )
        else:
            try:
                tls = server_tls_context(cert, key)
            except (OSError, ValueError) as exc:
                found.append(f"the TLS certificate or key cannot be loaded ({exc})")
        issues.extend(found)
        if found:
            return None
        return ListenerConfig(
            host=cls._setting("LDAP_PROVIDER_LISTEN_HOST") or "127.0.0.1",
            starttls_port=starttls_port,
            ldaps_port=ldaps_port,
            tls=tls,
            max_connections=max_connections or DEFAULT_MAX_CONNECTIONS,
        )

    @classmethod
    def validate_config(cls) -> List[str]:
        issues: List[str] = []
        cls.directory_config(issues)
        cls.listener_config(issues)
        return issues

    @classmethod
    def on_start(cls) -> None:
        from zephyrex.pydantic2.registry import ModelRegistry

        cls.on_stop()
        registry = ModelRegistry.attached()
        issues: List[str] = []
        directory_config = cls.directory_config(issues)
        listener_config = cls.listener_config(issues)
        if registry is None:
            issues.append("no app registry is attached")
        if (
            registry is None
            or directory_config is None
            or listener_config is None
            or issues
        ):
            logger.warning(
                "ldap_provider: the LDAP directory is not served: %s",
                "; ".join(issues),
            )
            return
        listener = LDAPServerThread(
            LDAPDirectory(registry, directory_config), listener_config
        )
        try:
            listener.start()
        except (OSError, TimeoutError) as exc:
            logger.error("ldap_provider: the LDAP directory cannot listen: %s", exc)
            return
        cls._listener = listener
        logger.info("ldap_provider: LDAP directory listening on %s", listener.ports)

    @classmethod
    def on_stop(cls) -> None:
        listener, cls._listener = cls._listener, None
        if listener is not None:
            listener.stop()
