# SPDX-License-Identifier: AGPL-3.0-or-later
"""SCIM 2.0 service provider (RFC 7643/7644): identity providers (Okta,
Entra ID, OneLogin, …) provision users and groups into this server.

A connection (``ScimConnectionModel``) is one identity provider. Root
registers it and is shown its bearer token once; only the token's
SHA-256 digest is kept, compared in constant time. Rotating issues a new
token (the old one stops working); disabling or deleting the connection
revokes it.

The protocol lives under ``/v1/scim/v2`` (``ScimResourceManager``):
``/ServiceProviderConfig``, ``/ResourceTypes``, ``/Schemas``, and
``/Users`` and ``/Groups`` with list (``filter``, ``startIndex``,
``count``, ``sortBy``/``sortOrder``, ``attributes``/
``excludedAttributes``), POST, GET, PUT, PATCH and DELETE. Users are
framework users and Groups are teams (see ``SCIMResources``). A
connection sees and changes only what it provisioned, recorded per
connection with the provider's ``externalId`` (``ScimResourceModel``);
with ``link_existing_users`` it adopts an existing account that has the
``userName`` or email it pushes, instead of refusing it (409). The
internal accounts (root, system, template) are never adopted.

DELETE of a user deactivates the account and ends the link (it is gone
from SCIM; a later POST of the same user links it again);
``delete_on_deprovision`` also deletes the account. DELETE of a group
deletes its team. Every change is logged (``ScimProvisioningLogModel``).
Resources carry weak ETags; PUT, PATCH and DELETE honour ``If-Match``
(412) and GET ``If-None-Match`` (304)."""

import hashlib
import hmac
import secrets
from datetime import datetime, timezone
from typing import Any, Callable, ClassVar, Dict, List, Optional, Set

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel as RouteModel
from pydantic import ConfigDict, Field
from sqlalchemy import func, or_

from zephyrex.database.StaticPermissions import is_any_internal_id, is_root_id
from zephyrex.extensions.scim_consumer.SCIMErrors import (
    UNIQUENESS,
    INVALID_VALUE,
    ScimError,
    bad_request,
)
from zephyrex.extensions.scim_consumer.SCIMFilter import (
    filter_resources,
    sort_resources,
)
from zephyrex.extensions.scim_consumer.SCIMPatch import apply_patch
from zephyrex.extensions.scim_consumer.SCIMResources import (
    GROUP,
    GROUP_READ_ONLY,
    SCIM_MEDIA_TYPE,
    USER,
    USER_READ_ONLY,
    GroupFields,
    Reference,
    UserFields,
    etag_matches,
    group_resource,
    list_response,
    page_bounds,
    parse_group,
    parse_user,
    project,
    resource_types,
    schemas,
    service_provider_config,
    user_resource,
)
from zephyrex.lib.CustomRoute import ExposeIn, custom_route
from zephyrex.lib.Environment import env
from zephyrex.lib.InboundSecurity import rate_limit
from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    DateSearchModel,
    ModelMeta,
    NameMixinModel,
    StringSearchModel,
    UpdateMixinModel,
)
from zephyrex.logic.BLL_Auth import (
    RoleModel,
    TeamManager,
    TeamModel,
    UserManager,
    UserModel,
    UserTeamManager,
    UserTeamModel,
)
from zephyrex.pydantic2.fastapi import AuthType, RouteType, RouterMixin
from zephyrex.pydantic2.registry import BaseModel

TOKEN_PREFIX = "scim_"
TOKEN_BYTES = 32
# Identity providers push in bursts (an initial sync is one request per
# user and group); this caps a runaway or hostile caller, not a sync.
PROTOCOL_RATE_LIMIT = "1200/min"
SCIM_PREFIX = "/v1/scim/v2"
OPERATION_CREATE = "create"
OPERATION_REPLACE = "replace"
OPERATION_PATCH = "patch"
OPERATION_DELETE = "delete"
STATUS_SUCCESS = "success"
STATUS_ERROR = "error"


def token_digest(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class ScimConnectionModel(
    ApplicationModel,
    UpdateMixinModel,
    NameMixinModel,
    RoleModel.Reference.Optional,
    metaclass=ModelMeta,
):
    """An identity provider that provisions into this server."""

    # Write-only: never serialized. Set only by register/rotate.
    token_hash: Optional[str] = Field(
        None, exclude=True, description="SHA-256 digest of the bearer token"
    )
    auto_create_users: bool = Field(
        True, description="A pushed user no account matches becomes a new account"
    )
    link_existing_users: bool = Field(
        False,
        description="Adopt an existing account with the pushed userName or email "
        "(otherwise 409)",
    )
    delete_on_deprovision: bool = Field(
        False, description="DELETE of a user deletes the account, not only disables it"
    )
    is_enabled: bool = Field(
        True, description="A disabled connection's token is refused"
    )

    table_comment: ClassVar[str] = (
        "SCIM 2.0 identity-provider connections provisioning into this server"
    )

    class Create(BaseModel, NameMixinModel, RoleModel.Reference.ID.Optional):
        auto_create_users: bool = True
        link_existing_users: bool = False
        delete_on_deprovision: bool = False
        is_enabled: bool = True

    class Update(BaseModel, NameMixinModel.Optional, RoleModel.Reference.ID.Optional):
        auto_create_users: Optional[bool] = None
        link_existing_users: Optional[bool] = None
        delete_on_deprovision: Optional[bool] = None
        is_enabled: Optional[bool] = None

    class Search(
        ApplicationModel.Search, UpdateMixinModel.Search, NameMixinModel.Search
    ):
        is_enabled: Optional[bool] = None


class ScimResourceModel(
    ApplicationModel,
    UpdateMixinModel,
    ScimConnectionModel.Reference,
    metaclass=ModelMeta,
):
    """A user or team a connection provisioned (or adopted), with the
    provider's externalId for it."""

    resource_type: str = Field(..., description="'User' or 'Group'")
    local_id: str = Field(..., description="The framework user's or team's id")
    external_id: Optional[str] = Field(None, description="The provider's externalId")
    deprovisioned_at: Optional[datetime] = Field(
        None, description="When the provider deleted it (the link is then ended)"
    )

    table_comment: ClassVar[str] = "Users and teams each SCIM connection provisions"
    permission_references: ClassVar[List[str]] = ["scim_connection"]

    class Create(BaseModel, ScimConnectionModel.Reference.ID):
        resource_type: str
        local_id: str
        external_id: Optional[str] = None
        deprovisioned_at: Optional[datetime] = None

    class Update(BaseModel):
        external_id: Optional[str] = None
        deprovisioned_at: Optional[datetime] = None

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        ScimConnectionModel.Reference.ID.Search,
    ):
        resource_type: Optional[StringSearchModel] = None
        local_id: Optional[StringSearchModel] = None
        external_id: Optional[StringSearchModel] = None


class ScimProvisioningLogModel(
    ApplicationModel,
    UpdateMixinModel,
    ScimConnectionModel.Reference,
    metaclass=ModelMeta,
):
    """One change an identity provider asked for, and how it went."""

    resource_type: str = Field(..., description="'User' or 'Group'")
    operation: str = Field(..., description="create, replace, patch or delete")
    scim_id: Optional[str] = Field(None, description="The resource's id")
    status: str = Field(..., description="'success' or 'error'")
    http_status: int = Field(..., description="The HTTP status answered")
    error_detail: Optional[str] = Field(None, description="Why it was refused")
    received_at: datetime = Field(..., description="When the request arrived")

    table_comment: ClassVar[str] = "Provisioning requests SCIM connections made"
    permission_references: ClassVar[List[str]] = ["scim_connection"]

    class Create(BaseModel, ScimConnectionModel.Reference.ID):
        resource_type: str
        operation: str
        scim_id: Optional[str] = None
        status: str
        http_status: int
        error_detail: Optional[str] = None
        received_at: Optional[datetime] = None

    class Update(BaseModel):
        pass

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        ScimConnectionModel.Reference.ID.Search,
    ):
        resource_type: Optional[StringSearchModel] = None
        operation: Optional[StringSearchModel] = None
        scim_id: Optional[StringSearchModel] = None
        status: Optional[StringSearchModel] = None
        received_at: Optional[DateSearchModel] = None


# ---------------------------------------------------------------------------
# Route models
# ---------------------------------------------------------------------------


class ScimConnectionRegistration(RouteModel):
    name: str = Field(..., description="The identity provider, for people")
    role_id: Optional[str] = Field(
        None, description="Members' role in provisioned teams (default: user)"
    )
    auto_create_users: bool = True
    link_existing_users: bool = False
    delete_on_deprovision: bool = False


class ScimTokenRotation(RouteModel):
    """No fields: the server makes the token."""


class ScimTokenIssued(RouteModel):
    """Returned once: the server keeps only the token's digest."""

    id: str
    name: str
    token: str = Field(..., description="The bearer token; never shown again")
    base_path: str = Field(..., description="Where the SCIM endpoints are")


class ScimBody(RouteModel):
    """A SCIM request body, kept whole for the protocol code to read."""

    model_config = ConfigDict(extra="allow")


class ScimResponse(JSONResponse):
    """A SCIM response: ``application/scim+json``, empty when ``None``."""

    media_type = SCIM_MEDIA_TYPE

    def render(self, content: Any) -> bytes:
        if content is None:
            return b""
        rendered: bytes = super().render(content)
        return rendered


def _error_response(error: ScimError) -> ScimResponse:
    return ScimResponse(error.body(), status_code=error.status, headers=error.headers)


# ---------------------------------------------------------------------------
# Connections
# ---------------------------------------------------------------------------


def _require_root(requester_id: str) -> None:
    if not is_root_id(requester_id):
        raise HTTPException(
            status_code=403, detail="Only root manages SCIM connections"
        )


class ScimConnectionManager(AbstractBLLManager, RouterMixin):
    """Root's identity-provider connections. Registered (and their tokens
    rotated) through custom routes, so the server makes every token."""

    _model = ScimConnectionModel

    prefix: ClassVar[Optional[str]] = "/v1/scim/connection"
    tags: ClassVar[Optional[List[str]]] = ["SCIM"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    routes_to_register: ClassVar[Optional[List[RouteType]]] = [
        RouteType.GET,
        RouteType.LIST,
        RouteType.SEARCH,
        RouteType.UPDATE,
        RouteType.DELETE,
    ]

    def create_validation(self, entity: Any) -> None:
        raise HTTPException(
            status_code=400,
            detail="Register a connection: POST /v1/scim/connection/register",
        )

    def _check_role(self, role_id: Optional[str]) -> None:
        if role_id is None:
            return
        RoleDB = RoleModel.DB(self.model_registry.DB.manager.Base)
        if not RoleDB.exists(
            requester_id=self.requester.id,
            model_registry=self.model_registry,
            id=role_id,
        ):
            raise HTTPException(status_code=404, detail="Role not found")

    def register(self, **fields: Any) -> ScimTokenIssued:
        _require_root(self.requester.id)
        registration = ScimConnectionRegistration(**fields)
        self._check_role(registration.role_id)
        raw = TOKEN_PREFIX + secrets.token_urlsafe(TOKEN_BYTES)
        record = self.DB.create(
            requester_id=self.requester.id,
            model_registry=self.model_registry,
            return_type="dto",
            override_dto=self.model_registry.apply(self.Model),
            token_hash=token_digest(raw),
            is_enabled=True,
            **registration.model_dump(),
        )
        return ScimTokenIssued(
            id=record.id, name=record.name, token=raw, base_path=SCIM_PREFIX
        )

    def rotate_token(self, id: str) -> ScimTokenIssued:
        """A new token for the connection; the old one stops working."""
        _require_root(self.requester.id)
        connection = self.get(id=id)
        raw = TOKEN_PREFIX + secrets.token_urlsafe(TOKEN_BYTES)
        self.DB.update(
            requester_id=self.requester.id,
            model_registry=self.model_registry,
            id=id,
            new_properties={"token_hash": token_digest(raw)},
        )
        return ScimTokenIssued(
            id=id, name=connection.name, token=raw, base_path=SCIM_PREFIX
        )

    def update(self, id: str, **kwargs: Any) -> Any:
        _require_root(self.requester.id)
        self._check_role(kwargs.get("role_id"))
        return super().update(id, **kwargs)

    def delete(self, id: str) -> None:
        _require_root(self.requester.id)
        super().delete(id)

    @custom_route(
        method="POST",
        path="/register",
        input_model=ScimConnectionRegistration,
        output_model=ScimTokenIssued,
        authentication_type="jwt",
        openapi_tags=("SCIM",),
        expose_in=(ExposeIn.REST,),
        summary="Register an identity provider; its token is shown once",
    )
    def register_route(self, body: ScimConnectionRegistration) -> ScimTokenIssued:
        return self.register(**body.model_dump())

    @custom_route(
        method="POST",
        path="/{connection_id}/rotate",
        input_model=ScimTokenRotation,
        output_model=ScimTokenIssued,
        authentication_type="jwt",
        openapi_tags=("SCIM",),
        expose_in=(ExposeIn.REST,),
        summary="Replace a connection's token; the old one stops working",
    )
    def rotate_route(
        self, connection_id: str, body: ScimTokenRotation
    ) -> ScimTokenIssued:
        return self.rotate_token(connection_id)


class ScimProvisioningLogManager(AbstractBLLManager, RouterMixin):
    """Read-only: the log is written by the protocol endpoints."""

    _model = ScimProvisioningLogModel

    prefix: ClassVar[Optional[str]] = "/v1/scim/log"
    tags: ClassVar[Optional[List[str]]] = ["SCIM"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    routes_to_register: ClassVar[Optional[List[RouteType]]] = [
        RouteType.GET,
        RouteType.LIST,
        RouteType.SEARCH,
    ]


# ---------------------------------------------------------------------------
# The directory one connection sees
# ---------------------------------------------------------------------------


def _member_display(user: Dict[str, Any]) -> Optional[str]:
    display: Optional[str] = (
        user.get("display_name") or user.get("username") or user.get("email")
    )
    return display


class ScimDirectory:
    """What one connection provisioned, read and changed for it. Accounts
    are read and written as root (the connection is root's), teams as
    system; nothing outside the connection's links is reachable."""

    def __init__(self, model_registry: Any, connection: Any, base: str) -> None:
        self.registry = model_registry
        self.connection = connection
        self.base = base
        declarative = model_registry.DB.manager.Base
        self.users_db = UserModel.DB(declarative)
        self.teams_db = TeamModel.DB(declarative)
        self.memberships_db = UserTeamModel.DB(declarative)
        self.links_db = ScimResourceModel.DB(declarative)
        self.root_id = env("ROOT_ID")
        self.system_id = env("SYSTEM_ID")

    # -- reading ----------------------------------------------------------

    def _rows(self, db: Any, *filters: Any) -> List[Dict[str, Any]]:
        """Live rows (root's reads include deleted ones unless told not to)."""
        rows: List[Dict[str, Any]] = db.list(
            requester_id=self.root_id,
            model_registry=self.registry,
            filters=[db.deleted_at.is_(None), *filters],
            return_type="dict",
        )
        return rows

    def location(self, resource_type: str, identifier: str) -> str:
        return f"{self.base}/{resource_type}s/{identifier}"

    def links(self, resource_type: str, ended: bool = False) -> List[Dict[str, Any]]:
        """The connection's links of ``resource_type``, oldest first; with
        ``ended``, also those the provider deleted."""
        rows = self._rows(
            self.links_db,
            self.links_db.scim_connection_id == self.connection.id,
            self.links_db.resource_type == resource_type,
        )
        if not ended:
            rows = [row for row in rows if row.get("deprovisioned_at") is None]
        return sorted(rows, key=lambda row: (str(row.get("created_at")), row["id"]))

    def link(self, resource_type: str, local_id: str) -> Dict[str, Any]:
        for row in self.links(resource_type):
            if row["local_id"] == local_id:
                return row
        raise ScimError(404, f"{resource_type} {local_id} not found")

    def _by_id(self, db: Any, ids: Set[str]) -> Dict[str, Dict[str, Any]]:
        if not ids:
            return {}
        return {row["id"]: row for row in self._rows(db, db.id.in_(sorted(ids)))}

    def _memberships(
        self, team_ids: Set[str], user_ids: Set[str]
    ) -> List[Dict[str, Any]]:
        if not team_ids or not user_ids:
            return []
        return self._rows(
            self.memberships_db,
            self.memberships_db.team_id.in_(sorted(team_ids)),
            self.memberships_db.user_id.in_(sorted(user_ids)),
        )

    def render_users(self, links: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        users = self._by_id(self.users_db, {link["local_id"] for link in links})
        teams = self._by_id(
            self.teams_db, {link["local_id"] for link in self.links(GROUP)}
        )
        groups: Dict[str, List[Reference]] = {}
        for membership in self._memberships(set(teams), set(users)):
            team = teams[membership["team_id"]]
            groups.setdefault(membership["user_id"], []).append(
                Reference(
                    team["id"], team.get("name"), self.location(GROUP, team["id"])
                )
            )
        return [
            user_resource(
                users[link["local_id"]],
                link.get("external_id"),
                groups.get(link["local_id"], []),
                self.location(USER, link["local_id"]),
            )
            for link in links
            if link["local_id"] in users
        ]

    def render_groups(self, links: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        teams = self._by_id(self.teams_db, {link["local_id"] for link in links})
        users = self._by_id(
            self.users_db, {link["local_id"] for link in self.links(USER)}
        )
        members: Dict[str, List[Reference]] = {}
        for membership in self._memberships(set(teams), set(users)):
            user = users[membership["user_id"]]
            members.setdefault(membership["team_id"], []).append(
                Reference(
                    user["id"], _member_display(user), self.location(USER, user["id"])
                )
            )
        return [
            group_resource(
                teams[link["local_id"]],
                link.get("external_id"),
                members.get(link["local_id"], []),
                self.location(GROUP, link["local_id"]),
            )
            for link in links
            if link["local_id"] in teams
        ]

    def resources(self, resource_type: str) -> List[Dict[str, Any]]:
        links = self.links(resource_type)
        if resource_type == USER:
            return self.render_users(links)
        return self.render_groups(links)

    def resource(self, resource_type: str, local_id: str) -> Dict[str, Any]:
        links = [self.link(resource_type, local_id)]
        rendered = (
            self.render_users(links)
            if resource_type == USER
            else self.render_groups(links)
        )
        if not rendered:
            raise ScimError(404, f"{resource_type} {local_id} not found")
        return rendered[0]

    # -- shared checks ----------------------------------------------------

    @staticmethod
    def precondition(current: Dict[str, Any], if_match: Optional[str]) -> None:
        if if_match is not None and not etag_matches(
            if_match, current["meta"]["version"]
        ):
            raise ScimError(412, "The resource has changed since that version")

    def _external_id_free(
        self, resource_type: str, external_id: Optional[str], local_id: Optional[str]
    ) -> None:
        if external_id is None:
            return
        for row in self.links(resource_type):
            if row.get("external_id") == external_id and row["local_id"] != local_id:
                raise ScimError(
                    409,
                    f"externalId {external_id!r} is another {resource_type}'s",
                    UNIQUENESS,
                )

    def _create_link(
        self, resource_type: str, local_id: str, external_id: Optional[str]
    ) -> None:
        self.links_db.create(
            requester_id=self.root_id,
            model_registry=self.registry,
            scim_connection_id=self.connection.id,
            resource_type=resource_type,
            local_id=local_id,
            external_id=external_id,
        )

    def _update_link(self, link_id: str, **properties: Any) -> None:
        self.links_db.update(
            requester_id=self.root_id,
            model_registry=self.registry,
            id=link_id,
            new_properties=properties,
        )

    # -- users ------------------------------------------------------------

    @staticmethod
    def _valid(fields: UserFields) -> UserFields:
        try:
            UserModel.Update(email=fields.email)
        except ValueError:
            raise bad_request(
                INVALID_VALUE, f"{fields.email!r} is not an email address"
            ) from None
        return fields

    def _accounts(
        self, fields: UserFields, other_than: Optional[str]
    ) -> List[Dict[str, Any]]:
        """Accounts holding ``fields``' userName or email (ignoring case)."""
        rows = self._rows(
            self.users_db,
            or_(
                func.lower(self.users_db.username) == fields.username.lower(),
                func.lower(self.users_db.email) == fields.email.lower(),
            ),
        )
        return [row for row in rows if row["id"] != other_than]

    def _write_user(self, user_id: str, fields: UserFields) -> None:
        UserManager(requester_id=self.root_id, model_registry=self.registry).update(
            id=user_id, **fields.columns()
        )

    def create_user(self, payload: Any) -> Dict[str, Any]:
        fields = self._valid(parse_user(payload))
        self._external_id_free(USER, fields.external_id, None)
        accounts = self._accounts(fields, None)
        if len(accounts) > 1:
            raise ScimError(
                409, "The userName and email belong to different accounts", UNIQUENESS
            )
        if accounts:
            account_id = self._adopt(accounts[0]["id"], fields)
        elif not self.connection.auto_create_users:
            raise ScimError(
                403, "This connection links existing accounts only; none matches"
            )
        else:
            created = self.users_db.create(
                requester_id=self.root_id,
                model_registry=self.registry,
                return_type="dict",
                **fields.columns(),
            )
            account_id = created["id"]
            self._create_link(USER, account_id, fields.external_id)
        return self.resource(USER, account_id)

    def _adopt(self, account_id: str, fields: UserFields) -> str:
        """Link an existing account: one this connection provisioned before
        (and the provider deleted), or, when the connection adopts
        accounts, any account but the internal ones."""
        previous = [
            row for row in self.links(USER, ended=True) if row["local_id"] == account_id
        ]
        if any(row.get("deprovisioned_at") is None for row in previous):
            raise ScimError(409, "That user is already provisioned", UNIQUENESS)
        if not previous and (
            not self.connection.link_existing_users or is_any_internal_id(account_id)
        ):
            raise ScimError(
                409, "An account with that userName or email exists", UNIQUENESS
            )
        self._write_user(account_id, fields)
        if previous:
            self._update_link(
                previous[-1]["id"],
                external_id=fields.external_id,
                deprovisioned_at=None,
            )
        else:
            self._create_link(USER, account_id, fields.external_id)
        return account_id

    def _store_user(self, user_id: str, fields: UserFields) -> Dict[str, Any]:
        link = self.link(USER, user_id)
        if self._accounts(fields, user_id):
            raise ScimError(
                409, "Another account has that userName or email", UNIQUENESS
            )
        self._external_id_free(USER, fields.external_id, user_id)
        self._write_user(user_id, fields)
        if link.get("external_id") != fields.external_id:
            self._update_link(link["id"], external_id=fields.external_id)
        return self.resource(USER, user_id)

    def replace_user(
        self, user_id: str, payload: Any, if_match: Optional[str]
    ) -> Dict[str, Any]:
        self.precondition(self.resource(USER, user_id), if_match)
        return self._store_user(user_id, self._valid(parse_user(payload)))

    def patch_user(
        self, user_id: str, payload: Any, if_match: Optional[str]
    ) -> Dict[str, Any]:
        current = self.resource(USER, user_id)
        self.precondition(current, if_match)
        patched = apply_patch(current, payload, USER_READ_ONLY)
        return self._store_user(user_id, self._valid(parse_user(patched)))

    def delete_user(self, user_id: str, if_match: Optional[str]) -> None:
        self.precondition(self.resource(USER, user_id), if_match)
        link = self.link(USER, user_id)
        for team_link in self.links(GROUP):
            self._set_members(team_link["local_id"], remove={user_id})
        UserManager(requester_id=self.root_id, model_registry=self.registry).update(
            id=user_id, active=False
        )
        self._update_link(link["id"], deprovisioned_at=_now())
        if self.connection.delete_on_deprovision:
            self.users_db.delete(
                requester_id=self.root_id, model_registry=self.registry, id=user_id
            )

    # -- groups -----------------------------------------------------------

    def _role_id(self) -> str:
        role: str = self.connection.role_id or env("USER_ROLE_ID")
        return role

    def _check_group(self, fields: GroupFields, team_id: Optional[str]) -> Set[str]:
        """The members' ids, once the name, externalId and members check."""
        try:
            TeamModel.Update(name=fields.display_name)
        except ValueError:
            raise bad_request(INVALID_VALUE, "displayName is not a team name") from None
        for group in self.resources(GROUP):
            if (
                group["id"] != team_id
                and str(group.get("displayName", "")).lower()
                == fields.display_name.lower()
            ):
                raise ScimError(
                    409, f"A group is named {fields.display_name!r}", UNIQUENESS
                )
        self._external_id_free(GROUP, fields.external_id, team_id)
        users = {link["local_id"] for link in self.links(USER)}
        strangers = [m for m in fields.member_ids if m not in users]
        if strangers:
            raise bad_request(
                INVALID_VALUE, f"Not users of this connection: {', '.join(strangers)}"
            )
        return set(fields.member_ids)

    def _set_members(
        self,
        team_id: str,
        wanted: Optional[Set[str]] = None,
        remove: Optional[Set[str]] = None,
    ) -> None:
        """Make the connection's users in the team exactly ``wanted``, or
        take ``remove`` out; members the connection did not provision are
        left as they are."""
        ours = {link["local_id"] for link in self.links(USER)}
        held: Dict[str, List[str]] = {}
        for row in self._memberships({team_id}, ours):
            held.setdefault(row["user_id"], []).append(row["id"])
        leaving = (
            (set(held) - wanted)
            if wanted is not None
            else set(held) & (remove or set())
        )
        memberships = UserTeamManager(
            requester_id=self.root_id, model_registry=self.registry
        )
        for user_id in sorted(leaving):
            for membership_id in held[user_id]:
                memberships.delete(id=membership_id)
        for user_id in sorted((wanted or set()) - set(held)):
            memberships.create(
                team_id=team_id, user_id=user_id, role_id=self._role_id()
            )

    def create_group(self, payload: Any) -> Dict[str, Any]:
        fields = parse_group(payload)
        members = self._check_group(fields, None)
        # Created as system, not through TeamManager, which would make the
        # creator an admin member of every provisioned team.
        team = self.teams_db.create(
            requester_id=self.system_id,
            model_registry=self.registry,
            return_type="dict",
            name=fields.display_name,
        )
        self._create_link(GROUP, team["id"], fields.external_id)
        self._set_members(team["id"], wanted=members)
        return self.resource(GROUP, team["id"])

    def _store_group(self, team_id: str, fields: GroupFields) -> Dict[str, Any]:
        link = self.link(GROUP, team_id)
        members = self._check_group(fields, team_id)
        TeamManager(requester_id=self.system_id, model_registry=self.registry).update(
            id=team_id, name=fields.display_name
        )
        if link.get("external_id") != fields.external_id:
            self._update_link(link["id"], external_id=fields.external_id)
        self._set_members(team_id, wanted=members)
        return self.resource(GROUP, team_id)

    def replace_group(
        self, team_id: str, payload: Any, if_match: Optional[str]
    ) -> Dict[str, Any]:
        self.precondition(self.resource(GROUP, team_id), if_match)
        return self._store_group(team_id, parse_group(payload))

    def patch_group(
        self, team_id: str, payload: Any, if_match: Optional[str]
    ) -> Dict[str, Any]:
        current = self.resource(GROUP, team_id)
        self.precondition(current, if_match)
        patched = apply_patch(current, payload, GROUP_READ_ONLY)
        return self._store_group(team_id, parse_group(patched))

    def delete_group(self, team_id: str, if_match: Optional[str]) -> None:
        self.precondition(self.resource(GROUP, team_id), if_match)
        link = self.link(GROUP, team_id)
        self._set_members(team_id, wanted=set())
        self.teams_db.delete(
            requester_id=self.system_id, model_registry=self.registry, id=team_id
        )
        self._update_link(link["id"], deprovisioned_at=_now())


# ---------------------------------------------------------------------------
# The protocol endpoints
# ---------------------------------------------------------------------------


def _scim_route(method: str, path: str, summary: str, body: bool = False) -> Any:
    """``@custom_route`` for a SCIM endpoint: public at the framework's
    door (the connection's bearer token is checked here), REST only, and
    answering SCIM's own media type."""
    return custom_route(
        method=method,
        path=path,
        input_model=ScimBody if body else None,
        authentication_type="none",
        openapi_tags=("SCIM 2.0",),
        expose_in=(ExposeIn.REST,),
        response_class=ScimResponse,
        summary=summary,
    )


Action = Callable[[ScimDirectory], ScimResponse]


class ScimResourceManager(AbstractBLLManager, RouterMixin):
    """The SCIM 2.0 protocol, for the connection whose token is presented."""

    _model = ScimResourceModel

    prefix: ClassVar[Optional[str]] = SCIM_PREFIX
    tags: ClassVar[Optional[List[str]]] = ["SCIM 2.0"]
    auth_type: ClassVar[AuthType] = AuthType.NONE
    routes_to_register: ClassVar[Optional[List[RouteType]]] = []

    def _connection(self, request: Request) -> Any:
        scheme, _, credential = request.headers.get("authorization", "").partition(" ")
        refused = ScimError(
            401,
            "A valid connection bearer token is required",
            headers={"WWW-Authenticate": 'Bearer realm="scim"'},
        )
        if scheme.lower() != "bearer" or not credential.strip():
            raise refused
        digest = token_digest(credential.strip())
        ConnectionDB = ScimConnectionModel.DB(self.model_registry.DB.manager.Base)
        found = ConnectionDB.list(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            filters=[
                ConnectionDB.token_hash == digest,
                ConnectionDB.deleted_at.is_(None),
            ],
            return_type="db",
        )
        for connection in found:
            if connection.is_enabled and hmac.compare_digest(
                connection.token_hash or "", digest
            ):
                return connection
        raise refused

    def _base(self, request: Request) -> str:
        return str(request.base_url).rstrip("/") + SCIM_PREFIX

    def _serve(self, request: Request, action: Action) -> ScimResponse:
        try:
            connection = self._connection(request)
            return action(
                ScimDirectory(self.model_registry, connection, self._base(request))
            )
        except ScimError as error:
            return _error_response(error)

    def _change(
        self,
        request: Request,
        resource_type: str,
        operation: str,
        scim_id: Optional[str],
        action: Action,
    ) -> ScimResponse:
        """``_serve`` for a change, logged whichever way it goes."""

        def logged(directory: ScimDirectory) -> ScimResponse:
            try:
                response = action(directory)
            except ScimError as error:
                self._log(
                    directory,
                    resource_type,
                    operation,
                    scim_id,
                    error.status,
                    error.detail,
                )
                raise
            self._log(
                directory, resource_type, operation, scim_id, response.status_code, None
            )
            return response

        return self._serve(request, logged)

    def _log(
        self,
        directory: ScimDirectory,
        resource_type: str,
        operation: str,
        scim_id: Optional[str],
        http_status: int,
        error_detail: Optional[str],
    ) -> None:
        ScimProvisioningLogModel.DB(self.model_registry.DB.manager.Base).create(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            scim_connection_id=directory.connection.id,
            resource_type=resource_type,
            operation=operation,
            scim_id=scim_id,
            status=STATUS_ERROR if error_detail else STATUS_SUCCESS,
            http_status=http_status,
            error_detail=error_detail,
            received_at=_now(),
        )

    @staticmethod
    def _resource_response(
        resource: Dict[str, Any], status_code: int = 200, created: bool = False
    ) -> ScimResponse:
        headers = {"ETag": resource["meta"]["version"]}
        if created:
            headers["Location"] = resource["meta"]["location"]
        return ScimResponse(resource, status_code=status_code, headers=headers)

    def _list(self, request: Request, resource_type: str) -> ScimResponse:
        query = request.query_params

        def answer(directory: ScimDirectory) -> ScimResponse:
            start, size = page_bounds(query.get("startIndex"), query.get("count"))
            selected = filter_resources(
                directory.resources(resource_type), query.get("filter")
            )
            ordered = sort_resources(
                selected, query.get("sortBy"), query.get("sortOrder")
            )
            shown = [
                project(r, query.get("attributes"), query.get("excludedAttributes"))
                for r in ordered
            ]
            return ScimResponse(list_response(shown, start, size))

        return self._serve(request, answer)

    def _get(self, request: Request, resource_type: str, local_id: str) -> ScimResponse:
        query = request.query_params

        def answer(directory: ScimDirectory) -> ScimResponse:
            resource = directory.resource(resource_type, local_id)
            version = resource["meta"]["version"]
            if etag_matches(request.headers.get("if-none-match"), version):
                return ScimResponse(None, status_code=304, headers={"ETag": version})
            shown = project(
                resource, query.get("attributes"), query.get("excludedAttributes")
            )
            return ScimResponse(shown, headers={"ETag": version})

        return self._serve(request, answer)

    # -- discovery ----------------------------------------------------------

    @_scim_route("GET", "/ServiceProviderConfig", "What this SCIM service supports")
    @rate_limit(PROTOCOL_RATE_LIMIT, scope="ip")
    def service_provider_config_route(self, request: Request) -> ScimResponse:
        return self._serve(
            request,
            lambda d: ScimResponse(
                service_provider_config(f"{d.base}/ServiceProviderConfig")
            ),
        )

    @_scim_route("GET", "/ResourceTypes", "The resource types served")
    @rate_limit(PROTOCOL_RATE_LIMIT, scope="ip")
    def resource_types_route(self, request: Request) -> ScimResponse:
        def answer(directory: ScimDirectory) -> ScimResponse:
            found = resource_types(f"{directory.base}/ResourceTypes")
            return ScimResponse(list_response(found, 1, len(found)))

        return self._serve(request, answer)

    @_scim_route("GET", "/Schemas", "The schemas served")
    @rate_limit(PROTOCOL_RATE_LIMIT, scope="ip")
    def schemas_route(self, request: Request) -> ScimResponse:
        def answer(directory: ScimDirectory) -> ScimResponse:
            found = schemas(f"{directory.base}/Schemas")
            return ScimResponse(list_response(found, 1, len(found)))

        return self._serve(request, answer)

    # -- users --------------------------------------------------------------

    @_scim_route("GET", "/Users", "List or filter the connection's users")
    @rate_limit(PROTOCOL_RATE_LIMIT, scope="ip")
    def list_users_route(self, request: Request) -> ScimResponse:
        return self._list(request, USER)

    @_scim_route("POST", "/Users", "Provision a user", body=True)
    @rate_limit(PROTOCOL_RATE_LIMIT, scope="ip")
    def create_user_route(self, request: Request, body: ScimBody) -> ScimResponse:
        payload = body.model_dump()
        return self._change(
            request,
            USER,
            OPERATION_CREATE,
            None,
            lambda d: self._resource_response(
                d.create_user(payload), 201, created=True
            ),
        )

    @_scim_route("GET", "/Users/{user_id}", "A user")
    @rate_limit(PROTOCOL_RATE_LIMIT, scope="ip")
    def get_user_route(self, request: Request, user_id: str) -> ScimResponse:
        return self._get(request, USER, user_id)

    @_scim_route("PUT", "/Users/{user_id}", "Replace a user", body=True)
    @rate_limit(PROTOCOL_RATE_LIMIT, scope="ip")
    def replace_user_route(
        self, request: Request, user_id: str, body: ScimBody
    ) -> ScimResponse:
        payload, if_match = body.model_dump(), request.headers.get("if-match")
        return self._change(
            request,
            USER,
            OPERATION_REPLACE,
            user_id,
            lambda d: self._resource_response(
                d.replace_user(user_id, payload, if_match)
            ),
        )

    @_scim_route("PATCH", "/Users/{user_id}", "Change a user", body=True)
    @rate_limit(PROTOCOL_RATE_LIMIT, scope="ip")
    def patch_user_route(
        self, request: Request, user_id: str, body: ScimBody
    ) -> ScimResponse:
        payload, if_match = body.model_dump(), request.headers.get("if-match")
        return self._change(
            request,
            USER,
            OPERATION_PATCH,
            user_id,
            lambda d: self._resource_response(d.patch_user(user_id, payload, if_match)),
        )

    @_scim_route("DELETE", "/Users/{user_id}", "Deprovision a user")
    @rate_limit(PROTOCOL_RATE_LIMIT, scope="ip")
    def delete_user_route(self, request: Request, user_id: str) -> ScimResponse:
        if_match = request.headers.get("if-match")

        def answer(directory: ScimDirectory) -> ScimResponse:
            directory.delete_user(user_id, if_match)
            return ScimResponse(None, status_code=204)

        return self._change(request, USER, OPERATION_DELETE, user_id, answer)

    # -- groups -------------------------------------------------------------

    @_scim_route("GET", "/Groups", "List or filter the connection's groups")
    @rate_limit(PROTOCOL_RATE_LIMIT, scope="ip")
    def list_groups_route(self, request: Request) -> ScimResponse:
        return self._list(request, GROUP)

    @_scim_route("POST", "/Groups", "Provision a group", body=True)
    @rate_limit(PROTOCOL_RATE_LIMIT, scope="ip")
    def create_group_route(self, request: Request, body: ScimBody) -> ScimResponse:
        payload = body.model_dump()
        return self._change(
            request,
            GROUP,
            OPERATION_CREATE,
            None,
            lambda d: self._resource_response(
                d.create_group(payload), 201, created=True
            ),
        )

    @_scim_route("GET", "/Groups/{group_id}", "A group")
    @rate_limit(PROTOCOL_RATE_LIMIT, scope="ip")
    def get_group_route(self, request: Request, group_id: str) -> ScimResponse:
        return self._get(request, GROUP, group_id)

    @_scim_route("PUT", "/Groups/{group_id}", "Replace a group", body=True)
    @rate_limit(PROTOCOL_RATE_LIMIT, scope="ip")
    def replace_group_route(
        self, request: Request, group_id: str, body: ScimBody
    ) -> ScimResponse:
        payload, if_match = body.model_dump(), request.headers.get("if-match")
        return self._change(
            request,
            GROUP,
            OPERATION_REPLACE,
            group_id,
            lambda d: self._resource_response(
                d.replace_group(group_id, payload, if_match)
            ),
        )

    @_scim_route("PATCH", "/Groups/{group_id}", "Change a group", body=True)
    @rate_limit(PROTOCOL_RATE_LIMIT, scope="ip")
    def patch_group_route(
        self, request: Request, group_id: str, body: ScimBody
    ) -> ScimResponse:
        payload, if_match = body.model_dump(), request.headers.get("if-match")
        return self._change(
            request,
            GROUP,
            OPERATION_PATCH,
            group_id,
            lambda d: self._resource_response(
                d.patch_group(group_id, payload, if_match)
            ),
        )

    @_scim_route("DELETE", "/Groups/{group_id}", "Delete a group")
    @rate_limit(PROTOCOL_RATE_LIMIT, scope="ip")
    def delete_group_route(self, request: Request, group_id: str) -> ScimResponse:
        if_match = request.headers.get("if-match")

        def answer(directory: ScimDirectory) -> ScimResponse:
            directory.delete_group(group_id, if_match)
            return ScimResponse(None, status_code=204)

        return self._change(request, GROUP, OPERATION_DELETE, group_id, answer)
