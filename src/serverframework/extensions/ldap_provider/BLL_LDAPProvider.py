"""LDAP provider BLL: this server acts as an LDAP directory.

Exposes the local user store as an LDAP directory tree so third-party
applications can bind and search against this server. The ``ldap3``
library is imported lazily at usage time.
"""

from typing import ClassVar, List, Optional

from pydantic import BaseModel, Field

from serverframework.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    ModelMeta,
    StringSearchModel,
    UpdateMixinModel,
)


# ---------------------------------------------------------------------------
# Database model
# ---------------------------------------------------------------------------


class LDAPDirectoryEntryModel(
    ApplicationModel,
    UpdateMixinModel,
    metaclass=ModelMeta,
):
    """An entry in the LDAP directory served by this instance."""

    dn: str = Field(..., description="Distinguished name of this entry")
    object_class: str = Field(
        "inetOrgPerson", description="Space-delimited LDAP objectClass values"
    )
    user_id: Optional[str] = Field(
        None, description="Local user ID this entry represents (if a person entry)"
    )
    attributes_json: str = Field(
        "{}", description="JSON-encoded LDAP attributes for this entry"
    )
    parent_dn: Optional[str] = Field(
        None, description="Parent entry DN for tree traversal"
    )
    is_enabled: bool = Field(True, description="Whether this entry is visible in search results")

    table_comment: ClassVar[str] = "LDAP directory entries served by this instance"

    class Create(BaseModel):
        dn: str
        object_class: str = "inetOrgPerson"
        user_id: Optional[str] = None
        attributes_json: str = "{}"
        parent_dn: Optional[str] = None
        is_enabled: bool = True

    class Update(BaseModel):
        object_class: Optional[str] = None
        attributes_json: Optional[str] = None
        is_enabled: Optional[bool] = None

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        dn: Optional[StringSearchModel] = None
        user_id: Optional[StringSearchModel] = None
        parent_dn: Optional[StringSearchModel] = None
        is_enabled: Optional[bool] = None


class LDAPProviderConfigModel(
    ApplicationModel,
    UpdateMixinModel,
    metaclass=ModelMeta,
):
    """Server-side LDAP provider configuration."""

    listen_port: int = Field(3389, description="Port the LDAP server listens on")
    base_dn: str = Field(..., description="Root DN for the directory tree")
    tls_cert_path: Optional[str] = Field(
        None, description="Path to TLS certificate for LDAPS"
    )
    tls_key_path: Optional[str] = Field(
        None, description="Path to TLS private key for LDAPS"
    )
    realm: Optional[str] = Field(None, description="Authentication realm name")
    max_connections: int = Field(100, description="Maximum concurrent LDAP connections")
    is_enabled: bool = Field(True)

    table_comment: ClassVar[str] = "LDAP provider server configuration"

    class Create(BaseModel):
        listen_port: int = 3389
        base_dn: str
        tls_cert_path: Optional[str] = None
        tls_key_path: Optional[str] = None
        realm: Optional[str] = None
        max_connections: int = 100
        is_enabled: bool = True

    class Update(BaseModel):
        listen_port: Optional[int] = None
        base_dn: Optional[str] = None
        tls_cert_path: Optional[str] = None
        tls_key_path: Optional[str] = None
        realm: Optional[str] = None
        max_connections: Optional[int] = None
        is_enabled: Optional[bool] = None

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        base_dn: Optional[StringSearchModel] = None
        is_enabled: Optional[bool] = None


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class LDAPProviderManager(AbstractBLLManager):
    _model = LDAPProviderConfigModel
