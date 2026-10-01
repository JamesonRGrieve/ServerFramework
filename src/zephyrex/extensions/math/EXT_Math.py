import ast
import math
import operator
import os
import tempfile
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Set

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import sympy  # noqa: E402

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
    ability,
)
from zephyrex.lib.Dependencies import EXT_Dependency, PIP_Dependency
from zephyrex.lib.Logging import logger

# Whitelisted operators/functions for `_safe_eval`. Kept at module scope so
# the mapping is built once (not per-call) and stays trivially auditable --
# nothing outside these dicts is ever reachable from user input.
_ALLOWED_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

# Only unary minus is supported. Unary plus (`+3`) is intentionally excluded
# so malformed-looking expressions such as "2 + + 3" are rejected rather than
# silently accepted.
_ALLOWED_UNARYOPS = {
    ast.USub: operator.neg,
}

_ALLOWED_NAMES: Dict[str, float] = {
    "pi": math.pi,
    "e": math.e,
}

_ALLOWED_FUNCTIONS: Dict[str, Any] = {
    "abs": abs,
    "round": round,
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "sinh": math.sinh,
    "cosh": math.cosh,
    "tanh": math.tanh,
    "exp": math.exp,
    "log": math.log,
    "log10": math.log10,
    "pow": math.pow,
    "ceil": math.ceil,
    "floor": math.floor,
    "degrees": math.degrees,
    "radians": math.radians,
}

# Calculation history is capped so a long-running agent session can't grow
# it without bound.
_MAX_HISTORY_SIZE = 100


class EXT_Math(AbstractStaticExtension):
    """
    Math extension for AGInfrastructure.

    Provides mathematical computation capabilities: safe expression
    evaluation, equation solving, statistical analysis, and graphing.
    """

    # Extension metadata
    name = "math"
    version = "1.0.0"
    description = "Math extension for mathematical calculations and analysis"

    # Define dependencies
    ext_dependencies = [
        EXT_Dependency(
            name="core",
            friendly_name="Core Extension",
            optional=False,
            reason="Required for base extension registration and permissions.",
        ),
    ]

    pip_dependencies = [
        PIP_Dependency(
            name="numpy",
            friendly_name="NumPy",
            optional=False,
            semver=">=1.21.0",
            reason="Statistical analysis (mean, median, std dev, variance).",
        ),
        PIP_Dependency(
            name="scipy",
            friendly_name="SciPy",
            optional=True,
            semver=">=1.7.0",
            reason="Extended scientific computing functions.",
        ),
        PIP_Dependency(
            name="sympy",
            friendly_name="SymPy",
            optional=False,
            semver=">=1.9",
            reason="Symbolic math and equation solving.",
        ),
        PIP_Dependency(
            name="matplotlib",
            friendly_name="Matplotlib",
            optional=False,
            semver=">=3.4",
            reason="Rendering graphs/plots to disk.",
        ),
    ]

    sys_dependencies: List[Any] = []

    # Capabilities this extension provides.
    capabilities: List[str] = [
        "basic_arithmetic",
        "advanced_math",
        "statistical_analysis",
        "graphing",
        "symbolic_math",
        "equation_solving",
    ]

    # Define database tables
    db_tables: List[Any] = []

    def __init__(self, precision: int = 10, **kwargs: Any) -> None:
        """
        Initialize the Math extension.

        Args:
            precision: Number of decimal places used by `_format_result`.
            **kwargs: Forwarded to the base extension.
        """
        super().__init__(**kwargs)

        self.precision = precision
        # Per-instance copies so mutating state (capabilities, history) on
        # one instance never leaks into another via the shared class attrs.
        self.capabilities = list(self.__class__.capabilities)
        self.calculation_history: List[Dict[str, Any]] = []

    def on_initialize(self) -> bool:
        """Initialize the Math extension, registering its capabilities."""
        logger.debug("Initializing Math Extension...")

        try:
            for capability in self.__class__.capabilities:
                self.register_capability(capability)

            logger.debug("Math extension initialized successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize Math extension: {str(e)}")
            return False

    def get_capabilities(self) -> Set[str]:
        """Return the capabilities this extension provides."""
        return set(self.capabilities)

    def register_capability(self, capability: str) -> None:
        """Register a new capability."""
        if capability not in self.capabilities:
            self.capabilities.append(capability)

    def get_registered_capabilities(self) -> Set[str]:
        """Return currently registered capabilities."""
        return set(self.capabilities)

    def has_capability(self, capability: str) -> bool:
        """Check if this extension has a specific capability."""
        return capability in self.capabilities

    @ability("calculate_expression")
    async def calculate_expression(self, expression: str) -> Dict[str, Any]:
        """
        Evaluate a mathematical expression using the safe AST-based evaluator.

        Args:
            expression: The mathematical expression to evaluate.

        Returns:
            A dict with `success`, and either `result`/`expression` or
            `error`/`message` on failure.
        """
        try:
            result = self._safe_eval(expression)
            self._add_to_history(expression, result)
            return {
                "success": True,
                "result": result,
                "expression": expression,
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "message": f"Failed to calculate expression: {str(e)}",
            }

    @ability("solve_equation")
    async def solve_equation(
        self, equation: str, variable: str = "x"
    ) -> Dict[str, Any]:
        """
        Solve a mathematical equation for a given variable using SymPy.

        Args:
            equation: The equation to solve (implicitly set to zero).
            variable: The variable to solve for.

        Returns:
            A dict with `success`, and either `solutions`/`equation`/
            `variable` or `error`/`message` on failure.
        """
        try:
            symbol = sympy.Symbol(variable)
            expr = sympy.sympify(equation)
            solutions = sympy.solve(expr, symbol)
            return {
                "success": True,
                "solutions": solutions,
                "equation": equation,
                "variable": variable,
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "message": f"Failed to solve equation: {str(e)}",
            }

    @ability("analyze_statistics")
    async def analyze_statistics(self, data: List[float]) -> Dict[str, Any]:
        """
        Compute descriptive statistics (mean, median, std dev, variance)
        for a dataset.

        Args:
            data: The numeric data to analyze.

        Returns:
            A dict with `success` and the computed statistics, or
            `success: False` and a `message` describing the failure.
        """
        if not data:
            return {"success": False, "message": "Cannot analyze empty dataset"}

        try:
            array = np.array(data, dtype=float)
            return {
                "success": True,
                "mean": float(np.mean(array)),
                "median": float(np.median(array)),
                "std_dev": float(np.std(array)),
                "variance": float(np.var(array)),
                "count": len(data),
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"Failed to analyze statistics: {str(e)}",
            }

    @ability("create_graph")
    async def create_graph(
        self,
        x_data: List[float],
        y_data: List[float],
        title: str = "Mathematical Graph",
    ) -> Dict[str, Any]:
        """
        Render a line graph of `y_data` against `x_data` to a PNG file.

        Args:
            x_data: X-axis values.
            y_data: Y-axis values.
            title: Graph title.

        Returns:
            A dict with `success`, `graph_path`, and `title` on success, or
            `success: False` and a `message` describing the failure.
        """
        if not x_data or not y_data:
            return {"success": False, "message": "Data arrays cannot be empty"}

        if len(x_data) != len(y_data):
            return {
                "success": False,
                "message": "Data arrays must have the same length",
            }

        try:
            figure = plt.figure()
            plt.plot(x_data, y_data)
            plt.title(title)

            graph_dir = os.path.join(tempfile.gettempdir(), "zephyrex_math_graphs")
            os.makedirs(graph_dir, exist_ok=True)
            graph_path = os.path.join(graph_dir, f"graph_{uuid.uuid4().hex}.png")
            plt.savefig(graph_path)
            plt.close(figure)

            return {
                "success": True,
                "graph_path": graph_path,
                "title": title,
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"Failed to create graph: {str(e)}",
            }

    def _safe_eval(self, expression: str) -> Any:
        """
        Safely evaluate a mathematical expression via a whitelisted AST walk.

        Only literal numbers, the basic arithmetic operators, unary minus,
        the constants `pi`/`e`, and a fixed set of `math` functions are
        reachable -- no name lookup, attribute access, or arbitrary calls.

        Args:
            expression: The expression to evaluate.

        Returns:
            The numeric result.

        Raises:
            ValueError: If the expression uses anything outside the
                whitelist, or contains invalid syntax.
        """
        try:
            tree = ast.parse(expression.replace("^", "**"), mode="eval")
        except SyntaxError as e:
            raise ValueError(f"Invalid expression syntax: {expression}") from e

        return self._eval_ast_node(tree.body)

    def _eval_ast_node(self, node: ast.AST) -> Any:
        """Recursively evaluate a single whitelisted AST node."""
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise ValueError(f"Unsupported constant: {node.value!r}")
            return node.value

        if isinstance(node, ast.BinOp):
            op_type = type(node.op)
            if op_type not in _ALLOWED_BINOPS:
                raise ValueError(f"Unsupported operator: {op_type.__name__}")
            left = self._eval_ast_node(node.left)
            right = self._eval_ast_node(node.right)
            return _ALLOWED_BINOPS[op_type](left, right)

        if isinstance(node, ast.UnaryOp):
            unary_op_type = type(node.op)
            if unary_op_type not in _ALLOWED_UNARYOPS:
                raise ValueError(
                    f"Unsupported unary operator: {unary_op_type.__name__}"
                )
            return _ALLOWED_UNARYOPS[unary_op_type](self._eval_ast_node(node.operand))

        if isinstance(node, ast.Name):
            if node.id in _ALLOWED_NAMES:
                return _ALLOWED_NAMES[node.id]
            raise ValueError(f"Use of name '{node.id}' is not allowed")

        if isinstance(node, ast.Call):
            if (
                not isinstance(node.func, ast.Name)
                or node.func.id not in _ALLOWED_FUNCTIONS
            ):
                func_name = getattr(node.func, "id", "<expression>")
                raise ValueError(f"Use of function '{func_name}' is not allowed")
            if node.keywords:
                raise ValueError("Keyword arguments are not allowed")
            args = [self._eval_ast_node(arg) for arg in node.args]
            return _ALLOWED_FUNCTIONS[node.func.id](*args)

        raise ValueError(f"Unsupported expression element: {type(node).__name__}")

    def _format_result(self, value: Any) -> Any:
        """
        Format a numeric result to the extension's configured precision.

        Integers pass through unchanged; floats are rounded to
        `self.precision` decimal places.
        """
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return round(value, self.precision)
        return value

    def _add_to_history(self, expression: str, result: Any) -> None:
        """Record a calculation in the history, capped at the last 100."""
        self.calculation_history.append(
            {
                "expression": expression,
                "result": result,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        if len(self.calculation_history) > _MAX_HISTORY_SIZE:
            self.calculation_history = self.calculation_history[-_MAX_HISTORY_SIZE:]

    def get_calculation_history(self) -> List[Dict[str, Any]]:
        """Return a copy of the calculation history."""
        return list(self.calculation_history)

    def clear_history(self) -> None:
        """Clear the calculation history."""
        self.calculation_history = []

    def set_precision(self, precision: int) -> None:
        """
        Set the number of decimal places used by `_format_result`.

        Raises:
            TypeError: If `precision` is not an integer.
            ValueError: If `precision` is not positive.
        """
        if not isinstance(precision, int) or isinstance(precision, bool):
            raise TypeError("precision must be an integer")
        if precision < 1:
            raise ValueError("precision must be a positive integer")
        self.precision = precision

    def get_precision(self) -> int:
        """Return the current precision setting."""
        return self.precision

    def get_required_permissions(self) -> List[str]:
        """Return the list of permissions required by this extension."""
        return [
            "math:calculate",
            "math:analyze",
            "math:graph",
            "file:write",  # For saving graphs
        ]

    def on_start(self) -> bool:
        """Start the Math extension."""
        try:
            logger.debug("Math extension started successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to start Math extension: {e}")
            return False

    def on_stop(self) -> bool:
        """Stop the Math extension."""
        try:
            logger.debug("Math extension stopped successfully")
            return True
        except Exception as e:
            logger.error(f"Error stopping Math extension: {e}")
            return False

    def validate_config(self) -> List[str]:
        """Validate the extension configuration."""
        issues = []

        if (
            not isinstance(self.precision, int)
            or isinstance(self.precision, bool)
            or self.precision < 1
        ):
            issues.append("Precision must be a positive integer")

        return issues

    def on_startup(self) -> None:
        """Called during application startup."""
        logger.debug("Math extension startup hook called")

    def on_shutdown(self) -> None:
        """Called during application shutdown."""
        logger.debug("Math extension shutdown hook called")
