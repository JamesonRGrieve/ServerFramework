# SPDX-License-Identifier: AGPL-3.0-or-later
"""3D printers (FDM and resin) on the local network: status, files,
uploads and print control through OctoPrint, Moonraker (Klipper:
Creality K-series, Voron and others) or PrusaLink (Prusa FDM and SL1S).

Each provider instance is one printer: its ``base_url`` and API key.
Printers live on private networks, which the server's SSRF guard refuses
by default; a printer's host must be listed in ``EGRESS_ALLOWED_HOSTS``.
Every ability names its printer (the instance's name or id) and acts on
that printer only.

A status is ``{state, nozzle, bed, job}``: ``state`` one of ``idle``,
``printing``, ``paused``, ``finished``, ``error``, ``busy`` or
``offline``; each heater ``{actual, target}`` in °C; ``job`` (or None)
``{file, progress (0-100), elapsed_s, remaining_s}``.
"""

import base64
import binascii
import re
from abc import abstractmethod
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Set, Tuple

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractProviderInstance,
    AbstractStaticExtension,
    AbstractStaticProvider,
    InstanceSetting,
    ability,
)
from zephyrex.extensions.ExternalErrors import (
    InvalidInputExternalError,
    TransientExternalError,
)
from zephyrex.lib.Dependencies import Dependencies
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

PRINTER_REQUEST_TIMEOUT_SECONDS = 30.0
MAX_UPLOAD_BYTES = 200 * 1024 * 1024
PRINT_FILE_TYPES = (".gcode", ".gco", ".bgcode", ".sl1", ".sl1s")
HEATER_LIMITS: Mapping[str, Tuple[float, float]] = {
    "nozzle": (0.0, 300.0),
    "bed": (0.0, 120.0),
}
STATES = ("idle", "printing", "paused", "finished", "error", "busy", "offline")
_FILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,127}$")


def print_file(name: str) -> str:
    """A print file's name: plain, one path segment, a print format."""
    if not _FILE_NAME.match(name) or ".." in name:
        raise InvalidInputExternalError(f"{name!r} is not a plain file name")
    if not name.lower().endswith(PRINT_FILE_TYPES):
        raise InvalidInputExternalError(
            f"{name!r} is not a print file ({', '.join(PRINT_FILE_TYPES)})"
        )
    return name


def heater_target(heater: str, celsius: float) -> float:
    if heater not in HEATER_LIMITS:
        raise InvalidInputExternalError(
            f"heater must be one of {', '.join(HEATER_LIMITS)}, not {heater!r}"
        )
    low, high = HEATER_LIMITS[heater]
    if not low <= celsius <= high:
        raise InvalidInputExternalError(f"a {heater} target is {low:g}-{high:g} °C")
    return float(celsius)


def upload_bytes(content_base64: str) -> bytes:
    try:
        content = base64.b64decode(content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise InvalidInputExternalError("content_base64 is not base64") from exc
    if not content:
        raise InvalidInputExternalError("the file is empty")
    if len(content) > MAX_UPLOAD_BYTES:
        raise InvalidInputExternalError(
            f"a print file is at most {MAX_UPLOAD_BYTES // (1024 * 1024)} MiB"
        )
    return content


def reading(actual: Any, target: Any) -> Dict[str, Optional[float]]:
    """A heater's actual and target temperatures."""
    return {
        "actual": float(actual) if actual is not None else None,
        "target": float(target) if target is not None else None,
    }


def job(
    file: Any, progress: Any, elapsed: Any, remaining: Any
) -> Optional[Dict[str, Any]]:
    if not file:
        return None
    return {
        "file": str(file),
        "progress": round(float(progress), 1) if progress is not None else None,
        "elapsed_s": int(elapsed) if elapsed is not None else None,
        "remaining_s": int(remaining) if remaining is not None else None,
    }


class AbstractPrinterProvider(AbstractStaticProvider):
    """A printer's control API; each instance is one printer."""

    name: ClassVar[str] = ""
    friendly_name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    _abilities: ClassVar[Set[str]] = {
        "printer_status",
        "list_print_files",
        "upload_print_file",
        "start_print",
        "pause_print",
        "resume_print",
        "cancel_print",
        "set_temperature",
    }
    _env: ClassVar[Dict[str, Any]] = {}
    http_timeout_seconds: ClassVar[float] = PRINTER_REQUEST_TIMEOUT_SECONDS
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            "base_url", "The printer's address on the LAN (http://192.168.1.50)"
        ),
        InstanceSetting("api_key", "API key", secret=True, field="api_key"),
    )

    @classmethod
    def bond_instance(cls, instance: ProviderInstanceModel) -> AbstractProviderInstance:
        return AbstractProviderInstance(instance)

    @classmethod
    def base_url(cls, instance: ProviderInstanceModel) -> str:
        url = str(cls.setting(instance, "base_url") or "").rstrip("/")
        if not url:
            raise TransientExternalError(
                f"{cls.friendly_name} printer address not configured",
                provider=cls.name,
            )
        return url

    @classmethod
    def api_headers(cls, instance: ProviderInstanceModel) -> Dict[str, str]:
        key = cls.setting(instance, "api_key")
        return {"X-Api-Key": str(key)} if key else {}

    @classmethod
    async def call(
        cls, instance: ProviderInstanceModel, method: str, path: str, **kwargs: Any
    ) -> Any:
        headers = {**cls.api_headers(instance), **kwargs.pop("headers", {})}
        return await cls.http().request(
            method, f"{cls.base_url(instance)}{path}", headers=headers, **kwargs
        )

    @classmethod
    @abstractmethod
    async def status(cls, instance: ProviderInstanceModel) -> Dict[str, Any]: ...

    @classmethod
    @abstractmethod
    async def files(cls, instance: ProviderInstanceModel) -> List[Dict[str, Any]]:
        """Print files on the printer: ``{name, size}``."""

    @classmethod
    @abstractmethod
    async def upload(
        cls, instance: ProviderInstanceModel, name: str, content: bytes, start: bool
    ) -> Dict[str, Any]: ...

    @classmethod
    @abstractmethod
    async def start(
        cls, instance: ProviderInstanceModel, name: str
    ) -> Dict[str, Any]: ...

    @classmethod
    @abstractmethod
    async def pause(cls, instance: ProviderInstanceModel) -> Dict[str, Any]: ...

    @classmethod
    @abstractmethod
    async def resume(cls, instance: ProviderInstanceModel) -> Dict[str, Any]: ...

    @classmethod
    @abstractmethod
    async def cancel(cls, instance: ProviderInstanceModel) -> Dict[str, Any]: ...

    @classmethod
    @abstractmethod
    async def set_temperature(
        cls, instance: ProviderInstanceModel, heater: str, celsius: float
    ) -> Dict[str, Any]: ...

    @classmethod
    def done(cls, action: str, **details: Any) -> Dict[str, Any]:
        return {"action": action, "provider": cls.name, **details}

    @classmethod
    def services(cls) -> List[str]:
        return ["3d_printing"]


class EXT_FDMSLAPrinting(AbstractStaticExtension):
    name: ClassVar[str] = "fdm_sla_printing"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "3D printer status and control through OctoPrint, Moonraker or PrusaLink"
    )

    _env: ClassVar[Dict[str, Any]] = {}
    dependencies: ClassVar[Dependencies] = Dependencies([])
    _abilities: ClassVar[Set[str]] = {
        "list_printers",
        *AbstractPrinterProvider._abilities,
    }

    @classmethod
    @ability("list_printers")
    async def list_printers(cls) -> List[Dict[str, Any]]:
        """The configured printers: id, name, and which API each speaks."""
        return cls.instances_of()

    @classmethod
    @ability("printer_status")
    async def printer_status(cls, printer: str) -> Dict[str, Any]:
        result: Dict[str, Any] = await cls.rotate_on_instance(printer, "status")
        return result

    @classmethod
    @ability("list_print_files")
    async def list_print_files(cls, printer: str) -> List[Dict[str, Any]]:
        result: List[Dict[str, Any]] = await cls.rotate_on_instance(printer, "files")
        return result

    @classmethod
    @ability("upload_print_file")
    async def upload_print_file(
        cls, printer: str, name: str, content_base64: str, start: bool = False
    ) -> Dict[str, Any]:
        """Send a sliced file to the printer, printing it when ``start``."""
        result: Dict[str, Any] = await cls.rotate_on_instance(
            printer, "upload", print_file(name), upload_bytes(content_base64), start
        )
        return result

    @classmethod
    @ability("start_print")
    async def start_print(cls, printer: str, name: str) -> Dict[str, Any]:
        result: Dict[str, Any] = await cls.rotate_on_instance(
            printer, "start", print_file(name)
        )
        return result

    @classmethod
    @ability("pause_print")
    async def pause_print(cls, printer: str) -> Dict[str, Any]:
        result: Dict[str, Any] = await cls.rotate_on_instance(printer, "pause")
        return result

    @classmethod
    @ability("resume_print")
    async def resume_print(cls, printer: str) -> Dict[str, Any]:
        result: Dict[str, Any] = await cls.rotate_on_instance(printer, "resume")
        return result

    @classmethod
    @ability("cancel_print")
    async def cancel_print(cls, printer: str) -> Dict[str, Any]:
        result: Dict[str, Any] = await cls.rotate_on_instance(printer, "cancel")
        return result

    @classmethod
    @ability("set_temperature")
    async def set_temperature(
        cls, printer: str, heater: str, celsius: float
    ) -> Dict[str, Any]:
        """Set the nozzle (0-300 °C) or bed (0-120 °C) target; 0 turns it off."""
        result: Dict[str, Any] = await cls.rotate_on_instance(
            printer, "set_temperature", heater, heater_target(heater, celsius)
        )
        return result
