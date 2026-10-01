# SPDX-License-Identifier: AGPL-3.0-or-later
"""Mathematics: arithmetic, equation solving, derivatives and integrals
through the provider rotation (SymPy locally, or Wolfram|Alpha), and
descriptive statistics and line graphs computed here.

Every provider answers in the same shapes: ``calculate`` gives
``{"result", "decimal"}``, ``solve`` ``{"variable", "solutions"}``,
``differentiate`` ``{"result"}`` and ``integrate`` ``{"result",
"evaluated"}`` (False when no closed form was found), each with the
``provider`` that answered. Expressions use ``^`` or ``**`` for powers.
"""

import asyncio
import base64
import io
import math
import statistics
from abc import abstractmethod
from typing import Any, ClassVar, Dict, List, Optional, Set

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractProviderInstance,
    AbstractStaticExtension,
    AbstractStaticProvider,
    ability,
)
from zephyrex.extensions.ExternalErrors import InvalidInputExternalError
from zephyrex.extensions.math.Symbolic import MAX_EXPRESSION_LENGTH, VARIABLE_NAME
from zephyrex.lib.Dependencies import Dependencies, PIP_Dependency
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

MATH_REQUEST_TIMEOUT_SECONDS = 20.0
MAX_DERIVATIVE_ORDER = 10
MAX_DATA_POINTS = 100_000
MAX_TITLE_LENGTH = 200
GRAPH_SIZE_INCHES = (8.0, 6.0)
GRAPH_DPI = 100


def checked_text(text: str, what: str) -> str:
    if not text.strip():
        raise InvalidInputExternalError(f"{what} is empty")
    if len(text) > MAX_EXPRESSION_LENGTH:
        raise InvalidInputExternalError(
            f"{what} is at most {MAX_EXPRESSION_LENGTH} characters"
        )
    return text


def checked_variable(name: str) -> str:
    if not VARIABLE_NAME.match(name):
        raise InvalidInputExternalError(f"{name!r} is not a variable name")
    return name


def checked_data(values: List[float], what: str) -> List[float]:
    if not values:
        raise InvalidInputExternalError(f"{what} is empty")
    if len(values) > MAX_DATA_POINTS:
        raise InvalidInputExternalError(f"{what} has over {MAX_DATA_POINTS} values")
    numbers = [float(value) for value in values]
    if not all(math.isfinite(number) for number in numbers):
        raise InvalidInputExternalError(f"{what} holds a non-finite value")
    return numbers


def describe(values: List[float]) -> Dict[str, Any]:
    """Descriptive statistics of ``values``; the sample spread is None for
    a single value."""
    data = checked_data(values, "data")
    several = len(data) > 1
    return {
        "count": len(data),
        "sum": math.fsum(data),
        "mean": statistics.fmean(data),
        "median": statistics.median(data),
        "min": min(data),
        "max": max(data),
        "population_std_dev": statistics.pstdev(data),
        "population_variance": statistics.pvariance(data),
        "sample_std_dev": statistics.stdev(data) if several else None,
        "sample_variance": statistics.variance(data) if several else None,
    }


def line_graph(x_data: List[float], y_data: List[float], title: str) -> bytes:
    """A PNG line graph of ``y_data`` against ``x_data``."""
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    xs, ys = checked_data(x_data, "x_data"), checked_data(y_data, "y_data")
    if len(xs) != len(ys):
        raise InvalidInputExternalError("x_data and y_data differ in length")
    if len(title) > MAX_TITLE_LENGTH:
        raise InvalidInputExternalError(
            f"title is at most {MAX_TITLE_LENGTH} characters"
        )
    # The object API, not pyplot: pyplot's global state is not thread-safe.
    figure = Figure(figsize=GRAPH_SIZE_INCHES, dpi=GRAPH_DPI)
    FigureCanvasAgg(figure)
    axes = figure.add_subplot()
    axes.plot(xs, ys)
    axes.set_title(title)
    axes.grid(True)
    buffer = io.BytesIO()
    figure.savefig(buffer, format="png")
    return buffer.getvalue()


class AbstractMathProvider(AbstractStaticProvider):
    """A mathematics engine."""

    name: ClassVar[str] = ""
    friendly_name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    _abilities: ClassVar[Set[str]] = {
        "calculate_expression",
        "solve_equation",
        "differentiate",
        "integrate",
    }
    _env: ClassVar[Dict[str, Any]] = {}
    http_timeout_seconds: ClassVar[float] = MATH_REQUEST_TIMEOUT_SECONDS

    @classmethod
    def bond_instance(cls, instance: ProviderInstanceModel) -> AbstractProviderInstance:
        return AbstractProviderInstance(instance)

    @classmethod
    @abstractmethod
    async def calculate(
        cls, instance: ProviderInstanceModel, expression: str
    ) -> Dict[str, Any]:
        """The value of a numeric expression."""

    @classmethod
    @abstractmethod
    async def solve(
        cls, instance: ProviderInstanceModel, equation: str, variable: Optional[str]
    ) -> Dict[str, Any]:
        """The solutions of ``equation`` for ``variable`` (its only unknown
        when None)."""

    @classmethod
    @abstractmethod
    async def differentiate(
        cls, instance: ProviderInstanceModel, expression: str, variable: str, order: int
    ) -> Dict[str, Any]:
        """The ``order``-th derivative."""

    @classmethod
    @abstractmethod
    async def integrate(
        cls,
        instance: ProviderInstanceModel,
        expression: str,
        variable: str,
        lower: Optional[str],
        upper: Optional[str],
    ) -> Dict[str, Any]:
        """The indefinite integral, or the definite one between bounds."""

    @classmethod
    def services(cls) -> List[str]:
        return ["math", "symbolic_math"]


class EXT_Math(AbstractStaticExtension):
    name: ClassVar[str] = "math"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Arithmetic, equations, calculus, statistics and graphs, with SymPy "
        "or Wolfram|Alpha"
    )

    _env: ClassVar[Dict[str, Any]] = {}
    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            PIP_Dependency(
                name="sympy",
                friendly_name="SymPy",
                semver=">=1.12",
                reason="Parsing expressions and the local symbolic engine",
            ),
            PIP_Dependency(
                name="matplotlib",
                friendly_name="Matplotlib",
                semver=">=3.7",
                reason="Rendering line graphs",
            ),
        ]
    )
    _abilities: ClassVar[Set[str]] = {
        "calculate_expression",
        "solve_equation",
        "differentiate",
        "integrate",
        "analyze_statistics",
        "create_graph",
    }

    @classmethod
    @ability("calculate_expression")
    async def calculate_expression(cls, expression: str) -> Dict[str, Any]:
        """The value of a numeric expression, exact and as a decimal."""
        result: Dict[str, Any] = await cls.rotate_provider(
            "calculate", checked_text(expression, "expression")
        )
        return result

    @classmethod
    @ability("solve_equation")
    async def solve_equation(
        cls, equation: str, variable: Optional[str] = None
    ) -> Dict[str, Any]:
        """The solutions of ``lhs = rhs`` (a bare expression equals zero)."""
        result: Dict[str, Any] = await cls.rotate_provider(
            "solve",
            checked_text(equation, "equation"),
            checked_variable(variable) if variable else None,
        )
        return result

    @classmethod
    @ability("differentiate")
    async def differentiate(
        cls, expression: str, variable: str = "x", order: int = 1
    ) -> Dict[str, Any]:
        """The ``order``-th derivative with respect to ``variable``."""
        if not 1 <= order <= MAX_DERIVATIVE_ORDER:
            raise InvalidInputExternalError(f"order must be 1-{MAX_DERIVATIVE_ORDER}")
        result: Dict[str, Any] = await cls.rotate_provider(
            "differentiate",
            checked_text(expression, "expression"),
            checked_variable(variable),
            order,
        )
        return result

    @classmethod
    @ability("integrate")
    async def integrate(
        cls,
        expression: str,
        variable: str = "x",
        lower: Optional[str] = None,
        upper: Optional[str] = None,
    ) -> Dict[str, Any]:
        """The integral with respect to ``variable``; definite when both
        bounds are given."""
        if (lower is None) != (upper is None):
            raise InvalidInputExternalError("a definite integral needs both bounds")
        result: Dict[str, Any] = await cls.rotate_provider(
            "integrate",
            checked_text(expression, "expression"),
            checked_variable(variable),
            checked_text(lower, "lower bound") if lower is not None else None,
            checked_text(upper, "upper bound") if upper is not None else None,
        )
        return result

    @classmethod
    @ability("analyze_statistics")
    async def analyze_statistics(cls, data: List[float]) -> Dict[str, Any]:
        """Count, sum, mean, median, extremes and spread of ``data``."""
        return describe(data)

    @classmethod
    @ability("create_graph")
    async def create_graph(
        cls, x_data: List[float], y_data: List[float], title: str = "Graph"
    ) -> Dict[str, Any]:
        """A line graph, as a base64 PNG."""
        png = await asyncio.to_thread(line_graph, x_data, y_data, title)
        return {
            "title": title,
            "media_type": "image/png",
            "image_base64": base64.b64encode(png).decode("ascii"),
        }
