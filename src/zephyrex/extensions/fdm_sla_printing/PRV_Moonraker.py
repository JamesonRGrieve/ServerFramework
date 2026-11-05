# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Klipper printer through Moonraker's API: Creality K-series, Voron,
Sovol, Elegoo and other Klipper machines. ``base_url`` is the Moonraker
server; the API key is optional (Moonraker may trust its LAN)."""

from typing import Any, ClassVar, Dict, List, Mapping

from zephyrex.extensions.fdm_sla_printing.EXT_FDMSLAPrinting import (
    PRINT_FILE_TYPES,
    AbstractPrinterProvider,
    job,
    reading,
)
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

QUERY = "print_stats&extruder&heater_bed&virtual_sdcard&webhooks"
_STATES = {
    "standby": "idle",
    "printing": "printing",
    "paused": "paused",
    "complete": "finished",
    "cancelled": "idle",
    "error": "error",
}
_HEATERS = {"nozzle": "extruder", "bed": "heater_bed"}


def moonraker_state(status: Mapping[str, Any]) -> str:
    """Klipper's own state first (shutdown, error, startup), then the job's."""
    klipper = status.get("webhooks", {}).get("state")
    if klipper in ("shutdown", "error"):
        return "error"
    if klipper == "startup":
        return "busy"
    return _STATES.get(str(status.get("print_stats", {}).get("state")), "idle")


def moonraker_report(status: Mapping[str, Any]) -> Dict[str, Any]:
    """A Moonraker object query's ``result.status`` as a printer status.
    The time left is estimated from the progress so far."""
    stats = status.get("print_stats", {})
    extruder, bed = status.get("extruder", {}), status.get("heater_bed", {})
    progress = status.get("virtual_sdcard", {}).get("progress")
    state = moonraker_state(status)
    elapsed = stats.get("print_duration")
    remaining = None
    if progress and elapsed and state in ("printing", "paused"):
        remaining = elapsed / progress - elapsed
    return {
        "state": state,
        "nozzle": reading(extruder.get("temperature"), extruder.get("target")),
        "bed": reading(bed.get("temperature"), bed.get("target")),
        "job": job(
            stats.get("filename") if state in ("printing", "paused") else None,
            progress * 100 if progress is not None else None,
            elapsed,
            remaining,
        ),
    }


class PRV_Moonraker_Printing(AbstractPrinterProvider):
    name: ClassVar[str] = "moonraker"
    friendly_name: ClassVar[str] = "Moonraker (Klipper)"
    description: ClassVar[str] = "A Klipper printer through Moonraker"

    @classmethod
    async def status(cls, instance: ProviderInstanceModel) -> Dict[str, Any]:
        found = await cls.call(instance, "GET", f"/printer/objects/query?{QUERY}")
        report = moonraker_report(found.get("result", {}).get("status", {}))
        return {**report, "provider": cls.name}

    @classmethod
    async def files(cls, instance: ProviderInstanceModel) -> List[Dict[str, Any]]:
        found = await cls.call(
            instance, "GET", "/server/files/list", params={"root": "gcodes"}
        )
        return [
            {"name": entry["path"], "size": entry.get("size")}
            for entry in found.get("result", [])
            if str(entry.get("path", "")).lower().endswith(PRINT_FILE_TYPES)
        ]

    @classmethod
    async def upload(
        cls, instance: ProviderInstanceModel, name: str, content: bytes, start: bool
    ) -> Dict[str, Any]:
        await cls.call(
            instance,
            "POST",
            "/server/files/upload",
            files={"file": (name, content, "application/octet-stream")},
            data={"root": "gcodes", "print": "true" if start else "false"},
        )
        return cls.done("upload", file=name, started=start)

    @classmethod
    async def start(cls, instance: ProviderInstanceModel, name: str) -> Dict[str, Any]:
        await cls.call(
            instance, "POST", "/printer/print/start", params={"filename": name}
        )
        return cls.done("start", file=name)

    @classmethod
    async def pause(cls, instance: ProviderInstanceModel) -> Dict[str, Any]:
        await cls.call(instance, "POST", "/printer/print/pause")
        return cls.done("pause")

    @classmethod
    async def resume(cls, instance: ProviderInstanceModel) -> Dict[str, Any]:
        await cls.call(instance, "POST", "/printer/print/resume")
        return cls.done("resume")

    @classmethod
    async def cancel(cls, instance: ProviderInstanceModel) -> Dict[str, Any]:
        await cls.call(instance, "POST", "/printer/print/cancel")
        return cls.done("cancel")

    @classmethod
    async def set_temperature(
        cls, instance: ProviderInstanceModel, heater: str, celsius: float
    ) -> Dict[str, Any]:
        # heater and celsius are checked by the extension: no free text
        # reaches the G-code line.
        script = f"SET_HEATER_TEMPERATURE HEATER={_HEATERS[heater]} TARGET={celsius:g}"
        await cls.call(
            instance, "POST", "/printer/gcode/script", params={"script": script}
        )
        return cls.done("set_temperature", heater=heater, target=celsius)
