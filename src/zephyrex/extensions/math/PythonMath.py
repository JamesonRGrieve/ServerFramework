import math
import statistics
from typing import Any, Dict, List, Optional

from zephyrex.extensions.math.PRV_Math import AbstractMathProvider


class PythonMathProvider(AbstractMathProvider):
    """
    Math provider that uses Python's built-in math and statistics capabilities.
    This provider handles basic arithmetic, trigonometric functions, and simple statistics.
    """

    def __init__(
        self,
        agent_name: str = "",
        precision: int = 10,
        extension_id: Optional[str] = None,
        conversation_name: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(
            agent_name=agent_name,
            precision=precision,
            extension_id=extension_id,
            conversation_name=conversation_name,
            **kwargs,
        )

        # Set up command mapping
        self.commands = {
            "Calculate with Math": self.calculate,
            "Solve Equation with Math": self.solve_equation,
            "Find Derivative with Math": self.differentiate,
            "Calculate Integral with Math": self.integrate,
            "Plot Function with Math": self.plot,
            "Solve System of Equations with Math": self.solve_system,
            "Perform Statistical Analysis with Math": self.statistics,
        }

    async def calculate(self, expression: str) -> str:
        """
        Calculate the result of a mathematical expression using Python's eval.

        Args:
            expression: The mathematical expression to evaluate

        Returns:
            The result of the calculation as a string
        """
        try:
            # Create a safe namespace with only math functions
            safe_dict = {
                "abs": abs,
                "round": round,
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
                "sqrt": math.sqrt,
                "pow": math.pow,
                "pi": math.pi,
                "e": math.e,
                "degrees": math.degrees,
                "radians": math.radians,
                "ceil": math.ceil,
                "floor": math.floor,
            }

            # Replace common mathematical notations with Python syntax
            expression = expression.replace("^", "**")

            # Evaluate the expression in the safe namespace
            result = eval(expression, {"__builtins__": {}}, safe_dict)

            # Format the result based on precision
            if isinstance(result, float):
                formatted_result = f"{result:.{self.precision}g}"
            else:
                formatted_result = str(result)

            return formatted_result
        except Exception as e:
            return f"Error calculating expression: {str(e)}"

    async def solve_equation(
        self, equation: str, variable: Optional[str] = None
    ) -> str:
        """
        Attempt to solve a basic equation using Python.
        Note: This implementation is limited to very simple cases.

        Args:
            equation: The equation to solve
            variable: The variable to solve for (optional)

        Returns:
            The solution as a string or an error message
        """
        return "This Python Math provider has limited equation solving capabilities. Consider using SymPy or Wolfram Alpha for equation solving."

    async def differentiate(
        self, expression: str, variable: str, order: int = 1
    ) -> str:
        """
        Calculate the derivative of an expression.

        Args:
            expression: The expression to differentiate
            variable: The variable with respect to which to differentiate
            order: The order of the derivative

        Returns:
            An error message as this is not supported in basic Python
        """
        return "Differentiation is not supported in the Python Math provider. Consider using SymPy or Wolfram Alpha for calculus operations."

    async def integrate(
        self,
        expression: str,
        variable: str,
        lower_bound: Optional[str] = None,
        upper_bound: Optional[str] = None,
    ) -> str:
        """
        Calculate the integral of an expression.

        Args:
            expression: The expression to integrate
            variable: The variable with respect to which to integrate
            lower_bound: The lower bound for definite integral (optional)
            upper_bound: The upper bound for definite integral (optional)

        Returns:
            An error message as this is not supported in basic Python
        """
        return "Integration is not supported in the Python Math provider. Consider using SymPy or Wolfram Alpha for calculus operations."

    async def plot(
        self, expression: str, variable: str, start: float = -10, end: float = 10
    ) -> str:
        """
        Plot a function over the given range.

        Args:
            expression: The expression to plot
            variable: The variable to use for the x-axis
            start: The starting x value
            end: The ending x value

        Returns:
            An error message as this is not supported in basic Python
        """
        return "Plotting is not supported in the Python Math provider. Consider using SymPy or a dedicated plotting provider."

    async def solve_system(
        self, equations: List[str], variables: Optional[List[str]] = None
    ) -> str:
        """
        Solve a system of equations.

        Args:
            equations: A list of equation strings
            variables: A list of variables to solve for (optional)

        Returns:
            An error message as this is not supported in basic Python
        """
        return "Solving systems of equations is not supported in the Python Math provider. Consider using SymPy or Wolfram Alpha."

    async def statistics(self, data: str, operation: str) -> str:
        """
        Perform statistical operations on data.

        Args:
            data: The data as a string (comma separated values)
            operation: The statistical operation to perform

        Returns:
            The result of the statistical operation as a string
        """
        try:
            # Parse the data
            values = [float(x.strip()) for x in data.split(",") if x.strip()]

            if not values:
                return "No valid data provided"

            operation = operation.lower()
            result = None

            # Perform the requested statistical operation
            if operation == "mean":
                result = statistics.mean(values)
            elif operation == "median":
                result = statistics.median(values)
            elif operation == "mode":
                try:
                    result = statistics.mode(values)
                except statistics.StatisticsError:
                    return "No unique mode found"
            elif operation == "stdev":
                if len(values) < 2:
                    return "Standard deviation requires at least two data points"
                result = statistics.stdev(values)
            elif operation == "variance":
                if len(values) < 2:
                    return "Variance requires at least two data points"
                result = statistics.variance(values)
            elif operation == "min":
                result = min(values)
            elif operation == "max":
                result = max(values)
            elif operation == "sum":
                result = sum(values)
            elif operation == "count":
                result = len(values)
            else:
                return f"Unsupported statistical operation: {operation}"

            # Format the result based on precision
            if isinstance(result, float):
                return f"{result:.{self.precision}g}"
            return str(result)

        except Exception as e:
            return f"Error in statistical calculation: {str(e)}"

    def get_math_type(self) -> str:
        """
        Get the type of math service implemented.

        Returns:
            The type of math service
        """
        return "python"

    def get_math_classifications(self) -> List[str]:
        """
        Get the classifications for this math provider.

        Returns:
            List of math classifications
        """
        return ["basic", "numeric", "statistics"]

    @staticmethod
    def get_services() -> List[str]:
        """
        Get the list of services provided by this provider.

        Returns:
            List of service names
        """
        return [
            "basic arithmetic",
            "trigonometric functions",
            "exponential and logarithmic functions",
            "basic statistics",
        ]

    def get_extension_info(self) -> Dict[str, Any]:
        """
        Get information about this extension.

        Returns:
            Dictionary with extension information
        """
        return {
            "name": "Python Math Provider",
            "description": "Basic mathematical operations using Python's built-in functions",
            "version": "1.0.0",
            "author": "AGInfrastructure",
            "services": self.get_services(),
            "capabilities": self.get_math_classifications(),
        }
