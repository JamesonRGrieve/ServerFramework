# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sign in with a Microsoft account (Entra ID, work or school, or a
personal account), an OpenID provider at
``https://login.microsoftonline.com/<tenant>/v2.0``.

``tenant`` is a tenant id or domain to admit one directory, or
``common`` / ``organizations`` / ``consumers``. Those multi-tenant
endpoints publish a templated issuer, ``.../{tenantid}/v2.0``: an ID
token's issuer must be that template filled with the token's own ``tid``.

Entra's ``email`` claim is not verified: a directory administrator can set
it to any address. It counts as verified only when the token says so
(``email_verified``, or the ``xms_edov`` optional claim: the email domain's
owner is verified), so enable ``xms_edov`` on the app registration for
email matching to work."""

from typing import Any, ClassVar, Dict, List, Tuple

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.ExternalErrors import PermanentExternalError
from zephyrex.extensions.oauth_consumer.IdentityProvider import (
    AbstractOIDCProvider,
    Endpoints,
    asserted_true,
    client_settings,
    text,
)
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

MICROSOFT_AUTHORITY = "https://login.microsoftonline.com"
DEFAULT_TENANT = "common"
TENANT_PLACEHOLDER = "{tenantid}"
DEFAULT_SCOPES = "openid email profile"


class PRV_Microsoft(AbstractOIDCProvider):
    name: ClassVar[str] = "oauth_microsoft"
    public_name: ClassVar[str] = "microsoft"
    friendly_name: ClassVar[str] = "Microsoft"
    description: ClassVar[str] = "Sign in with a Microsoft account"
    default_scopes: ClassVar[str] = DEFAULT_SCOPES
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        *client_settings(
            "MICROSOFT_CLIENT_ID", "MICROSOFT_CLIENT_SECRET", DEFAULT_SCOPES
        ),
        InstanceSetting(
            "tenant",
            "Tenant id or domain, or common / organizations / consumers",
            "MICROSOFT_TENANT_ID",
            default=DEFAULT_TENANT,
        ),
        InstanceSetting(
            "authority",
            "The Microsoft identity platform's host",
            default=MICROSOFT_AUTHORITY,
        ),
    )

    @classmethod
    def issuer(cls, instance: ProviderInstanceModel) -> str:
        authority = (cls.setting(instance, "authority") or MICROSOFT_AUTHORITY).rstrip(
            "/"
        )
        tenant = (cls.setting(instance, "tenant") or DEFAULT_TENANT).strip()
        return f"{authority}/{tenant}/v2.0"

    @classmethod
    def is_configured_instance(cls, instance: ProviderInstanceModel) -> bool:
        return text(cls.setting(instance, "client_id")) is not None

    @classmethod
    def metadata_issuer(
        cls, instance: ProviderInstanceModel, metadata: Dict[str, Any]
    ) -> str:
        """The configured tenant's issuer, or for a multi-tenant endpoint
        the template every tenant's issuer fills."""
        named = text(metadata.get("issuer"))
        authority = (cls.setting(instance, "authority") or MICROSOFT_AUTHORITY).rstrip(
            "/"
        )
        template = f"{authority}/{TENANT_PLACEHOLDER}/v2.0"
        if named is not None and named in (cls.issuer(instance), template):
            return named
        raise PermanentExternalError(
            f"{cls.friendly_name}: the provider's metadata names another issuer",
            provider=cls.name,
        )

    @classmethod
    def accepted_issuers(
        cls,
        instance: ProviderInstanceModel,
        endpoints: Endpoints,
        claims: Dict[str, Any],
    ) -> List[str]:
        issuer = endpoints.issuer or ""
        if TENANT_PLACEHOLDER not in issuer:
            return super().accepted_issuers(instance, endpoints, claims)
        tenant = text(claims.get("tid"))
        return [issuer.replace(TENANT_PLACEHOLDER, tenant)] if tenant else []

    @classmethod
    def email_verified(cls, claims: Dict[str, Any]) -> bool:
        return asserted_true(claims.get("email_verified")) or asserted_true(
            claims.get("xms_edov")
        )
