# SPDX-License-Identifier: AGPL-3.0-or-later
"""CAD with OpenSCAD: code that would read files is refused, parameter
values cannot smuggle expressions in, a missing OpenSCAD is reported,
and (where OpenSCAD is installed) models compile, take overrides and
report their errors."""

import base64
import struct

import pytest

from zephyrex.extensions.cad import EXT_CAD as cad_module
from zephyrex.extensions.cad.EXT_CAD import EXT_CAD
from zephyrex.extensions.cad.OpenSCAD import (
    Refused,
    checked_code,
    definition,
    definitions,
    problems,
)
from zephyrex.extensions.ExternalErrors import (
    InvalidInputExternalError,
    PermanentExternalError,
)
from zephyrex.lib.LimitedProcess import available

CUBE = "size = 10;\ncube([size, size, size]);\necho(volume = size * size * size);\n"
needs_openscad = pytest.mark.xfail(
    not available("openscad"),
    reason="OpenSCAD is not installed (the extension's apt dependency)",
)


def stl_triangles(body: bytes) -> int:
    """A binary STL's triangle count (80-byte header, then a uint32)."""
    return int(struct.unpack_from("<I", body, 80)[0])


class TestRefusals:
    @pytest.mark.parametrize(
        "code",
        [
            "include </etc/passwd>",
            "use <MCAD/gears.scad>",
            'import("/etc/shadow");',
            'surface(file = "/etc/hosts");',
            'linear_extrude(10) import ("secret.dxf");',
            'dxf_linear_extrude(file="x.dxf", height=1);',
            'import_stl("x.stl");',
            "   ",
        ],
    )
    def test_code_that_reads_files_is_refused(self, code):
        with pytest.raises(Refused):
            checked_code(code)

    def test_ordinary_code_passes(self):
        assert checked_code(CUBE) == CUBE

    @pytest.mark.parametrize(
        "name, value, literal",
        [
            ("size", 12, "size=12"),
            ("ratio", 0.5, "ratio=0.5"),
            ("hollow", True, "hollow=true"),
            ("label", "Front panel", 'label="Front panel"'),
        ],
    )
    def test_parameter_literals(self, name, value, literal):
        assert definition(name, value) == literal

    @pytest.mark.parametrize(
        "name, value",
        [
            ("size", 'x"; import("/etc/passwd"); y="'),
            ("size", "back\\slash"),
            ("size", float("nan")),
            ("size", float("inf")),
            ("size", [1, 2]),
            ("1size", 1),
            ("size;cube", 1),
        ],
    )
    def test_a_parameter_cannot_smuggle_an_expression(self, name, value):
        with pytest.raises(Refused):
            definition(name, value)

    def test_definitions_are_bounded(self):
        assert definitions({"a": 1, "b": 2}) == ["-D", "a=1", "-D", "b=2"]
        with pytest.raises(Refused):
            definitions({f"p{i}": i for i in range(51)})

    def test_problems(self):
        log = "Compiling…\nWARNING: Ignoring unknown variable 'x'\nECHO: 1\nERROR: Parser error"
        assert problems(log) == [
            "WARNING: Ignoring unknown variable 'x'",
            "ERROR: Parser error",
        ]

    async def test_a_refusal_comes_before_openscad(self):
        with pytest.raises(InvalidInputExternalError):
            await EXT_CAD.compile_model("include </etc/passwd>", "stl")
        with pytest.raises(InvalidInputExternalError):
            await EXT_CAD.compile_model(CUBE, "docx")

    async def test_a_missing_openscad_is_reported(self, monkeypatch):
        monkeypatch.setattr(cad_module, "OPENSCAD", "no-such-openscad-zx")
        with pytest.raises(PermanentExternalError):
            await EXT_CAD.compile_model(CUBE, "stl")


@needs_openscad
class TestCompile:
    async def test_a_cube_to_stl(self):
        model = await EXT_CAD.compile_model(CUBE, "stl")
        body = base64.b64decode(model["content_base64"])
        assert model["media_type"] == "model/stl"
        assert stl_triangles(body) == 12

    async def test_a_parameter_overrides_the_code(self):
        small = await EXT_CAD.compile_model(CUBE, "off", {"size": 2})
        assert b"2" in base64.b64decode(small["content_base64"])

    async def test_an_error_is_reported(self):
        with pytest.raises(InvalidInputExternalError, match="could not compile"):
            await EXT_CAD.compile_model("cube([10, 10, 10]", "stl")

    async def test_check_echoes(self):
        checked = await EXT_CAD.check_model(CUBE, {"size": 3})
        assert checked["ok"] and "27" in checked["echo"]
