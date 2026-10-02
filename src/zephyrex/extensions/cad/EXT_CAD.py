# SPDX-License-Identifier: AGPL-3.0-or-later
"""Parametric CAD with OpenSCAD: compile a model's code (with parameter
overrides) to STL, 3MF, OFF, AMF, DXF or SVG, or check it for errors.

The code is untrusted (an agent or a user wrote it); see ``OpenSCAD`` for
what is refused and the limits it runs under.
"""

import base64
from typing import Any, ClassVar, Dict, Mapping, Optional, Set

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
    ability,
)
from zephyrex.extensions.cad.OpenSCAD import (
    FORMATS,
    Refused,
    Value,
    checked_code,
    compile_model,
    definitions,
    problems,
)
from zephyrex.extensions.ExternalErrors import (
    InvalidInputExternalError,
    PermanentExternalError,
    TransientExternalError,
)
from zephyrex.lib.Dependencies import Dependencies, SYS_Dependency
from zephyrex.lib.LimitedProcess import LimitExceeded, available

OPENSCAD = "openscad"
MAX_PROBLEMS = 20


class EXT_CAD(AbstractStaticExtension):
    name: ClassVar[str] = "cad"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Parametric CAD: OpenSCAD code compiled to STL, 3MF and more"
    )

    _env: ClassVar[Dict[str, Any]] = {}
    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            SYS_Dependency.for_apt(
                "openscad",
                "openscad",
                friendly_name="OpenSCAD",
                reason="Compiling models",
            ),
        ]
    )
    _abilities: ClassVar[Set[str]] = {"compile_model", "check_model"}

    @classmethod
    async def _compile(
        cls,
        code: str,
        export_format: str,
        output_name: str,
        parameters: Optional[Mapping[str, Value]],
    ) -> Any:
        try:
            checked_code(code)
            definitions(parameters or {})
        except Refused as exc:
            raise InvalidInputExternalError(str(exc)) from exc
        if not available(OPENSCAD):
            raise PermanentExternalError(
                "OpenSCAD is not installed (the cad extension's system dependency)"
            )
        try:
            return await compile_model(
                OPENSCAD, code, export_format, output_name, parameters or {}
            )
        except Refused as exc:
            raise InvalidInputExternalError(str(exc)) from exc
        except LimitExceeded as exc:
            raise TransientExternalError(
                "The model took too long to compile", cause=exc
            ) from exc

    @classmethod
    @ability("compile_model")
    async def compile_model(
        cls,
        code: str,
        format: str = "stl",
        parameters: Optional[Dict[str, Value]] = None,
    ) -> Dict[str, Any]:
        """Compile OpenSCAD ``code`` (``parameters`` override its
        top-level variables) to a mesh or drawing, returned in base64."""
        if format not in FORMATS:
            raise InvalidInputExternalError(
                f"format must be one of {', '.join(FORMATS)}, not {format!r}"
            )
        export_format, media_type = FORMATS[format]
        compiled = await cls._compile(
            code, export_format, f"model.{format}", parameters
        )
        found = problems(compiled.log)
        if not compiled.ok or not compiled.body:
            raise InvalidInputExternalError(
                "OpenSCAD could not compile the model: "
                + ("; ".join(found[:MAX_PROBLEMS]) or "no output")
            )
        return {
            "format": format,
            "media_type": media_type,
            "filename": f"model.{format}",
            "size": len(compiled.body),
            "content_base64": base64.b64encode(compiled.body).decode("ascii"),
            "warnings": found[:MAX_PROBLEMS],
        }

    @classmethod
    @ability("check_model")
    async def check_model(
        cls, code: str, parameters: Optional[Dict[str, Value]] = None
    ) -> Dict[str, Any]:
        """Evaluate the code without building geometry: its warnings and
        errors, and what it echoes."""
        compiled = await cls._compile(code, "echo", "model.echo", parameters)
        return {
            "ok": compiled.ok
            and not any(line.startswith("ERROR") for line in problems(compiled.log)),
            "problems": problems(compiled.log)[:MAX_PROBLEMS],
            "echo": compiled.body.decode("utf-8", "replace"),
        }
