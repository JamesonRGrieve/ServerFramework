# SPDX-License-Identifier: AGPL-3.0-or-later
"""Connected vehicles: read a vehicle's state, lock and unlock it, run its
climate and charging, and send it a destination.

Every ability names the vehicle it acts on. A provider instance is an
account's credentials, so failing over moves to another credential for the
same vehicle, never to another vehicle: unlocking the wrong car is not a
failover.
"""

from abc import abstractmethod
from typing import Any, ClassVar, Dict, List, Set

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractProviderInstance,
    AbstractStaticExtension,
    AbstractStaticProvider,
    ability,
)
from zephyrex.extensions.ExternalErrors import InvalidInputExternalError
from zephyrex.lib.Dependencies import Dependencies
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

# Cabin temperatures a vehicle accepts, in °C.
MIN_CLIMATE_C = 15.0
MAX_CLIMATE_C = 28.0


def climate_temperature(celsius: float) -> float:
    if not MIN_CLIMATE_C <= celsius <= MAX_CLIMATE_C:
        raise InvalidInputExternalError(
            f"Cabin temperature must be {MIN_CLIMATE_C}-{MAX_CLIMATE_C} °C"
        )
    return celsius


class AbstractAutomotiveProvider(AbstractStaticProvider):
    """A vehicle platform. Every ability takes the rotated instance (the
    account) and the vehicle it acts on."""

    name: ClassVar[str] = ""
    friendly_name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    _abilities: ClassVar[Set[str]] = {
        "get_vehicle_info",
        "lock_doors",
        "unlock_doors",
        "set_climate",
        "start_charging",
        "stop_charging",
        "navigate_to",
    }
    _env: ClassVar[Dict[str, Any]] = {}

    @classmethod
    def bond_instance(cls, instance: ProviderInstanceModel) -> AbstractProviderInstance:
        return AbstractProviderInstance(instance)

    @classmethod
    @abstractmethod
    async def get_vehicle_info(
        cls, instance: ProviderInstanceModel, vehicle_id: str
    ) -> Dict[str, Any]:
        """The vehicle's name, state, battery and range."""

    @classmethod
    @abstractmethod
    async def command(
        cls,
        instance: ProviderInstanceModel,
        vehicle_id: str,
        command: str,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Run one of the extension's vehicle commands (``door_lock``,
        ``door_unlock``, ``climate_on`` / ``climate_off`` with
        ``temperature``, ``charge_start``, ``charge_stop``, ``navigate``
        with ``address``)."""

    @classmethod
    def services(cls) -> List[str]:
        return ["automotive"]


class EXT_Automotive(AbstractStaticExtension):
    name: ClassVar[str] = "automotive"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Connected vehicles: state, locks, climate, charging and navigation"
    )

    _env: ClassVar[Dict[str, Any]] = {}
    dependencies: ClassVar[Dependencies] = Dependencies([])
    _abilities: ClassVar[Set[str]] = AbstractAutomotiveProvider._abilities

    @classmethod
    def get_required_permissions(cls) -> List[str]:
        return ["automotive:read", "automotive:control"]

    @classmethod
    async def _command(
        cls, vehicle_id: str, command: str, **payload: Any
    ) -> Dict[str, Any]:
        result: Dict[str, Any] = await cls.rotate_provider(
            "command", vehicle_id, command, payload
        )
        return result

    @classmethod
    @ability("get_vehicle_info")
    async def get_vehicle_info(cls, vehicle_id: str) -> Dict[str, Any]:
        info: Dict[str, Any] = await cls.rotate_provider("get_vehicle_info", vehicle_id)
        return info

    @classmethod
    @ability("lock_doors")
    async def lock_doors(cls, vehicle_id: str) -> Dict[str, Any]:
        return await cls._command(vehicle_id, "door_lock")

    @classmethod
    @ability("unlock_doors")
    async def unlock_doors(cls, vehicle_id: str) -> Dict[str, Any]:
        return await cls._command(vehicle_id, "door_unlock")

    @classmethod
    @ability("set_climate")
    async def set_climate(
        cls, vehicle_id: str, temperature: float, enabled: bool = True
    ) -> Dict[str, Any]:
        """Run the climate at ``temperature`` °C, or turn it off."""
        if not enabled:
            return await cls._command(vehicle_id, "climate_off")
        return await cls._command(
            vehicle_id, "climate_on", temperature=climate_temperature(temperature)
        )

    @classmethod
    @ability("start_charging")
    async def start_charging(cls, vehicle_id: str) -> Dict[str, Any]:
        return await cls._command(vehicle_id, "charge_start")

    @classmethod
    @ability("stop_charging")
    async def stop_charging(cls, vehicle_id: str) -> Dict[str, Any]:
        return await cls._command(vehicle_id, "charge_stop")

    @classmethod
    @ability("navigate_to")
    async def navigate_to(cls, vehicle_id: str, address: str) -> Dict[str, Any]:
        if not address.strip():
            raise InvalidInputExternalError("A destination needs an address")
        return await cls._command(vehicle_id, "navigate", address=address.strip())
