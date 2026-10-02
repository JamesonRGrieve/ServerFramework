# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Prusa printer through PrusaLink's API v1 (MK4, MK3.9, XL, MINI, Core
One, and the SL1S resin printer). PrusaLink authenticates with HTTP
Digest: the ``username`` (``maker`` by default) and the instance's API
key as the password, both shown on the printer. ``storage`` names where
print files live (``usb``, or ``local`` on printers without USB).

PrusaLink has no API to set a heater's target; that is refused.
"""

from typing import Any, ClassVar, Dict, List, Mapping, Tuple
from urllib.parse import quote

import httpx

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.ExternalErrors import (
    InvalidInputExternalError,
    PermanentExternalError,
)
from zephyrex.extensions.fdm_sla_printing.EXT_FDMSLAPrinting import (
    PRINT_FILE_TYPES,
    AbstractPrinterProvider,
    job,
    reading,
)
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

DEFAULT_USERNAME = "maker"
DEFAULT_STORAGE = "usb"
_STATES = {
    "IDLE": "idle",
    "READY": "idle",
    "BUSY": "busy",
    "PRINTING": "printing",
    "PAUSED": "paused",
    "FINISHED": "finished",
    "STOPPED": "idle",
    "ERROR": "error",
    "ATTENTION": "error",
}


def prusalink_state(state: Any) -> str:
    return _STATES.get(str(state), "offline")


def prusalink_report(found: Mapping[str, Any], detail: Any) -> Dict[str, Any]:
    """PrusaLink's /api/v1/status (and, during a job, /api/v1/job) as a
    printer status."""
    printer: Mapping[str, Any] = found.get("printer", {})
    current: Mapping[str, Any] = found.get("job") or {}
    file = detail.get("file", {}) if isinstance(detail, dict) else {}
    return {
        "state": prusalink_state(printer.get("state")),
        "nozzle": reading(printer.get("temp_nozzle"), printer.get("target_nozzle")),
        "bed": reading(printer.get("temp_bed"), printer.get("target_bed")),
        "job": job(
            file.get("display_name") or file.get("name"),
            current.get("progress"),
            current.get("time_printing"),
            current.get("time_remaining"),
        ),
    }


class PRV_PrusaLink_Printing(AbstractPrinterProvider):
    name: ClassVar[str] = "prusalink"
    friendly_name: ClassVar[str] = "PrusaLink"
    description: ClassVar[str] = "A Prusa printer through PrusaLink"
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting("base_url", "The printer's address on the LAN"),
        InstanceSetting("username", "PrusaLink username", default=DEFAULT_USERNAME),
        InstanceSetting(
            "api_key",
            "PrusaLink password (on the printer)",
            secret=True,
            field="api_key",
        ),
        InstanceSetting(
            "storage", "Where print files live (usb or local)", default=DEFAULT_STORAGE
        ),
    )

    @classmethod
    def api_headers(cls, instance: ProviderInstanceModel) -> Dict[str, str]:
        return {"Accept": "application/json"}

    @classmethod
    async def call(
        cls, instance: ProviderInstanceModel, method: str, path: str, **kwargs: Any
    ) -> Any:
        password = cls.setting(instance, "api_key") or ""
        username = cls.setting(instance, "username") or DEFAULT_USERNAME
        return await super().call(
            instance,
            method,
            path,
            auth=httpx.DigestAuth(str(username), str(password)),
            **kwargs,
        )

    @classmethod
    def _storage(cls, instance: ProviderInstanceModel) -> str:
        storage = str(cls.setting(instance, "storage") or DEFAULT_STORAGE)
        if storage not in ("usb", "local"):
            raise InvalidInputExternalError(
                f"PrusaLink storage is usb or local, not {storage!r}", provider=cls.name
            )
        return storage

    @classmethod
    async def _job_id(cls, instance: ProviderInstanceModel) -> int:
        found = await cls.call(instance, "GET", "/api/v1/status")
        current = found.get("job") or {}
        if "id" not in current:
            raise InvalidInputExternalError("No print is running", provider=cls.name)
        return int(current["id"])

    @classmethod
    async def status(cls, instance: ProviderInstanceModel) -> Dict[str, Any]:
        found = await cls.call(instance, "GET", "/api/v1/status")
        detail: Any = None
        if found.get("job"):
            detail = await cls.call(instance, "GET", "/api/v1/job")
        return {**prusalink_report(found, detail), "provider": cls.name}

    @classmethod
    async def files(cls, instance: ProviderInstanceModel) -> List[Dict[str, Any]]:
        found = await cls.call(
            instance, "GET", f"/api/v1/files/{cls._storage(instance)}"
        )
        return [
            {"name": entry.get("name", ""), "size": entry.get("size")}
            for entry in found.get("children", [])
            if str(entry.get("name", "")).lower().endswith(PRINT_FILE_TYPES)
        ]

    @classmethod
    async def upload(
        cls, instance: ProviderInstanceModel, name: str, content: bytes, start: bool
    ) -> Dict[str, Any]:
        await cls.call(
            instance,
            "PUT",
            f"/api/v1/files/{cls._storage(instance)}/{quote(name)}",
            content=content,
            headers={
                "Content-Type": "application/octet-stream",
                "Print-After-Upload": "?1" if start else "?0",
                "Overwrite": "?1",
            },
        )
        return cls.done("upload", file=name, started=start)

    @classmethod
    async def start(cls, instance: ProviderInstanceModel, name: str) -> Dict[str, Any]:
        await cls.call(
            instance, "POST", f"/api/v1/files/{cls._storage(instance)}/{quote(name)}"
        )
        return cls.done("start", file=name)

    @classmethod
    async def pause(cls, instance: ProviderInstanceModel) -> Dict[str, Any]:
        job_id = await cls._job_id(instance)
        await cls.call(instance, "PUT", f"/api/v1/job/{job_id}/pause")
        return cls.done("pause", job_id=job_id)

    @classmethod
    async def resume(cls, instance: ProviderInstanceModel) -> Dict[str, Any]:
        job_id = await cls._job_id(instance)
        await cls.call(instance, "PUT", f"/api/v1/job/{job_id}/resume")
        return cls.done("resume", job_id=job_id)

    @classmethod
    async def cancel(cls, instance: ProviderInstanceModel) -> Dict[str, Any]:
        job_id = await cls._job_id(instance)
        await cls.call(instance, "DELETE", f"/api/v1/job/{job_id}")
        return cls.done("cancel", job_id=job_id)

    @classmethod
    async def set_temperature(
        cls, instance: ProviderInstanceModel, heater: str, celsius: float
    ) -> Dict[str, Any]:
        raise PermanentExternalError(
            "PrusaLink has no API to set a heater's target; set it on the printer",
            provider=cls.name,
        )
