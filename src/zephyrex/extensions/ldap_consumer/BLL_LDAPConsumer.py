"""LDAP consumer BLL: authenticate users against an external LDAP/AD server.

Supports direct-bind and search-then-bind flows. Connection configuration
(host, port, TLS, service account) is drawn from environment variables.
The ``ldap3`` library is imported lazily at usage time so the extension
can be installed without the dependency present (it only fails when
actually used).
"""

from datetime import datetime
from typing import ClassVar, List, Optional

from pydantic import BaseModel, Field

from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    DateSearchModel,
    ModelMeta,
    StringSearchModel,
    UpdateMixinModel,
)


# ---------------------------------------------------------------------------
# Database model
# ---------------------------------------------------------------------------


class LDAPServerConfigModel(
    ApplicationModel,
    UpdateMixinModel,
    metaclass=ModelMeta,
):
    """Configuration for an external LDAP/AD server this instance authenticates against."""

    host: str = Field(..., description="LDAP server hostname or IP")
    port: int = Field(389, description="LDAP server port (389 for plain/STARTTLS, 636 for LDAPS)")
    use_ssl: bool = Field(False, description="Connect over LDAPS (port 636)")
    use_starttls: bool = Field(False, description="Upgrade plain connection to TLS via STARTTLS")
    bind_dn: Optional[str] = Field(
        None, description="Service account DN for search-then-bind flow"
    )
    bind_password: Optional[str] = Field(
        None, description="Service account password (encrypted at rest)"
    )
    base_dn: str = Field(..., description="Base DN for user searches")
    user_search_filter: str = Field(
        "(uid={username})",
        description="LDAP filter template for user lookup; {username} is replaced",
    )
    group_search_base: Optional[str] = Field(
        None, description="Base DN for group membership searches"
    )
    group_search_filter: str = Field(
        "(member={dn})",
        description="LDAP filter for group membership; {dn} is replaced with user DN",
    )
    timeout_seconds: int = Field(10, description="Connection and operation timeout")
    is_enabled: bool = Field(True, description="Whether this server config is active")

    table_comment: ClassVar[str] = "LDAP/AD server configurations for consumer auth"

    class Create(BaseModel):
        host: str
        port: int = 389
        use_ssl: bool = False
        use_starttls: bool = False
        bind_dn: Optional[str] = None
        bind_password: Optional[str] = None
        base_dn: str
        user_search_filter: str = "(uid={username})"
        group_search_base: Optional[str] = None
        group_search_filter: str = "(member={dn})"
        timeout_seconds: int = 10
        is_enabled: bool = True

    class Update(BaseModel):
        host: Optional[str] = None
        port: Optional[int] = None
        use_ssl: Optional[bool] = None
        use_starttls: Optional[bool] = None
        bind_dn: Optional[str] = None
        bind_password: Optional[str] = None
        base_dn: Optional[str] = None
        user_search_filter: Optional[str] = None
        group_search_base: Optional[str] = None
        group_search_filter: Optional[str] = None
        timeout_seconds: Optional[int] = None
        is_enabled: Optional[bool] = None

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        host: Optional[StringSearchModel] = None
        base_dn: Optional[StringSearchModel] = None
        is_enabled: Optional[bool] = None


class UserLDAPLinkModel(
    ApplicationModel,
    UpdateMixinModel,
    metaclass=ModelMeta,
):
    """Persistent link between a local user and their LDAP identity."""

    user_id: str = Field(..., description="Local user this link belongs to")
    ldap_server_id: str = Field(..., description="FK to LDAPServerConfigModel")
    ldap_dn: str = Field(..., description="User's distinguished name in the directory")
    ldap_uid: Optional[str] = Field(None, description="uid attribute value")
    ldap_email: Optional[str] = Field(None, description="mail attribute value")
    last_login_at: Optional[datetime] = Field(
        None, description="Last successful LDAP authentication"
    )

    table_comment: ClassVar[str] = "Links a local user to an LDAP directory identity"

    class Create(BaseModel):
        user_id: str
        ldap_server_id: str
        ldap_dn: str
        ldap_uid: Optional[str] = None
        ldap_email: Optional[str] = None

    class Update(BaseModel):
        ldap_uid: Optional[str] = None
        ldap_email: Optional[str] = None

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        user_id: Optional[StringSearchModel] = None
        ldap_server_id: Optional[StringSearchModel] = None
        ldap_dn: Optional[StringSearchModel] = None
        last_login_at: Optional[DateSearchModel] = None


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class LDAPConsumerManager(AbstractBLLManager):
    _model = LDAPServerConfigModel
