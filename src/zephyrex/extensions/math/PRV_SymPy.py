# SPDX-License-Identifier: AGPL-3.0-or-later
"""SymPy, computed on this server in a separate worker process (see
``Symbolic``): no account or network, and a computation that runs too
long is stopped rather than held."""

import asyncio
from typing import Any, ClassVar, Dict, Optional

from zephyrex.extensions.ExternalErrors import (
    InvalidInputExternalError,
    PermanentExternalError,
)
from zephyrex.extensions.math.EXT_Math import AbstractMathProvider
from zephyrex.extensions.math.Symbolic import (
    SYMBOLIC_TIMEOUT_SECONDS,
    SymbolicTimeout,
    SymbolicWorker,
)
from zephyrex.logic.BLL_Providers import ProviderInstanceModel


class PRV_SymPy_Math(AbstractMathProvider):
    name: ClassVar[str] = "sympy"
    friendly_name: ClassVar[str] = "SymPy"
    description: ClassVar[str] = "Symbolic mathematics computed on this server"
    _worker: ClassVar[SymbolicWorker] = SymbolicWorker()

    @classmethod
    async def _run(cls, operation: str, *args: Any) -> Dict[str, Any]:
        try:
            status, value = await asyncio.to_thread(cls._worker.run, operation, *args)
        except SymbolicTimeout as exc:
            raise PermanentExternalError(
                f"No {operation} answer within {SYMBOLIC_TIMEOUT_SECONDS:.0f} seconds",
                provider=cls.name,
            ) from exc
        if status == "refused":
            raise InvalidInputExternalError(str(value), provider=cls.name)
        if status != "ok":
            raise PermanentExternalError(
                f"SymPy failed on this input ({value})", provider=cls.name
            )
        return {**value, "provider": cls.name}

    @classmethod
    async def calculate(
        cls, instance: ProviderInstanceModel, expression: str
    ) -> Dict[str, Any]:
        return await cls._run("calculate", expression)

    @classmethod
    async def solve(
        cls, instance: ProviderInstanceModel, equation: str, variable: Optional[str]
    ) -> Dict[str, Any]:
        return await cls._run("solve", equation, variable)

    @classmethod
    async def differentiate(
        cls, instance: ProviderInstanceModel, expression: str, variable: str, order: int
    ) -> Dict[str, Any]:
        return await cls._run("differentiate", expression, variable, order)

    @classmethod
    async def integrate(
        cls,
        instance: ProviderInstanceModel,
        expression: str,
        variable: str,
        lower: Optional[str],
        upper: Optional[str],
    ) -> Dict[str, Any]:
        return await cls._run("integrate", expression, variable, lower, upper)
