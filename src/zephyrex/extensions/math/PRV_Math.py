from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class AbstractMathProvider(ABC):
    """
    Abstract base class for math providers.
    Defines the interface that must be implemented by all math providers.
    """

    def __init__(
        self,
        agent_name: str = "",
        precision: int = 10,
        extension_id: Optional[str] = None,
        conversation_name: Optional[str] = None,
        **kwargs,
    ):
        """
        Initialize the math provider.

        Args:
            agent_name: Name of the agent using the provider
            precision: Number of significant digits for floating-point results
            extension_id: Unique identifier for this extension instance
            conversation_name: Name of the conversation using this provider
            **kwargs: Additional provider-specific configuration options
        """
        self.agent_name = agent_name
        self.precision = precision
        self.extension_id = extension_id
        self.conversation_name = conversation_name
        self.commands = {}  # To be populated by implementing classes

    @abstractmethod
    async def calculate(self, expression: str) -> str:
        """
        Calculate the result of a mathematical expression.

        Args:
            expression: The mathematical expression to evaluate

        Returns:
            The result of the calculation as a string
        """
        pass

    @abstractmethod
    async def solve_equation(
        self, equation: str, variable: Optional[str] = None
    ) -> str:
        """
        Solve an equation for a variable.

        Args:
            equation: The equation to solve
            variable: The variable to solve for (optional)

        Returns:
            The solution as a string
        """
        pass

    @abstractmethod
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
            The derivative as a string
        """
        pass

    @abstractmethod
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
            The integral as a string
        """
        pass

    @abstractmethod
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
            A reference to the generated plot or an error message
        """
        pass

    @abstractmethod
    async def solve_system(
        self, equations: List[str], variables: Optional[List[str]] = None
    ) -> str:
        """
        Solve a system of equations.

        Args:
            equations: A list of equation strings
            variables: A list of variables to solve for (optional)

        Returns:
            The solution as a string
        """
        pass

    @abstractmethod
    async def statistics(self, data: str, operation: str) -> str:
        """
        Perform statistical operations on data.

        Args:
            data: The data as a string (comma separated values)
            operation: The statistical operation to perform

        Returns:
            The result of the statistical operation as a string
        """
        pass

    @abstractmethod
    def get_math_type(self) -> str:
        """
        Get the type of math service implemented.

        Returns:
            The type of math service
        """
        pass

    @abstractmethod
    def get_math_classifications(self) -> List[str]:
        """
        Get the classifications for this math provider.

        Returns:
            List of math classifications
        """
        pass

    @staticmethod
    @abstractmethod
    def get_services() -> List[str]:
        """
        Get the list of services provided by this provider.

        Returns:
            List of service names
        """
        pass

    @abstractmethod
    def get_extension_info(self) -> Dict[str, Any]:
        """
        Get information about this extension.

        Returns:
            Dictionary with extension information
        """
        pass
