# SPDX-License-Identifier: AGPL-3.0-or-later
"""A printer behind OctoPrint, through its REST API. The instance's API
key is an OctoPrint application key; ``base_url`` the OctoPrint server."""

from typing import Any, ClassVar, Dict, List, Mapping
from urllib.parse import quote

from zephyrex.extensions.ExternalErrors import InvalidInputExternalError
from zephyrex.extensions.fdm_sla_printing.EXT_FDMSLAPrinting import (
    PRINT_FILE_TYPES,
    AbstractPrinterProvider,
    job,
    reading,
)
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

_HEATERS = {"nozzle": ("/api/printer/tool", "tool0"), "bed": ("/api/printer/bed", None)}


def octoprint_state(flags: Mapping[str, Any]) -> str:
    if flags.get("error") or flags.get("closedOrError"):
        return "error"
    if flags.get("paused") or flags.get("pausing"):
        return "paused"
    if flags.get("printing") or flags.get("cancelling"):
        return "printing"
    if flags.get("ready") or flags.get("operational"):
        return "idle"
    return "offline"


def octoprint_report(
    printer: Mapping[str, Any], current: Mapping[str, Any]
) -> Dict[str, Any]:
    """OctoPrint's /api/printer and /api/job answers as a printer status."""
    temperature = printer.get("temperature", {})
    tool, bed = temperature.get("tool0", {}), temperature.get("bed", {})
    progress = current.get("progress") or {}
    return {
        "state": octoprint_state(printer.get("state", {}).get("flags", {})),
        "nozzle": reading(tool.get("actual"), tool.get("target")),
        "bed": reading(bed.get("actual"), bed.get("target")),
        "job": job(
            ((current.get("job") or {}).get("file") or {}).get("name"),
            progress.get("completion"),
            progress.get("printTime"),
            progress.get("printTimeLeft"),
        ),
    }


class PRV_OctoPrint_Printing(AbstractPrinterProvider):
    name: ClassVar[str] = "octoprint"
    friendly_name: ClassVar[str] = "OctoPrint"
    description: ClassVar[str] = "A printer behind OctoPrint"

    @classmethod
    async def status(cls, instance: ProviderInstanceModel) -> Dict[str, Any]:
        try:
            printer = await cls.call(instance, "GET", "/api/printer")
        except InvalidInputExternalError as exc:
            # 409: OctoPrint is up but not connected to the printer.
            if exc.upstream_status == 409:
                offline = {"state": "offline", "nozzle": None, "bed": None, "job": None}
                return {**offline, "provider": cls.name}
            raise
        current = await cls.call(instance, "GET", "/api/job")
        return {**octoprint_report(printer, current), "provider": cls.name}

    @classmethod
    async def files(cls, instance: ProviderInstanceModel) -> List[Dict[str, Any]]:
        found = await cls.call(
            instance, "GET", "/api/files/local", params={"recursive": "true"}
        )

        def walk(entries: List[Mapping[str, Any]]) -> List[Dict[str, Any]]:
            files: List[Dict[str, Any]] = []
            for entry in entries:
                if entry.get("type") == "folder":
                    files += walk(entry.get("children", []))
                elif str(entry.get("name", "")).lower().endswith(PRINT_FILE_TYPES):
                    files.append(
                        {
                            "name": entry.get("path", entry["name"]),
                            "size": entry.get("size"),
                        }
                    )
            return files

        return walk(found.get("files", []))

    @classmethod
    async def upload(
        cls, instance: ProviderInstanceModel, name: str, content: bytes, start: bool
    ) -> Dict[str, Any]:
        await cls.call(
            instance,
            "POST",
            "/api/files/local",
            files={"file": (name, content, "application/octet-stream")},
            data={
                "select": "true" if start else "false",
                "print": "true" if start else "false",
            },
        )
        return cls.done("upload", file=name, started=start)

    @classmethod
    async def start(cls, instance: ProviderInstanceModel, name: str) -> Dict[str, Any]:
        await cls.call(
            instance,
            "POST",
            f"/api/files/local/{quote(name)}",
            json={"command": "select", "print": True},
        )
        return cls.done("start", file=name)

    @classmethod
    async def _job(
        cls, instance: ProviderInstanceModel, action: str, body: Dict[str, Any]
    ) -> Dict[str, Any]:
        await cls.call(instance, "POST", "/api/job", json=body)
        return cls.done(action)

    @classmethod
    async def pause(cls, instance: ProviderInstanceModel) -> Dict[str, Any]:
        return await cls._job(
            instance, "pause", {"command": "pause", "action": "pause"}
        )

    @classmethod
    async def resume(cls, instance: ProviderInstanceModel) -> Dict[str, Any]:
        return await cls._job(
            instance, "resume", {"command": "pause", "action": "resume"}
        )

    @classmethod
    async def cancel(cls, instance: ProviderInstanceModel) -> Dict[str, Any]:
        return await cls._job(instance, "cancel", {"command": "cancel"})

    @classmethod
    async def set_temperature(
        cls, instance: ProviderInstanceModel, heater: str, celsius: float
    ) -> Dict[str, Any]:
        path, tool = _HEATERS[heater]
        body: Dict[str, Any] = (
            {"command": "target", "targets": {tool: celsius}}
            if tool
            else {"command": "target", "target": celsius}
        )
        await cls.call(instance, "POST", path, json=body)
        return cls.done("set_temperature", heater=heater, target=celsius)
