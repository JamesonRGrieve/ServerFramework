# SPDX-License-Identifier: AGPL-3.0-or-later
"""3D printing: file names, heater limits and uploads checked before any
call; each API's answer (as its documentation shows it) read as a status;
a printer named by its instance's name or id, and no other; a printer on
the LAN refused until its host is allowed egress; and read-only live
checks with test printers."""

import base64

import pytest
from fastapi import HTTPException

from zephyrex.extensions.ExternalErrors import (
    InvalidInputExternalError,
    PermanentExternalError,
)
from zephyrex.extensions.fdm_sla_printing.EXT_FDMSLAPrinting import (
    MAX_UPLOAD_BYTES,
    EXT_FDMSLAPrinting,
    heater_target,
    print_file,
    upload_bytes,
)
from zephyrex.extensions.fdm_sla_printing.PRV_Moonraker import (
    PRV_Moonraker_Printing,
    moonraker_report,
)
from zephyrex.extensions.fdm_sla_printing.PRV_OctoPrint import (
    PRV_OctoPrint_Printing,
    octoprint_report,
)
from zephyrex.extensions.fdm_sla_printing.PRV_PrusaLink import (
    PRV_PrusaLink_Printing,
    prusalink_report,
)


class TestChecks:
    @pytest.mark.parametrize("name", ["benchy.gcode", "Part 2_v3.bgcode", "cube.sl1s"])
    def test_print_files(self, name):
        assert print_file(name) == name

    @pytest.mark.parametrize(
        "name", ["../etc/passwd.gcode", "a/b.gcode", "model.stl", ".hidden.gcode", ""]
    )
    def test_a_file_name_that_is_not_a_plain_print_file(self, name):
        with pytest.raises(InvalidInputExternalError):
            print_file(name)

    def test_heater_limits(self):
        assert heater_target("nozzle", 215) == 215.0
        assert heater_target("bed", 0) == 0.0
        for heater, celsius in (("nozzle", 400), ("bed", 150), ("chamber", 40)):
            with pytest.raises(InvalidInputExternalError):
                heater_target(heater, celsius)

    def test_uploads(self):
        assert upload_bytes(base64.b64encode(b"G28").decode()) == b"G28"
        with pytest.raises(InvalidInputExternalError):
            upload_bytes("not base64!")
        with pytest.raises(InvalidInputExternalError):
            upload_bytes("")
        assert MAX_UPLOAD_BYTES == 200 * 1024 * 1024


class TestReports:
    def test_octoprint(self):
        report = octoprint_report(
            {
                "state": {"flags": {"printing": True, "operational": True}},
                "temperature": {
                    "tool0": {"actual": 214.8, "target": 215.0},
                    "bed": {"actual": 60.1, "target": 60.0},
                },
            },
            {
                "job": {"file": {"name": "benchy.gcode"}},
                "progress": {
                    "completion": 42.123,
                    "printTime": 900,
                    "printTimeLeft": 1200,
                },
            },
        )
        assert report == {
            "state": "printing",
            "nozzle": {"actual": 214.8, "target": 215.0},
            "bed": {"actual": 60.1, "target": 60.0},
            "job": {
                "file": "benchy.gcode",
                "progress": 42.1,
                "elapsed_s": 900,
                "remaining_s": 1200,
            },
        }

    def test_octoprint_idle_has_no_job(self):
        report = octoprint_report(
            {"state": {"flags": {"ready": True, "operational": True}}},
            {"job": {"file": {"name": None}}, "progress": {"completion": None}},
        )
        assert report["state"] == "idle" and report["job"] is None

    def test_moonraker(self):
        report = moonraker_report(
            {
                "webhooks": {"state": "ready"},
                "print_stats": {
                    "state": "printing",
                    "filename": "voron_cube.gcode",
                    "print_duration": 600.0,
                },
                "extruder": {"temperature": 240.2, "target": 240.0},
                "heater_bed": {"temperature": 99.8, "target": 100.0},
                "virtual_sdcard": {"progress": 0.25},
            }
        )
        assert report["state"] == "printing"
        assert report["job"] == {
            "file": "voron_cube.gcode",
            "progress": 25.0,
            "elapsed_s": 600,
            "remaining_s": 1800,
        }

    def test_moonraker_klipper_shutdown_is_an_error(self):
        report = moonraker_report(
            {"webhooks": {"state": "shutdown"}, "print_stats": {"state": "printing"}}
        )
        assert report["state"] == "error"

    def test_prusalink(self):
        report = prusalink_report(
            {
                "printer": {
                    "state": "PAUSED",
                    "temp_nozzle": 210.0,
                    "target_nozzle": 215.0,
                    "temp_bed": 59.5,
                    "target_bed": 60.0,
                },
                "job": {
                    "id": 7,
                    "progress": 63,
                    "time_printing": 3000,
                    "time_remaining": 1700,
                },
            },
            {
                "id": 7,
                "file": {"name": "BENCHY~1.BGC", "display_name": "benchy.bgcode"},
            },
        )
        assert report["state"] == "paused"
        assert report["job"]["file"] == "benchy.bgcode"
        assert report["job"]["remaining_s"] == 1700

    def test_prusalink_attention_is_an_error(self):
        assert prusalink_report({"printer": {"state": "ATTENTION"}}, None)["state"] == (
            "error"
        )


class TestPrinters:
    @pytest.fixture
    def printers(self, provider_instance, rotation_over, monkeypatch):
        octo = provider_instance(PRV_OctoPrint_Printing)
        prusa = provider_instance(
            PRV_PrusaLink_Printing, settings={"base_url": "http://192.168.1.77"}
        )
        monkeypatch.setattr(
            EXT_FDMSLAPrinting, "_root_rotation_cache", rotation_over(octo, prusa)
        )
        return octo, prusa

    async def test_list_printers(self, printers):
        octo, prusa = printers
        listed = {
            p["name"]: p["provider"] for p in await EXT_FDMSLAPrinting.list_printers()
        }
        assert listed[octo.name] == "octoprint" and listed[prusa.name] == "prusalink"

    async def test_a_printer_is_reached_by_name_and_no_other(self, printers):
        """The OctoPrint instance has no address; it must not fall over to
        the PrusaLink printer behind it in the rotation."""
        octo, _ = printers
        with pytest.raises(HTTPException) as raised:
            await EXT_FDMSLAPrinting.printer_status(octo.name)
        self.assert_only_tried(raised.value, octo)

    async def test_by_id_and_an_unknown_printer(self, printers):
        octo, _ = printers
        with pytest.raises(HTTPException) as raised:
            await EXT_FDMSLAPrinting.printer_status(str(octo.id))
        self.assert_only_tried(raised.value, octo)
        with pytest.raises(HTTPException) as raised:
            await EXT_FDMSLAPrinting.printer_status("no-such-printer")
        assert raised.value.status_code == 404

    @staticmethod
    def assert_only_tried(error: HTTPException, instance) -> None:
        """The rotation gave up on the named printer alone, and said why."""
        # Starlette types detail as str; the rotation raises a dict.
        detail: object = error.detail
        assert isinstance(detail, dict)
        attempted = detail["attempted_providers"]
        assert {a["provider_instance_id"] for a in attempted} == {str(instance.id)}
        assert "address not configured" in str(attempted)

    async def test_a_lan_printer_needs_an_egress_allowance(self, printers):
        _, prusa = printers
        with pytest.raises(InvalidInputExternalError, match="SSRF"):
            await EXT_FDMSLAPrinting.printer_status(prusa.name)

    async def test_prusalink_cannot_set_a_heater(self, printers):
        _, prusa = printers
        with pytest.raises(PermanentExternalError):
            await EXT_FDMSLAPrinting.set_temperature(prusa.name, "bed", 60)

    async def test_arguments_are_checked_before_the_printer(self, printers):
        octo, _ = printers
        with pytest.raises(InvalidInputExternalError):
            await EXT_FDMSLAPrinting.start_print(octo.name, "../../x.gcode")
        with pytest.raises(InvalidInputExternalError):
            await EXT_FDMSLAPrinting.set_temperature(octo.name, "nozzle", 999)


class TestLive:
    """Read-only checks against test printers (status and file list)."""

    @pytest.mark.external_api(provider="octoprint_test")
    async def test_octoprint(self, provider_instance, sandbox_credentials_for):
        creds = sandbox_credentials_for("octoprint_test")
        instance = provider_instance(
            PRV_OctoPrint_Printing,
            api_key=creds["OCTOPRINT_API_KEY"],
            settings={"base_url": creds["OCTOPRINT_URL"]},
        )
        assert (await PRV_OctoPrint_Printing.status(instance))["state"]

    @pytest.mark.external_api(provider="moonraker_test")
    async def test_moonraker(self, provider_instance, sandbox_credentials_for):
        creds = sandbox_credentials_for("moonraker_test")
        instance = provider_instance(
            PRV_Moonraker_Printing, settings={"base_url": creds["MOONRAKER_URL"]}
        )
        assert (await PRV_Moonraker_Printing.status(instance))["state"]

    @pytest.mark.external_api(provider="prusalink_test")
    async def test_prusalink(self, provider_instance, sandbox_credentials_for):
        creds = sandbox_credentials_for("prusalink_test")
        instance = provider_instance(
            PRV_PrusaLink_Printing,
            api_key=creds["PRUSALINK_PASSWORD"],
            settings={"base_url": creds["PRUSALINK_URL"]},
        )
        assert (await PRV_PrusaLink_Printing.status(instance))["state"]
