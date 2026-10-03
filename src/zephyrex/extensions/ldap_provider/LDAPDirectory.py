# SPDX-License-Identifier: AGPL-3.0-or-later
"""The directory: framework users and teams as a read-only LDAP tree, and
the binds that open it.

The tree under the configured base DN::

    <base>
      ou=people   uid=<username, else email>   inetOrgPerson, one per user
      ou=groups   cn=<team id>                 groupOfNames, one per team
      ou=services cn=<name>                    service accounts (bind only)

A person's ``memberOf`` and a group's ``member`` follow enabled, unexpired
team memberships. Deleted and inactive users are not in the tree, nor are
the framework's own ROOT, SYSTEM and TEMPLATE users.

Who sees what: a service account sees the whole tree; a user sees
themselves, the teams they belong to and the people in those teams; an
unauthenticated connection sees only the root DSE.

A user's simple bind goes through the framework's own checks: the same
brute-force budget per source address as the login endpoint, the
lockout extension's per-user threshold when it is loaded, the account's
active flag, and ``UserManager.verify_password`` for the password. A
user with a second factor cannot bind, because a simple bind cannot
carry it.

Every method here does blocking database work and runs on a worker
thread; the protocol server calls it through ``asyncio.to_thread``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import IntEnum
from string import hexdigits
from typing import Any, Dict, FrozenSet, Iterator, List, Optional, Sequence, Set, Tuple

from fastapi import HTTPException
from ldap3.core.exceptions import LDAPInvalidDnError
from ldap3.utils.dn import escape_rdn, parse_dn

from zephyrex.extensions.ldap_provider.BLL_LDAPProvider import (
    LdapServiceAccountModel,
    secret_matches,
)
from zephyrex.extensions.ldap_provider.LDAPFilter import (
    ALL_OPERATIONAL_ATTRIBUTES,
    DirectoryEntry,
    Node,
    evaluate,
)
from zephyrex.lib.DateTimeUtils import ensure_utc
from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Auth import (
    TeamModel,
    UserManager,
    UserModel,
    UserTeamModel,
    _lockout_hooks,
    mfa_login_methods,
)

PAGE_SIZE = 200
LOGIN_FLOW = "password_login"
PEOPLE = "people"
GROUPS = "groups"
SERVICES = "services"

STARTTLS_OID = "1.3.6.1.4.1.1466.20037"
WHOAMI_OID = "1.3.6.1.4.1.4203.1.11.3"

PERSON_CLASSES = ("top", "person", "organizationalPerson", "inetOrgPerson")
GROUP_CLASSES = ("top", "groupOfNames")
CONTAINER_CLASSES = ("top", "organizationalUnit")

DEFAULT_RELEASED_ATTRIBUTES = (
    "uid",
    "cn",
    "sn",
    "givenName",
    "displayName",
    "mail",
    "memberOf",
    "member",
    "description",
    "entryUUID",
)
# Released whatever the configuration: they name entries (they are in
# every DN returned) or say what an entry is.
STRUCTURAL_ATTRIBUTES = ("objectClass", "ou", "uid", "cn")


class ResultCode(IntEnum):
    """RFC 4511 4.1.9 result codes this directory answers with."""

    SUCCESS = 0
    OPERATIONS_ERROR = 1
    PROTOCOL_ERROR = 2
    TIME_LIMIT_EXCEEDED = 3
    SIZE_LIMIT_EXCEEDED = 4
    COMPARE_FALSE = 5
    COMPARE_TRUE = 6
    AUTH_METHOD_NOT_SUPPORTED = 7
    ADMIN_LIMIT_EXCEEDED = 11
    UNAVAILABLE_CRITICAL_EXTENSION = 12
    CONFIDENTIALITY_REQUIRED = 13
    NO_SUCH_ATTRIBUTE = 16
    NO_SUCH_OBJECT = 32
    INVALID_DN_SYNTAX = 34
    INVALID_CREDENTIALS = 49
    INSUFFICIENT_ACCESS_RIGHTS = 50
    UNAVAILABLE = 52
    UNWILLING_TO_PERFORM = 53


class Refused(Exception):
    """An operation answered with ``code`` and ``message``."""

    def __init__(self, code: ResultCode, message: str, matched_dn: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.matched_dn = matched_dn


RDN = Tuple[str, str]
DN = Tuple[RDN, ...]


def _unescape(value: str) -> str:
    """An RFC 4514 attribute value with its escapes resolved."""
    out = bytearray()
    index = 0
    while index < len(value):
        char = value[index]
        if char == "\\" and index + 1 < len(value):
            pair = value[index + 1 : index + 3]
            if len(pair) == 2 and all(c in hexdigits for c in pair):
                out.append(int(pair, 16))
                index += 3
                continue
            out.extend(value[index + 1].encode())
            index += 2
            continue
        out.extend(char.encode())
        index += 1
    return out.decode("utf-8")


def parse(dn: str) -> DN:
    """``dn`` as (attribute, value) pairs, attributes lower-cased and values
    unescaped. Raises :class:`Refused` (invalidDNSyntax) when it is not a
    DN; multi-valued RDNs are not part of this tree and are refused too."""
    if not dn.strip():
        return ()
    try:
        parts = parse_dn(dn, escape=False, strip=True)
        if any(separator == "+" for _, _, separator in parts):
            raise ValueError("multi-valued RDN")
        return tuple((kind.lower(), _unescape(value)) for kind, value, _ in parts)
    except (LDAPInvalidDnError, ValueError, UnicodeDecodeError) as exc:
        raise Refused(ResultCode.INVALID_DN_SYNTAX, "invalid DN") from exc


def _key(dn: DN) -> Tuple[RDN, ...]:
    return tuple((kind, " ".join(value.split()).casefold()) for kind, value in dn)


def render(dn: DN) -> str:
    return ",".join(f"{kind}={escape_rdn(value)}" for kind, value in dn)


@dataclass(frozen=True)
class DirectoryConfig:
    """What the directory serves: under ``base_dn``, the attributes in
    ``released`` (lower-case names), at most ``size_limit`` entries and
    ``time_limit_seconds`` per search."""

    base_dn: str
    released: FrozenSet[str]
    size_limit: int
    time_limit_seconds: float

    @staticmethod
    def released_from(names: Sequence[str]) -> FrozenSet[str]:
        return frozenset(
            name.strip().lower()
            for name in (*names, *STRUCTURAL_ATTRIBUTES)
            if name.strip()
        )

    @property
    def base(self) -> DN:
        return parse(self.base_dn)


@dataclass(frozen=True)
class Principal:
    """Who a connection is bound as: a user (``user_id``) or a service
    account (``user_id`` None)."""

    dn: str
    user_id: Optional[str] = None


@dataclass
class SearchOutcome:
    entries: List[Tuple[str, List[Tuple[str, Tuple[str, ...]]]]]
    code: ResultCode = ResultCode.SUCCESS
    message: str = ""
    matched_dn: str = ""


@dataclass(frozen=True)
class SearchRequest:
    base: str
    scope: int
    filter: Node
    size_limit: int
    time_limit: int
    attributes: Tuple[str, ...]
    types_only: bool


BASE_OBJECT, SINGLE_LEVEL, WHOLE_SUBTREE = 0, 1, 2


class LDAPDirectory:
    """The directory served from ``model_registry``'s users and teams."""

    def __init__(self, model_registry: Any, config: DirectoryConfig) -> None:
        self.registry = model_registry
        self.config = config
        self.base = config.base
        self.base_key = _key(self.base)
        self.root_id = str(env("ROOT_ID"))
        self.hidden_ids = {
            str(env("ROOT_ID")),
            str(env("SYSTEM_ID")),
            str(env("TEMPLATE_ID")),
        }

    # -- names ---------------------------------------------------------------

    def container(self, name: str) -> DN:
        return (("ou", name),) + self.base

    def person_dn(self, user: Any) -> str:
        return render((("uid", self._uid(user)),) + self.container(PEOPLE))

    def group_dn(self, team_id: str) -> str:
        return render((("cn", team_id),) + self.container(GROUPS))

    def _relative(self, dn: DN) -> Optional[DN]:
        """The RDNs of ``dn`` above the base, or None outside it."""
        depth = len(self.base)
        if len(dn) < depth or _key(dn[len(dn) - depth :]) != self.base_key:
            return None
        return dn[: len(dn) - depth]

    def _leaf(self, dn: DN) -> Tuple[Optional[str], Optional[RDN]]:
        """For ``ou=<container>,<base>`` or ``<leaf>,ou=<container>,<base>``:
        the container's name (lower case) and the leaf RDN, if any.
        (None, None) for any other name."""
        relative = self._relative(dn)
        if relative is None or not 1 <= len(relative) <= 2:
            return None, None
        kind, name = relative[-1]
        if kind != "ou" or name.casefold() not in (PEOPLE, GROUPS, SERVICES):
            return None, None
        return name.casefold(), relative[0] if len(relative) == 2 else None

    # -- reading the framework -------------------------------------------------

    def _db(self, model: Any) -> Any:
        return model.DB(self.registry.DB.manager.Base)

    def _rows(self, model: Any, filters: List[Any], **kwargs: Any) -> List[Any]:
        rows: List[Any] = self._db(model).list(
            requester_id=self.root_id,
            model_registry=self.registry,
            return_type="dto",
            override_dto=model,
            filters=list(filters),
            **kwargs,
        )
        return rows

    def _user_filters(self) -> List[Any]:
        User = self._db(UserModel)
        return [
            User.deleted_at.is_(None),
            User.id.notin_(sorted(self.hidden_ids)),
            (User.active.is_(None)) | (User.active.is_(True)),
        ]

    @staticmethod
    def _uid(user: Any) -> str:
        return str(user.username or user.email or user.id)

    def _memberships(
        self,
        *,
        user_ids: Optional[Set[str]] = None,
        team_ids: Optional[Set[str]] = None,
    ) -> List[Any]:
        """Enabled, unexpired memberships of ``user_ids`` or in ``team_ids``."""
        Membership = self._db(UserTeamModel)
        filters: List[Any] = [
            Membership.deleted_at.is_(None),
            Membership.enabled.is_(True),
        ]
        if user_ids is not None:
            filters.append(Membership.user_id.in_(sorted(user_ids)))
        if team_ids is not None:
            filters.append(Membership.team_id.in_(sorted(team_ids)))
        now = datetime.now(timezone.utc)
        return [
            row
            for row in self._rows(UserTeamModel, filters)
            if row.expires_at is None or ensure_utc(row.expires_at) > now
        ]

    def _teams(self, team_ids: Optional[Set[str]]) -> List[Any]:
        Team = self._db(TeamModel)
        filters: List[Any] = [Team.deleted_at.is_(None)]
        if team_ids is not None:
            filters.append(Team.id.in_(sorted(team_ids)))
        return self._rows(TeamModel, filters, order_by=[Team.id])

    def _users(self, user_ids: Set[str]) -> List[Any]:
        if not user_ids:
            return []
        User = self._db(UserModel)
        return self._rows(
            UserModel, [*self._user_filters(), User.id.in_(sorted(user_ids))]
        )

    def _scope(
        self, principal: Principal
    ) -> Tuple[Optional[Set[str]], Optional[Set[str]]]:
        """The (user ids, team ids) ``principal`` may see; None is all."""
        if principal.user_id is None:
            return None, None
        teams = {
            str(row.team_id) for row in self._memberships(user_ids={principal.user_id})
        }
        users = {principal.user_id}
        if teams:
            users |= {str(row.user_id) for row in self._memberships(team_ids=teams)}
        return users, teams

    # -- entries ---------------------------------------------------------------

    def root_dse(self) -> DirectoryEntry:
        return DirectoryEntry(
            "",
            {
                "objectClass": ("top",),
                "namingContexts": (render(self.base),),
                "supportedLDAPVersion": ("3",),
                "supportedExtension": (STARTTLS_OID, WHOAMI_OID),
                "vendorName": ("zephyrex",),
            },
        )

    def _base_entry(self) -> DirectoryEntry:
        kind, value = self.base[0]
        return DirectoryEntry(
            render(self.base),
            {"objectClass": ("top", "extensibleObject"), kind: (value,)},
        )

    def _container_entry(self, name: str) -> DirectoryEntry:
        return DirectoryEntry(
            render(self.container(name)),
            {"objectClass": CONTAINER_CLASSES, "ou": (name,)},
        )

    def _person(self, user: Any, team_ids: Sequence[str]) -> DirectoryEntry:
        uid = self._uid(user)
        names = " ".join(n for n in (user.first_name, user.last_name) if n)
        attributes: Dict[str, Tuple[str, ...]] = {
            "objectClass": PERSON_CLASSES,
            "uid": (uid,),
            "cn": (user.display_name or names or uid,),
            "sn": (user.last_name or uid,),
            "entryUUID": (str(user.id),),
            "memberOf": tuple(self.group_dn(team) for team in sorted(team_ids)),
        }
        for name, value in (
            ("givenName", user.first_name),
            ("displayName", user.display_name),
            ("mail", user.email),
        ):
            if value:
                attributes[name] = (str(value),)
        return DirectoryEntry(self.person_dn(user), attributes)

    def _group(self, team: Any, members: Sequence[Any]) -> DirectoryEntry:
        names = (str(team.id),) + ((str(team.name),) if team.name else ())
        attributes: Dict[str, Tuple[str, ...]] = {
            "objectClass": GROUP_CLASSES,
            "cn": names,
            "entryUUID": (str(team.id),),
            "member": tuple(sorted(self.person_dn(user) for user in members)),
        }
        if team.description:
            attributes["description"] = (str(team.description),)
        return DirectoryEntry(self.group_dn(str(team.id)), attributes)

    def _people_entries(
        self, users: List[Any], teams: Optional[Set[str]]
    ) -> List[DirectoryEntry]:
        joined: Dict[str, List[str]] = {str(user.id): [] for user in users}
        for row in self._memberships(user_ids=set(joined), team_ids=teams):
            joined[str(row.user_id)].append(str(row.team_id))
        return [self._person(user, joined[str(user.id)]) for user in users]

    def _group_entries(
        self, found: List[Any], users: Optional[Set[str]]
    ) -> List[DirectoryEntry]:
        members: Dict[str, Set[str]] = {str(team.id): set() for team in found}
        for row in self._memberships(team_ids=set(members), user_ids=users):
            members[str(row.team_id)].add(str(row.user_id))
        people = {
            str(user.id): user
            for user in self._users(
                set().union(*members.values()) if members else set()
            )
        }
        return [
            self._group(
                team,
                [people[m] for m in members[str(team.id)] if m in people],
            )
            for team in found
        ]

    def _people(self, principal: Principal) -> Iterator[DirectoryEntry]:
        """Every person ``principal`` may see, a page at a time."""
        users, teams = self._scope(principal)
        User = self._db(UserModel)
        filters = self._user_filters()
        if users is not None:
            filters.append(User.id.in_(sorted(users)))
        offset = 0
        while True:
            page = self._rows(
                UserModel, filters, order_by=[User.id], limit=PAGE_SIZE, offset=offset
            )
            yield from self._people_entries(page, teams)
            if len(page) < PAGE_SIZE:
                return
            offset += PAGE_SIZE

    def _groups(self, principal: Principal) -> Iterator[DirectoryEntry]:
        users, teams = self._scope(principal)
        if teams is not None and not teams:
            return
        found = self._teams(teams)
        for start in range(0, len(found), PAGE_SIZE):
            yield from self._group_entries(found[start : start + PAGE_SIZE], users)

    def _find_person(self, principal: Principal, uid: str) -> Optional[DirectoryEntry]:
        user = self._user_named(uid)
        if user is None:
            return None
        users, teams = self._scope(principal)
        if users is not None and str(user.id) not in users:
            return None
        return self._people_entries([user], teams)[0]

    def _find_group(
        self, principal: Principal, team_id: str
    ) -> Optional[DirectoryEntry]:
        users, teams = self._scope(principal)
        if teams is not None and team_id not in teams:
            return None
        found = self._teams({team_id})
        return self._group_entries(found, users)[0] if found else None

    def _user_named(self, uid: str) -> Optional[Any]:
        """The one user whose username or email is ``uid``, as login finds
        them."""
        identifier = UserManager._normalize_identifier(uid)
        User = self._db(UserModel)
        found = self._rows(
            UserModel,
            [
                *self._user_filters(),
                (User.username == identifier) | (User.email == identifier),
            ],
        )
        return found[0] if len(found) == 1 else None

    # -- binds -----------------------------------------------------------------

    def bind(self, dn: str, password: str, address: str) -> Principal:
        """The principal ``dn`` binds as with ``password``, from ``address``.
        Raises :class:`Refused` for anything else."""
        if not password:
            raise Refused(
                ResultCode.UNWILLING_TO_PERFORM,
                "unauthenticated and anonymous binds are refused",
            )
        tracker = UserManager._lockout_tracker
        if tracker.is_locked(address, LOGIN_FLOW):
            raise Refused(
                ResultCode.UNWILLING_TO_PERFORM, "too many failed attempts; try later"
            )
        container, leaf = self._leaf(parse(dn))
        principal: Optional[Principal] = None
        if container == PEOPLE and leaf is not None and leaf[0] == "uid":
            principal = self._bind_user(leaf[1], password, address)
        elif container == SERVICES and leaf is not None and leaf[0] == "cn":
            principal = self._bind_service(leaf[1], password)
        else:
            # Not a name that can bind: the same bcrypt work as a wrong
            # password, so the answer's timing says nothing about the name.
            secret_matches(password, None)
        if principal is None:
            tracker.record_failure(address, LOGIN_FLOW)
            raise Refused(ResultCode.INVALID_CREDENTIALS, "invalid credentials")
        tracker.clear(address, LOGIN_FLOW)
        return principal

    def _bind_service(self, name: str, secret: str) -> Optional[Principal]:
        Account = self._db(LdapServiceAccountModel)
        found = Account.list(
            requester_id=self.root_id,
            model_registry=self.registry,
            filters=[
                Account.name == name,
                Account.deleted_at.is_(None),
                Account.enabled.is_(True),
            ],
        )
        stored = found[0]["secret_hash"] if len(found) == 1 else None
        if not secret_matches(secret, stored):
            return None
        return Principal(render((("cn", name),) + self.container(SERVICES)))

    def _bind_user(self, uid: str, password: str, address: str) -> Optional[Principal]:
        user = self._user_named(uid)
        if user is None:
            secret_matches(password, None)
            return None
        user_id = str(user.id)
        record_failure = _lockout_hooks["record_failure"]
        within_threshold = _lockout_hooks["assert_within_threshold"]
        if within_threshold is not None:
            try:
                within_threshold(user_id, self.registry)
            except HTTPException:
                secret_matches(password, None)
                return None
        verified = UserManager(
            model_registry=self.registry, requester_id=user_id
        ).verify_password(user_id, password)
        if not verified:
            if record_failure is not None:
                record_failure(user_id, address, self.registry)
            return None
        if mfa_login_methods(user_id, self.registry):
            raise Refused(
                ResultCode.UNWILLING_TO_PERFORM,
                "this account has a second factor, which a simple bind cannot carry",
            )
        return Principal(self.person_dn(user), user_id)

    # -- reads -----------------------------------------------------------------

    def _locate(self, principal: Principal, dn: DN) -> Tuple[str, DirectoryEntry]:
        """What ``dn`` names: (kind, entry). Raises noSuchObject when it is
        nothing ``principal`` can see."""
        relative = self._relative(dn)
        if relative is None:
            raise Refused(ResultCode.NO_SUCH_OBJECT, "no such object")
        if not relative:
            return "base", self._base_entry()
        name, leaf = self._leaf(dn)
        if name not in (PEOPLE, GROUPS):
            raise Refused(
                ResultCode.NO_SUCH_OBJECT, "no such object", render(self.base)
            )
        if leaf is None:
            return name, self._container_entry(name)
        matched = render(self.container(name))
        entry: Optional[DirectoryEntry] = None
        if name == PEOPLE and leaf[0] == "uid":
            entry = self._find_person(principal, leaf[1])
        elif name == GROUPS and leaf[0] == "cn":
            entry = self._find_group(principal, leaf[1])
        if entry is None:
            raise Refused(ResultCode.NO_SUCH_OBJECT, "no such object", matched)
        return "leaf", entry

    def _candidates(
        self, principal: Principal, kind: str, entry: DirectoryEntry, scope: int
    ) -> Iterator[DirectoryEntry]:
        if scope != SINGLE_LEVEL:
            yield entry
        if scope == BASE_OBJECT or kind == "leaf":
            return
        if kind == "base":
            yield self._container_entry(PEOPLE)
            yield self._container_entry(GROUPS)
            if scope == SINGLE_LEVEL:
                return
        if kind in ("base", PEOPLE):
            yield from self._people(principal)
        if kind in ("base", GROUPS):
            yield from self._groups(principal)

    def search(
        self, principal: Optional[Principal], request: SearchRequest
    ) -> SearchOutcome:
        dn = parse(request.base)
        if not dn and request.scope == BASE_OBJECT:
            return self._root_dse_search(request)
        if principal is None:
            raise Refused(
                ResultCode.INSUFFICIENT_ACCESS_RIGHTS, "bind before searching"
            )
        kind, entry = self._locate(principal, dn)
        size_limit = self.config.size_limit
        if request.size_limit:
            size_limit = min(size_limit, request.size_limit)
        time_limit = self.config.time_limit_seconds
        if request.time_limit:
            time_limit = min(time_limit, float(request.time_limit))
        deadline = time.monotonic() + time_limit
        released = self.config.released
        outcome = SearchOutcome(entries=[])
        for candidate in self._candidates(principal, kind, entry, request.scope):
            if time.monotonic() > deadline:
                outcome.code = ResultCode.TIME_LIMIT_EXCEEDED
                outcome.message = f"the search ran past {time_limit:g} seconds"
                return outcome
            if evaluate(request.filter, candidate, released) is not True:
                continue
            if len(outcome.entries) >= size_limit:
                outcome.code = ResultCode.SIZE_LIMIT_EXCEEDED
                outcome.message = f"more than {size_limit} entries match"
                return outcome
            outcome.entries.append(
                (
                    candidate.dn,
                    candidate.released(
                        released, request.attributes, request.types_only
                    ),
                )
            )
        return outcome

    def _root_dse_search(self, request: SearchRequest) -> SearchOutcome:
        entry = self.root_dse()
        everything = frozenset(name.lower() for name in entry.attributes)
        if evaluate(request.filter, entry, everything) is not True:
            return SearchOutcome(entries=[])
        selection = request.attributes
        if ALL_OPERATIONAL_ATTRIBUTES in selection:
            selection = ()
        return SearchOutcome(
            entries=[("", entry.released(everything, selection, request.types_only))]
        )

    def compare(
        self, principal: Optional[Principal], dn: str, attribute: str, value: str
    ) -> ResultCode:
        if principal is None:
            raise Refused(
                ResultCode.INSUFFICIENT_ACCESS_RIGHTS, "bind before comparing"
            )
        _, entry = self._locate(principal, parse(dn))
        values = entry.values(attribute)
        if attribute.lower() not in self.config.released or not values:
            raise Refused(ResultCode.NO_SUCH_ATTRIBUTE, "no such attribute")
        asserted = " ".join(value.split()).casefold()
        if any(" ".join(v.split()).casefold() == asserted for v in values):
            return ResultCode.COMPARE_TRUE
        return ResultCode.COMPARE_FALSE
