# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bring one SCIM target in line with the framework's users and teams.

A target is a ``scim_target`` provider instance. What it is given is read
through the framework's managers *as the instance's owner*: a root (or
system) instance pushes every user and team; any other owner's instance
only the owner, the owner's teams (with their sub-teams) and those of
their members the owner can read, since a target is an export to a third
party. A ``team_id`` setting narrows it to
that team, its sub-teams, and their members.

Each local record a target knows of has a link (``ScimLinkModel``): the
remote id, the last ETag, a digest of what was last pushed, and whether
a push is pending. Pushes are state-based, not event-based: a pending
link is pushed as the local record now stands, so a burst of changes
becomes one request and their order does not matter.

- A user with no remote id is looked up by ``userName`` (``userName eq
  "…"``, quoted as a JSON string) and linked when found, else created; a
  409 from the create links the existing user of that ``userName``.
- A known user is PATCHed (a path-less ``replace``) when the target
  supports PATCH, else PUT, with ``If-Match`` when it supports ETags; a
  412 rereads the version and retries once, a 404 recreates it.
- A user who is gone (deleted, out of scope, or no longer visible to the
  owner) is deactivated (``active: false``), or deleted when the target's
  ``delete_mode`` is ``delete``.
- Teams become groups after the users (members are remote user ids).
  Membership changes are PATCHed as ``add``/``remove`` operations; a gone
  team's group is emptied, or deleted under ``delete_mode = delete``.

A full reconcile reads every remote user and group (paged), links by
``externalId`` or name, and pushes whatever differs from the local state.
Every remote write is recorded in the sync log.
"""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Set,
)

from fastapi import HTTPException

from zephyrex.extensions.ExternalErrors import (
    AuthExternalError,
    BaseExternalError,
    InvalidInputExternalError,
    RateLimitExternalError,
)
from zephyrex.extensions.scim_provider.PRV_SCIM import (
    DELETE_MODES,
    GROUP_SCHEMA,
    USER_NAME_SOURCES,
    USER_SCHEMA,
    PRV_SCIM_SCIMProvider,
    RemoteResource,
    ServiceProviderConfig,
    scim_string,
)
from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger
from zephyrex.logic.AbstractLogicManager.ownership import server_side
from zephyrex.logic.BLL_Providers import (
    InstanceOwner,
    ProviderInstanceManager,
    ProviderInstanceModel,
    ProviderManager,
    instance_owner,
)

USER = "User"
GROUP = "Group"
NAME_ATTRIBUTES = {USER: "userName", GROUP: "displayName"}
LOCAL_PAGE_SIZE = 500
MAX_TEAM_DEPTH = 32
ERROR_DETAIL_CHARS = 500
OUTCOMES = (
    "created",
    "linked",
    "updated",
    "unchanged",
    "deactivated",
    "deleted",
    "failed",
)

Client = PRV_SCIM_SCIMProvider
Write = Callable[[Optional[str]], Awaitable[Any]]
Differs = Callable[[Mapping[str, Any], Mapping[str, Any]], bool]


def system_ids() -> Set[str]:
    return {env("ROOT_ID"), env("SYSTEM_ID"), env("TEMPLATE_ID")}


def digest(resource: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(resource, sort_keys=True).encode()).hexdigest()


def _folded(value: Any) -> Optional[str]:
    return str(value).casefold() if value is not None else None


def primary_email(resource: Mapping[str, Any]) -> Optional[str]:
    emails = [e for e in resource.get("emails") or [] if isinstance(e, Mapping)]
    chosen = next((e for e in emails if e.get("primary")), None) or (
        emails[0] if emails else None
    )
    return _folded(chosen.get("value")) if chosen else None


def member_ids(resource: Mapping[str, Any]) -> Optional[Set[str]]:
    """The member ids a group lists, or None when it leaves them out."""
    if "members" not in resource:
        return None
    return {
        str(m["value"])
        for m in resource.get("members") or []
        if isinstance(m, Mapping) and m.get("value")
    }


def user_differs(desired: Mapping[str, Any], remote: Mapping[str, Any]) -> bool:
    name, remote_name = desired.get("name") or {}, remote.get("name") or {}
    return (
        _folded(desired.get("userName")) != _folded(remote.get("userName"))
        or bool(desired.get("active")) != bool(remote.get("active", True))
        or desired.get("externalId") != remote.get("externalId")
        or desired.get("displayName") != remote.get("displayName")
        or name.get("givenName") != remote_name.get("givenName")
        or name.get("familyName") != remote_name.get("familyName")
        or primary_email(desired) != primary_email(remote)
    )


def group_differs(desired: Mapping[str, Any], remote: Mapping[str, Any]) -> bool:
    members = member_ids(remote)
    return (
        desired.get("displayName") != remote.get("displayName")
        or desired.get("externalId") != remote.get("externalId")
        or (members is not None and members != member_ids(desired))
    )


def still_provisioned(resource_type: str, current: RemoteResource) -> bool:
    """Whether a remote record taken off earlier is back in use."""
    if resource_type == USER:
        return bool(current.body.get("active", True))
    return bool(member_ids(current.body))


def _expired(moment: Optional[datetime], now: datetime) -> bool:
    if moment is None:
        return False
    aware = moment if moment.tzinfo else moment.replace(tzinfo=UTC)
    return aware <= now


@dataclass
class RemoteIndex:
    """Every remote resource of one type, by id, ``externalId`` and name."""

    name_attribute: str
    by_id: Dict[str, RemoteResource] = field(default_factory=dict)
    by_external_id: Dict[str, RemoteResource] = field(default_factory=dict)
    by_name: Dict[str, RemoteResource] = field(default_factory=dict)

    @classmethod
    def of(cls, name_attribute: str, found: Iterable[RemoteResource]) -> "RemoteIndex":
        index = cls(name_attribute)
        for resource in found:
            index.by_id[resource.id] = resource
            external = resource.body.get("externalId")
            if external:
                index.by_external_id.setdefault(str(external), resource)
            name = _folded(resource.body.get(name_attribute))
            if name:
                index.by_name.setdefault(name, resource)
        return index

    def match(self, local_id: str, name: str) -> Optional[RemoteResource]:
        return self.by_external_id.get(local_id) or self.by_name.get(name.casefold())


@dataclass
class Summary:
    """What one run did, per resource type and outcome."""

    target: str
    counts: Dict[str, Dict[str, int]] = field(
        default_factory=lambda: {
            USER: dict.fromkeys(OUTCOMES, 0),
            GROUP: dict.fromkeys(OUTCOMES, 0),
        }
    )

    def add(self, resource_type: str, outcome: str) -> None:
        self.counts[resource_type][outcome] += 1

    def as_dict(self) -> Dict[str, Any]:
        return {
            "target": self.target,
            "users": self.counts[USER],
            "groups": self.counts[GROUP],
        }


def live(manager: Any, **match: Any) -> List[Any]:
    """``manager.list(**match)`` without soft-deleted rows. ROOT's reads
    include them (for audit), and a root-owned target reads as ROOT: a
    deleted user, team, membership or link must read as gone."""
    found = manager.list(filters=[manager.DB.deleted_at.is_(None)], **match)
    return list(found or [])


def live_one(manager: Any, record_id: str) -> Any:
    """The live record ``record_id``, or None when it is deleted or the
    requester may not see it."""
    try:
        found = live(manager, id=record_id)
    except HTTPException as exc:
        if exc.status_code in (403, 404):
            return None
        raise
    return found[0] if found else None


def scim_provider_id(registry: Any) -> str:
    provider = ProviderManager(model_registry=registry, requester_id=env("ROOT_ID"))
    return str(provider.get(name=Client.name).id)


def target_instances(registry: Any) -> List[ProviderInstanceModel]:
    """The enabled SCIM targets."""
    instances = ProviderInstanceManager(
        model_registry=registry, requester_id=env("ROOT_ID")
    )
    return [
        ProviderInstanceModel.model_validate(instance, from_attributes=True)
        for instance in live(instances, provider_id=scim_provider_id(registry))
        if instance.enabled is not False
    ]


def target_instance(registry: Any, instance_ref: str) -> ProviderInstanceModel:
    """The enabled SCIM target ``instance_ref`` (its id or name); 404
    otherwise."""
    for instance in target_instances(registry):
        if instance_ref in (instance.id, instance.name):
            return instance
    raise HTTPException(status_code=404, detail=f"no SCIM target {instance_ref!r}")


class SCIMSync:
    """One run against one target: built with :meth:`open`, which reads the
    target's settings and its ServiceProviderConfig."""

    def __init__(
        self,
        registry: Any,
        instance: ProviderInstanceModel,
        owner: InstanceOwner,
        config: ServiceProviderConfig,
    ) -> None:
        from zephyrex.extensions.scim_provider.BLL_SCIMProvider import (
            ScimLinkManager,
            ScimSyncLogManager,
        )
        from zephyrex.logic.BLL_Auth import TeamManager, UserManager, UserTeamManager

        self.instance = instance
        self.config = config
        self.delete_mode = Client.choice(instance, "delete_mode", DELETE_MODES)
        self.user_name_source = Client.choice(instance, "user_name", USER_NAME_SOURCES)
        self.scope_team_id = (Client.setting(instance, "team_id") or "").strip()
        self.push_groups = Client.flag(instance, "push_groups")
        self.owner_id = str(owner.requester_id)
        self.unrestricted = server_side(self.owner_id)
        acting = {"model_registry": registry, "requester_id": owner.requester_id}
        self.links = ScimLinkManager(**acting)
        self.log = ScimSyncLogManager(**acting)
        self.users = UserManager(**acting)
        self.teams = TeamManager(**acting)
        self.memberships = UserTeamManager(**acting)
        self.summary = Summary(target=str(instance.id))
        self._scope_teams: Optional[Set[str]] = None
        self._scope_users: Optional[Set[str]] = None
        self._remote_users: Optional[Dict[str, str]] = None

    @classmethod
    async def open(cls, registry: Any, instance: ProviderInstanceModel) -> "SCIMSync":
        config = await Client.service_provider_config(instance)
        return cls(registry, instance, instance_owner(registry, instance.id), config)

    # ----- local state, read as the owner ----------------------------------

    def _with_sub_teams(self, roots: Set[str]) -> Set[str]:
        found: Set[str] = set()
        level = set(roots)
        for _ in range(MAX_TEAM_DEPTH):
            level = {
                team_id
                for team_id in level - found
                if live_one(self.teams, team_id) is not None
            }
            if not level:
                break
            found |= level
            level = {
                str(child.id)
                for parent in level
                for child in live(self.teams, parent_id=parent)
            }
        return found

    def _live_memberships(self, **match: Any) -> List[Any]:
        now = datetime.now(UTC)
        return [
            membership
            for membership in live(self.memberships, **match)
            if membership.enabled is not False
            and not _expired(membership.expires_at, now)
        ]

    def scope_teams(self) -> Optional[Set[str]]:
        """The teams (with their sub-teams) the target is given; None when
        it is given every team.

        A root (or system) target is given every team, or the ``team_id``
        team. Any other owner's target is bounded by the owner's own
        teams: it is given those teams, or the ``team_id`` team only where
        it lies within them."""
        if self.unrestricted and not self.scope_team_id:
            return None
        if self._scope_teams is None:
            scoped = (
                self._with_sub_teams({self.scope_team_id})
                if self.scope_team_id
                else None
            )
            if self.unrestricted:
                self._scope_teams = scoped or set()
            else:
                own = self._with_sub_teams(
                    {
                        str(m.team_id)
                        for m in self._live_memberships(user_id=self.owner_id)
                    }
                )
                self._scope_teams = own if scoped is None else scoped & own
        return self._scope_teams

    def team_members(self, team_id: str) -> Set[str]:
        return {str(m.user_id) for m in self._live_memberships(team_id=team_id)}

    def scope_users(self) -> Optional[Set[str]]:
        """The users the target is given; None when it is given every
        user. Members of its teams, and a non-root owner themself."""
        teams = self.scope_teams()
        if teams is None:
            return None
        if self._scope_users is None:
            users = {user for team in teams for user in self.team_members(team)}
            if not self.unrestricted and not self.scope_team_id:
                users.add(self.owner_id)
            self._scope_users = users
        return self._scope_users

    def local_user(self, user_id: str) -> Any:
        scoped = self.scope_users()
        if user_id in system_ids() or (scoped is not None and user_id not in scoped):
            return None
        return live_one(self.users, user_id)

    def local_team(self, team_id: str) -> Any:
        teams = self.scope_teams()
        if team_id in system_ids() or (teams is not None and team_id not in teams):
            return None
        return live_one(self.teams, team_id)

    def local(self, resource_type: str, local_id: str) -> Any:
        if resource_type == USER:
            return self.local_user(local_id)
        return self.local_team(local_id)

    @staticmethod
    def _every(manager: Any) -> List[Any]:
        found: List[Any] = []
        offset = 0
        while True:
            page = live(manager, limit=LOCAL_PAGE_SIZE, offset=offset)
            found.extend(page)
            if len(page) < LOCAL_PAGE_SIZE:
                return found
            offset += LOCAL_PAGE_SIZE

    def local_records(self, resource_type: str) -> Dict[str, Any]:
        """Every local record of ``resource_type`` the target is given."""
        scoped = self.scope_users() if resource_type == USER else self.scope_teams()
        if scoped is not None:
            found = {
                local_id: self.local(resource_type, local_id) for local_id in scoped
            }
            return {local_id: rec for local_id, rec in found.items() if rec is not None}
        manager = self.users if resource_type == USER else self.teams
        return {
            str(record.id): record
            for record in self._every(manager)
            if str(record.id) not in system_ids()
        }

    # ----- what the target is given ----------------------------------------

    def user_resource(self, user: Any) -> Optional[Dict[str, Any]]:
        """``user`` as a SCIM User, or None when it has no ``userName``."""
        user_name = user.email if self.user_name_source == "email" else user.username
        if not user_name:
            return None
        formatted = " ".join(p for p in (user.first_name, user.last_name) if p)
        name = {
            key: value
            for key, value in (
                ("givenName", user.first_name),
                ("familyName", user.last_name),
                ("formatted", formatted),
            )
            if value
        }
        resource: Dict[str, Any] = {
            "schemas": [USER_SCHEMA],
            "externalId": str(user.id),
            "userName": user_name,
            "displayName": user.display_name or formatted or user_name,
            "active": user.active is not False,
        }
        if name:
            resource["name"] = name
        if user.email:
            resource["emails"] = [
                {"value": user.email, "type": "work", "primary": True}
            ]
        if user.language:
            resource["preferredLanguage"] = user.language
        if user.timezone:
            resource["timezone"] = user.timezone
        return resource

    def remote_user_ids(self) -> Dict[str, str]:
        """Local user id -> remote id, for the users this target holds.
        Read once a run, after the users are pushed."""
        if self._remote_users is None:
            self._remote_users = {
                str(link.local_id): str(link.remote_id)
                for link in live(
                    self.links,
                    provider_instance_id=self.instance.id,
                    resource_type=USER,
                )
                if link.remote_id and link.status == "synced"
            }
        return self._remote_users

    def group_resource(self, team: Any) -> Dict[str, Any]:
        remote = self.remote_user_ids()
        members = sorted(
            remote[user] for user in self.team_members(str(team.id)) if user in remote
        )
        return {
            "schemas": [GROUP_SCHEMA],
            "externalId": str(team.id),
            "displayName": team.name,
            "members": [{"value": member} for member in members],
        }

    def desired(self, resource_type: str, record: Any) -> Optional[Dict[str, Any]]:
        if record is None:
            return None
        if resource_type == USER:
            return self.user_resource(record)
        return self.group_resource(record)

    # ----- links and the log -----------------------------------------------

    def link_for(self, resource_type: str, local_id: str) -> Any:
        found = live(
            self.links,
            provider_instance_id=self.instance.id,
            resource_type=resource_type,
            local_id=local_id,
        )
        if found:
            return found[0]
        return self.links.create(
            provider_instance_id=self.instance.id,
            resource_type=resource_type,
            local_id=local_id,
            pending=True,
            status="pending",
        )

    def record(
        self,
        resource_type: str,
        operation: str,
        local_id: str,
        remote_id: Optional[str],
        error: Optional[BaseException] = None,
    ) -> None:
        self.log.create(
            provider_instance_id=self.instance.id,
            resource_type=resource_type,
            operation=operation,
            local_id=local_id,
            remote_id=remote_id,
            status="error" if error else "success",
            error_detail=str(error)[:ERROR_DETAIL_CHARS] if error else None,
        )

    def synced(
        self,
        link: Any,
        desired: Mapping[str, Any],
        remote: Optional[RemoteResource],
    ) -> None:
        """The link after a push that left the remote record as
        ``desired``; ``remote`` is the record as written, when the target
        answered with it."""
        values: Dict[str, Any] = {
            "pending": False,
            "status": "synced",
            "last_synced_at": datetime.now(UTC),
            "last_error": None,
            "pushed_hash": digest(desired),
            "remote_name": str(desired[NAME_ATTRIBUTES[link.resource_type]]),
        }
        if remote is not None:
            values["remote_id"] = remote.id
            values["remote_etag"] = remote.etag
        self.links.update(link.id, **values)

    def linked(self, link: Any, remote: RemoteResource) -> Any:
        self.record(link.resource_type, "link", link.local_id, remote.id)
        return self.links.update(link.id, remote_id=remote.id, remote_etag=remote.etag)

    # ----- remote writes ---------------------------------------------------

    async def _with_fresh_etag(
        self, resource_type: str, remote_id: str, etag: Optional[str], write: Write
    ) -> Any:
        """``write(if_match)``, with ``If-Match`` only when the target
        supports ETags; on 412 the version is reread and the write made
        once more against it."""
        if not self.config.etag.supported:
            return await write(None)
        try:
            return await write(etag)
        except InvalidInputExternalError as exc:
            if exc.upstream_status != 412:
                raise
        fresh = await Client.get(self.instance, resource_type, remote_id)
        return await write(fresh.etag)

    async def _create(self, link: Any, desired: Dict[str, Any]) -> str:
        resource_type = str(link.resource_type)
        name_attribute = NAME_ATTRIBUTES[resource_type]
        try:
            remote = await Client.create(self.instance, resource_type, desired)
        except InvalidInputExternalError as exc:
            if exc.upstream_status != 409:
                raise
            existing = await Client.find(
                self.instance,
                resource_type,
                name_attribute,
                str(desired[name_attribute]),
            )
            if existing is None:
                raise
            link = self.linked(link, existing)
            self.synced(link, desired, await self._update(link, desired, existing))
            return "linked"
        self.record(resource_type, "create", link.local_id, remote.id)
        self.synced(link, desired, remote)
        return "created"

    async def _update(
        self, link: Any, desired: Dict[str, Any], current: Optional[RemoteResource]
    ) -> Optional[RemoteResource]:
        """PATCH (when supported) or PUT ``desired`` over the remote record;
        the record as written, when the target answers with it."""
        resource_type, remote_id = str(link.resource_type), str(link.remote_id)
        written: Optional[RemoteResource]
        if self.config.patch.supported:
            operations = await self._operations(
                resource_type, remote_id, desired, current
            )

            async def patch(if_match: Optional[str]) -> Optional[RemoteResource]:
                return await Client.patch(
                    self.instance, resource_type, remote_id, operations, if_match
                )

            written = await self._with_fresh_etag(
                resource_type, remote_id, link.remote_etag, patch
            )
            self.record(resource_type, "patch", link.local_id, remote_id)
            return written

        async def put(if_match: Optional[str]) -> RemoteResource:
            return await Client.replace(
                self.instance, resource_type, remote_id, desired, if_match
            )

        written = await self._with_fresh_etag(
            resource_type, remote_id, link.remote_etag, put
        )
        self.record(resource_type, "replace", link.local_id, remote_id)
        return written

    async def _operations(
        self,
        resource_type: str,
        remote_id: str,
        desired: Dict[str, Any],
        current: Optional[RemoteResource],
    ) -> List[Dict[str, Any]]:
        """The PATCH operations taking the remote record to ``desired``.
        Its attributes are one path-less ``replace``; a group's membership
        is ``add`` and ``remove`` operations against what it lists now (a
        ``replace`` of ``members`` when it does not list them)."""
        values = {k: v for k, v in desired.items() if k not in ("schemas", "members")}
        operations: List[Dict[str, Any]] = [{"op": "replace", "value": values}]
        if resource_type != GROUP:
            return operations
        if current is None:
            current = await Client.get(self.instance, resource_type, remote_id)
        now = member_ids(current.body)
        if now is None:
            operations.append(
                {"op": "replace", "path": "members", "value": desired["members"]}
            )
            return operations
        wanted = member_ids(desired) or set()
        added = sorted(wanted - now)
        if added:
            operations.append(
                {"op": "add", "path": "members", "value": [{"value": m} for m in added]}
            )
        operations.extend(
            {"op": "remove", "path": f"members[value eq {scim_string(member)}]"}
            for member in sorted(now - wanted)
        )
        return operations

    async def _push(
        self, link: Any, desired: Dict[str, Any], index: Optional[RemoteIndex]
    ) -> str:
        resource_type = str(link.resource_type)
        name_attribute = NAME_ATTRIBUTES[resource_type]
        differs: Differs = user_differs if resource_type == USER else group_differs
        name = str(desired[name_attribute])
        if not link.remote_id:
            found = (
                index.match(str(link.local_id), name)
                if index is not None
                else await Client.find(
                    self.instance, resource_type, name_attribute, name
                )
            )
            if found is None:
                return await self._create(link, desired)
            link = self.linked(link, found)
            written = (
                await self._update(link, desired, found)
                if differs(desired, found.body)
                else None
            )
            self.synced(link, desired, written)
            return "linked"
        current = index.by_id.get(str(link.remote_id)) if index is not None else None
        if index is not None and current is None:
            return await self._recreate(link, desired)
        if (
            not differs(desired, current.body)
            if current is not None
            else link.pushed_hash == digest(desired) and link.status == "synced"
        ):
            self.synced(link, desired, None)
            return "unchanged"
        try:
            written = await self._update(link, desired, current)
        except InvalidInputExternalError as exc:
            if exc.upstream_status != 404:
                raise
            return await self._recreate(link, desired)
        self.synced(link, desired, written)
        return "updated"

    async def _recreate(self, link: Any, desired: Dict[str, Any]) -> str:
        """The remote record is gone: create it again."""
        link = self.links.update(link.id, remote_id=None, remote_etag=None)
        return await self._create(link, desired)

    async def _deprovision(
        self, link: Any, current: Optional[RemoteResource]
    ) -> Optional[str]:
        """Take a gone record off the target: None when it was never there
        (its link is dropped)."""
        if not link.remote_id:
            self.links.delete(link.id)
            return None
        resource_type, remote_id = str(link.resource_type), str(link.remote_id)
        outcome = "deactivated"
        try:
            if self.delete_mode == "delete":

                async def delete(if_match: Optional[str]) -> None:
                    await Client.delete(
                        self.instance, resource_type, remote_id, if_match
                    )

                await self._with_fresh_etag(
                    resource_type, remote_id, link.remote_etag, delete
                )
                self.record(resource_type, "delete", link.local_id, remote_id)
                outcome = "deleted"
            else:
                await self._deactivate(link, current)
        except InvalidInputExternalError as exc:
            if exc.upstream_status != 404:
                raise
            outcome = "deleted"
        gone = {"remote_id": None, "remote_etag": None} if outcome == "deleted" else {}
        self.links.update(
            link.id,
            pending=False,
            status=outcome,
            last_synced_at=datetime.now(UTC),
            last_error=None,
            pushed_hash=None,
            **gone,
        )
        return outcome

    async def _deactivate(self, link: Any, current: Optional[RemoteResource]) -> None:
        """A user becomes ``active: false``; a group loses its members."""
        resource_type, remote_id = str(link.resource_type), str(link.remote_id)
        write: Write
        if self.config.patch.supported:
            operation = (
                {"op": "replace", "path": "active", "value": False}
                if resource_type == USER
                else {"op": "replace", "path": "members", "value": []}
            )

            async def patch(if_match: Optional[str]) -> Any:
                return await Client.patch(
                    self.instance, resource_type, remote_id, [operation], if_match
                )

            write, etag = patch, link.remote_etag
        else:
            if current is None:
                current = await Client.get(self.instance, resource_type, remote_id)
            body: Dict[str, Any] = {
                k: v for k, v in current.body.items() if k not in ("id", "meta")
            }
            if resource_type == USER:
                body["active"] = False
            else:
                body["members"] = []

            async def put(if_match: Optional[str]) -> Any:
                return await Client.replace(
                    self.instance, resource_type, remote_id, body, if_match
                )

            write, etag = put, current.etag
        await self._with_fresh_etag(resource_type, remote_id, etag, write)
        self.record(resource_type, "deactivate", link.local_id, remote_id)

    # ----- one record ------------------------------------------------------

    async def sync_link(
        self, link: Any, record: Any, index: Optional[RemoteIndex]
    ) -> None:
        """Push one linked record as it now stands. A refused push is noted
        on the link (left pending, for the next run) and in the log; a
        refused token or an outlasted rate limit ends the run."""
        resource_type = str(link.resource_type)
        try:
            desired = self.desired(resource_type, record)
            if desired is not None:
                outcome: Optional[str] = await self._push(link, desired, index)
            else:
                current = (
                    index.by_id.get(str(link.remote_id))
                    if index is not None and link.remote_id
                    else None
                )
                if (
                    index is not None
                    and link.status in ("deactivated", "deleted")
                    and (
                        current is None or not still_provisioned(resource_type, current)
                    )
                ):
                    return
                outcome = await self._deprovision(link, current)
            if outcome:
                self.summary.add(resource_type, outcome)
        except BaseExternalError as exc:
            self.summary.add(resource_type, "failed")
            self.record(resource_type, "push", link.local_id, link.remote_id, exc)
            self.links.update(
                link.id,
                pending=True,
                status="error",
                last_error=str(exc)[:ERROR_DETAIL_CHARS],
            )
            logger.warning(
                "SCIM push of %s %s to %s failed: %s",
                resource_type,
                link.local_id,
                self.instance.id,
                exc,
            )
            if isinstance(exc, (AuthExternalError, RateLimitExternalError)):
                raise

    # ----- runs --------------------------------------------------------------

    def types(self) -> List[str]:
        return [USER, GROUP] if self.push_groups else [USER]

    async def drain(self) -> Summary:
        """Push every pending link, users before groups."""
        for resource_type in self.types():
            pending = live(
                self.links,
                provider_instance_id=self.instance.id,
                resource_type=resource_type,
                pending=True,
            )
            for link in pending:
                record = self.local(resource_type, str(link.local_id))
                await self.sync_link(link, record, None)
        return self.summary

    async def reconcile(self) -> Summary:
        """Bring the whole target in line: every local record in scope is
        linked and pushed where it differs, every linked record no longer
        in scope is taken off."""
        for resource_type in self.types():
            index = RemoteIndex.of(
                NAME_ATTRIBUTES[resource_type],
                await Client.search(
                    self.instance, resource_type, None, self.config.page_size
                ),
            )
            known = {
                str(link.local_id): link
                for link in live(
                    self.links,
                    provider_instance_id=self.instance.id,
                    resource_type=resource_type,
                )
            }
            for local_id, record in self.local_records(resource_type).items():
                link = known.pop(local_id, None) or self.link_for(
                    resource_type, local_id
                )
                await self.sync_link(link, record, index)
            for link in known.values():
                await self.sync_link(link, None, index)
        return self.summary


def mark_pending(
    registry: Any, resource_type: str, local_ids: Iterable[Optional[str]]
) -> List[str]:
    """Flag ``local_ids`` for a push to every enabled target, writing as
    each target's owner; the ids of the targets flagged."""
    from zephyrex.extensions.scim_provider.BLL_SCIMProvider import ScimLinkManager

    ids = sorted({str(i) for i in local_ids if i and str(i) not in system_ids()})
    if not ids:
        return []
    flagged: List[str] = []
    for instance in target_instances(registry):
        owner = instance_owner(registry, instance.id)
        links = ScimLinkManager(
            model_registry=registry, requester_id=owner.requester_id
        )
        for local_id in ids:
            found = live(
                links,
                provider_instance_id=instance.id,
                resource_type=resource_type,
                local_id=local_id,
            )
            if found:
                links.update(found[0].id, pending=True)
            else:
                links.create(
                    provider_instance_id=instance.id,
                    resource_type=resource_type,
                    local_id=local_id,
                    pending=True,
                    status="pending",
                )
        flagged.append(str(instance.id))
    return flagged
