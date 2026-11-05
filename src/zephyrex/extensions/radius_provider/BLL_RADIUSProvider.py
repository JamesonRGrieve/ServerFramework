# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the RADIUS authenticator answers, and for whom.

* :class:`RadiusClientModel` is a NAS (an access point controller, a switch,
  a VPN concentrator) allowed to ask: a source address or network and the
  shared secret it signs with. The secret is write-only and encrypted at rest.
  A client may require its users to belong to a team, so a guest signup can
  never reach a staff network.
* :class:`RadiusTeamPolicyModel` is what an accepted member of a team is
  given: a VLAN (RFC 3580 tunnel attributes), a Filter-Id and a
  Session-Timeout. A user in several teams with policies gets the one with the
  lowest ``priority``.

Both are server configuration: only the root account reads or writes them.
"""

import ipaddress
from typing import Any, ClassVar, Iterable, List, Optional, Type, TypeVar

from fastapi import HTTPException
from pydantic import Field

from zephyrex.database.StaticPermissions import is_root_id
from zephyrex.lib.Environment import env
from zephyrex.lib.SecretEncryption import encrypt_secret
from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    ModelMeta,
    StringSearchModel,
    UpdateMixinModel,
)
from zephyrex.logic.BLL_Auth import TeamModel
from zephyrex.pydantic2.fastapi import AuthType, RouterMixin
from zephyrex.pydantic2.registry import BaseModel

# RFC 2865 §3 asks for shared secrets of at least 16 octets; shorter ones are
# open to offline guessing from one captured exchange.
MIN_SHARED_SECRET_LENGTH = 16
MAX_SHARED_SECRET_LENGTH = 128
MIN_VLAN_ID = 1
MAX_VLAN_ID = 4094


class RadiusClientModel(ApplicationModel, UpdateMixinModel, metaclass=ModelMeta):
    """A NAS allowed to send Access-Requests."""

    name: str = Field(..., description="What the NAS is")
    address: str = Field(
        ..., description="Source address or network (CIDR) the NAS sends from"
    )
    shared_secret: Optional[str] = Field(
        None, exclude=True, description="Shared secret (write-only, encrypted)"
    )
    required_team_id: Optional[str] = Field(
        None, description="Only members of this team are accepted through it"
    )
    is_enabled: bool = Field(True, description="Whether its requests are answered")

    table_comment: ClassVar[str] = "NAS clients the RADIUS authenticator answers"

    class Create(BaseModel):
        name: str
        address: str
        shared_secret: str
        required_team_id: Optional[str] = None
        is_enabled: bool = True

    class Update(BaseModel):
        name: Optional[str] = None
        address: Optional[str] = None
        shared_secret: Optional[str] = None
        required_team_id: Optional[str] = None
        is_enabled: Optional[bool] = None

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        name: Optional[StringSearchModel] = None
        address: Optional[StringSearchModel] = None
        is_enabled: Optional[bool] = None


class RadiusTeamPolicyModel(
    ApplicationModel, UpdateMixinModel, TeamModel.Reference, metaclass=ModelMeta
):
    """The reply attributes a team's members are accepted with."""

    vlan_id: Optional[int] = Field(
        None, ge=MIN_VLAN_ID, le=MAX_VLAN_ID, description="VLAN to place them on"
    )
    filter_id: Optional[str] = Field(None, description="Filter-Id the NAS applies")
    session_timeout_seconds: Optional[int] = Field(
        None, ge=1, description="Session-Timeout, in seconds"
    )
    priority: int = Field(
        100, description="Lowest wins when a user's teams have several"
    )

    table_comment: ClassVar[str] = "RADIUS reply attributes per team"

    class Create(BaseModel, TeamModel.Reference.ID):
        vlan_id: Optional[int] = Field(None, ge=MIN_VLAN_ID, le=MAX_VLAN_ID)
        filter_id: Optional[str] = None
        session_timeout_seconds: Optional[int] = Field(None, ge=1)
        priority: int = 100

    class Update(BaseModel):
        vlan_id: Optional[int] = Field(None, ge=MIN_VLAN_ID, le=MAX_VLAN_ID)
        filter_id: Optional[str] = None
        session_timeout_seconds: Optional[int] = Field(None, ge=1)
        priority: Optional[int] = None

    class Search(
        ApplicationModel.Search, UpdateMixinModel.Search, TeamModel.Reference.ID.Search
    ):
        priority: Optional[int] = None


def normalized_address(address: str) -> str:
    """An address as the network it names: ``10.0.0.5`` is ``10.0.0.5/32``.
    A network with host bits set is ambiguous and refused."""
    try:
        return str(ipaddress.ip_network(address.strip(), strict=True))
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="address is an IP address, or a network in CIDR form "
            "with no host bits set",
        )


def sealed_secret(secret: str) -> str:
    """A shared secret checked for strength and encrypted for storage."""
    if not MIN_SHARED_SECRET_LENGTH <= len(secret) <= MAX_SHARED_SECRET_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"shared_secret is {MIN_SHARED_SECRET_LENGTH}-"
            f"{MAX_SHARED_SECRET_LENGTH} characters",
        )
    sealed = encrypt_secret(secret)
    if sealed is None:
        raise HTTPException(status_code=400, detail="shared_secret is required")
    return sealed


class RootManagedManager(AbstractBLLManager):
    """A manager of server configuration: anyone but root is refused before
    any read or write."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        requester = self.optional_requester
        if requester is not None and not is_root_id(requester.id):
            raise HTTPException(
                status_code=403, detail="RADIUS configuration is root-managed"
            )

    def _live(self) -> Any:
        """The filter for rows not deleted. Root reads see deleted rows
        unless they say otherwise, and a deleted client or policy must stop
        applying."""
        return self.DB.deleted_at.is_(None)

    def _require_team(self, team_id: str) -> None:
        if not TeamModel.DB(self.model_registry.DB.manager.Base).exists(
            requester_id=self.requester.id,
            model_registry=self.model_registry,
            id=team_id,
        ):
            raise HTTPException(status_code=404, detail="Team not found")


class RadiusClientManager(RootManagedManager, RouterMixin):
    _model = RadiusClientModel

    prefix: ClassVar[Optional[str]] = "/v1/radius/client"
    tags: ClassVar[Optional[List[str]]] = ["RADIUS Provider"]
    auth_type: ClassVar[AuthType] = AuthType.JWT

    def _require_unique_address(
        self, address: str, other_than: Optional[str] = None
    ) -> None:
        for client in self.list(address=address, filters=[self._live()]):
            if client.id != other_than:
                raise HTTPException(
                    status_code=409, detail="A client already has that address"
                )

    def create_validation(self, entity: Any) -> None:
        entity.address = normalized_address(entity.address)
        self._require_unique_address(entity.address)
        entity.shared_secret = sealed_secret(entity.shared_secret)
        if entity.required_team_id:
            self._require_team(entity.required_team_id)

    def update(self, id: str, **kwargs: Any) -> RadiusClientModel:
        if kwargs.get("address") is not None:
            kwargs["address"] = normalized_address(kwargs["address"])
            self._require_unique_address(kwargs["address"], other_than=id)
        if kwargs.get("shared_secret") is not None:
            kwargs["shared_secret"] = sealed_secret(kwargs["shared_secret"])
        if kwargs.get("required_team_id"):
            self._require_team(kwargs["required_team_id"])
        updated: RadiusClientModel = super().update(id, **kwargs)
        return updated

    def enabled(self) -> List[RadiusClientModel]:
        clients: List[RadiusClientModel] = self.list(
            is_enabled=True, filters=[self._live()]
        )
        return clients


class RadiusTeamPolicyManager(RootManagedManager, RouterMixin):
    _model = RadiusTeamPolicyModel

    prefix: ClassVar[Optional[str]] = "/v1/radius/team-policy"
    tags: ClassVar[Optional[List[str]]] = ["RADIUS Provider"]
    auth_type: ClassVar[AuthType] = AuthType.JWT

    def create_validation(self, entity: Any) -> None:
        self._require_team(entity.team_id)

    def for_teams(self, team_ids: Iterable[str]) -> Optional[RadiusTeamPolicyModel]:
        """The policy that applies to a member of ``team_ids``: the lowest
        priority, then the lowest id, so the choice is stable."""
        wanted = set(team_ids)
        if not wanted:
            return None
        policies: List[RadiusTeamPolicyModel] = self.list(
            filters=[self.DB.team_id.in_(sorted(wanted)), self._live()]
        )
        return min(policies, key=lambda p: (p.priority, p.id), default=None)


RootManaged = TypeVar("RootManaged", bound=RootManagedManager)


def root_manager(manager_class: Type[RootManaged], model_registry: Any) -> RootManaged:
    """``manager_class`` acting as root: how the authenticator reads its own
    configuration."""
    return manager_class(model_registry=model_registry, requester_id=env("ROOT_ID"))
