from typing import Any, Dict, List, Optional

import aiohttp

from zephyrex.extensions.math.PRV_Math import AbstractMathProvider


class WolframAlphaProvider(AbstractMathProvider):
    """
    Math provider that uses Wolfram Alpha API for advanced mathematical computation,
    symbolic mathematics, and visualization.
    """

    def __init__(
        self,
        agent_name: str = "",
        precision: int = 10,
        extension_id: Optional[str] = None,
        conversation_name: Optional[str] = None,
        api_key: str = "",
        api_uri: str = "https://api.wolframalpha.com/v2/",
        timeout: int = 30,
        **kwargs,
    ):
        """
        Initialize the Wolfram Alpha provider.

        Args:
            agent_name: Name of the agent using this provider
            precision: Number of significant digits for floating-point results
            extension_id: Unique identifier for this extension instance
            conversation_name: Name of the conversation using this provider
            api_key: Wolfram Alpha API key
            api_uri: Wolfram Alpha API endpoint
            timeout: Timeout for API requests in seconds
            **kwargs: Additional provider-specific configuration
        """
        super().__init__(
            agent_name=agent_name,
            precision=precision,
            extension_id=extension_id,
            conversation_name=conversation_name,
            **kwargs,
        )

        self.api_key = api_key
        self.api_uri = api_uri
        self.timeout = timeout

        # Check if API key is provided
        if not self.api_key:
            print("WARNING: No Wolfram Alpha API key provided. API calls will fail.")

        # Set up command mapping
        self.commands = {
            "Calculate with Wolfram Alpha": self.calculate,
            "Solve Equation with Wolfram Alpha": self.solve_equation,
            "Find Derivative with Wolfram Alpha": self.differentiate,
            "Calculate Integral with Wolfram Alpha": self.integrate,
            "Plot Function with Wolfram Alpha": self.plot,
            "Solve System of Equations with Wolfram Alpha": self.solve_system,
            "Perform Statistical Analysis with Wolfram Alpha": self.statistics,
        }

    async def _make_api_request(
        self, query: str, params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Make a request to the Wolfram Alpha API.

        Args:
            query: The query to send to Wolfram Alpha
            params: Additional parameters to include in the request

        Returns:
            The API response as a dictionary
        """
        if not self.api_key:
            return {"error": "No API key provided for Wolfram Alpha"}

        # Default parameters
        default_params = {
            "appid": self.api_key,
            "input": query,
            "format": "json",
            "output": "json",
        }

        # Merge with provided parameters
        if params:
            default_params.update(params)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.api_uri}query", params=default_params, timeout=self.timeout
                ) as response:
                    if response.status != 200:
                        return {
                            "error": f"API request failed with status {response.status}"
                        }

                    result = await response.json()
                    return result
        except aiohttp.ClientError as e:
            return {"error": f"API request failed: {str(e)}"}
        except Exception as e:
            return {"error": f"Unexpected error: {str(e)}"}

    async def calculate(self, expression: str) -> str:
        """
        Calculate the result of a mathematical expression using Wolfram Alpha.

        Args:
            expression: The mathematical expression to evaluate

        Returns:
            The result of the calculation as a string
        """
        response = await self._make_api_request(expression)

        if "error" in response:
            return f"Error: {response['error']}"

        try:
            # Extract result from Wolfram Alpha response
            if "queryresult" in response and response["queryresult"]["success"]:
                pods = response["queryresult"]["pods"]

                # Look for the Result pod
                for pod in pods:
                    if pod["title"] == "Result" or pod["title"] == "Solution":
                        return pod["subpods"][0]["plaintext"]

                # If no specific result pod found, return the first relevant information
                return (
                    pods[1]["subpods"][0]["plaintext"]
                    if len(pods) > 1
                    else "No straightforward result found"
                )
            else:
                return "Unable to calculate expression with Wolfram Alpha"
        except Exception as e:
            return f"Error parsing Wolfram Alpha response: {str(e)}"

    async def solve_equation(
        self, equation: str, variable: Optional[str] = None
    ) -> str:
        """
        Solve an equation for a variable using Wolfram Alpha.

        Args:
            equation: The equation to solve
            variable: The variable to solve for (optional)

        Returns:
            The solution as a string
        """
        # Prepare the query
        query = f"solve {equation}"
        if variable:
            query += f" for {variable}"

        response = await self._make_api_request(query)

        if "error" in response:
            return f"Error: {response['error']}"

        try:
            # Extract solution from Wolfram Alpha response
            if "queryresult" in response and response["queryresult"]["success"]:
                pods = response["queryresult"]["pods"]

                # Look for the Solution pod
                for pod in pods:
                    if pod["title"] == "Solution" or pod["title"] == "Solutions":
                        return pod["subpods"][0]["plaintext"]

                # If no specific solution pod found, return the first relevant information
                return (
                    pods[1]["subpods"][0]["plaintext"]
                    if len(pods) > 1
                    else "No solution found"
                )
            else:
                return "Unable to solve equation with Wolfram Alpha"
        except Exception as e:
            return f"Error parsing Wolfram Alpha response: {str(e)}"

    async def differentiate(
        self, expression: str, variable: str, order: int = 1
    ) -> str:
        """
        Calculate the derivative of an expression using Wolfram Alpha.

        Args:
            expression: The expression to differentiate
            variable: The variable with respect to which to differentiate
            order: The order of the derivative

        Returns:
            The derivative as a string
        """
        # Prepare the query
        query = f"differentiate {expression} with respect to {variable}"
        if order > 1:
            query = f"{order}th {query}"

        response = await self._make_api_request(query)

        if "error" in response:
            return f"Error: {response['error']}"

        try:
            # Extract derivative from Wolfram Alpha response
            if "queryresult" in response and response["queryresult"]["success"]:
                pods = response["queryresult"]["pods"]

                # Look for the Derivative pod
                for pod in pods:
                    if pod["title"] == "Derivative" or pod["title"] == "Result":
                        return pod["subpods"][0]["plaintext"]

                # If no specific derivative pod found, return the first relevant information
                return (
                    pods[1]["subpods"][0]["plaintext"]
                    if len(pods) > 1
                    else "No derivative found"
                )
            else:
                return "Unable to differentiate expression with Wolfram Alpha"
        except Exception as e:
            return f"Error parsing Wolfram Alpha response: {str(e)}"

    async def integrate(
        self,
        expression: str,
        variable: str,
        lower_bound: Optional[str] = None,
        upper_bound: Optional[str] = None,
    ) -> str:
        """
        Calculate the integral of an expression using Wolfram Alpha.

        Args:
            expression: The expression to integrate
            variable: The variable with respect to which to integrate
            lower_bound: The lower bound for definite integral (optional)
            upper_bound: The upper bound for definite integral (optional)

        Returns:
            The integral as a string
        """
        # Prepare the query
        if lower_bound is not None and upper_bound is not None:
            query = f"integrate {expression} from {lower_bound} to {upper_bound} with respect to {variable}"
        else:
            query = f"integrate {expression} with respect to {variable}"

        response = await self._make_api_request(query)

        if "error" in response:
            return f"Error: {response['error']}"

        try:
            # Extract integral from Wolfram Alpha response
            if "queryresult" in response and response["queryresult"]["success"]:
                pods = response["queryresult"]["pods"]

                # Look for the Integral pod
                for pod in pods:
                    if (
                        pod["title"] == "Indefinite integral"
                        or pod["title"] == "Definite integral"
                        or pod["title"] == "Result"
                    ):
                        return pod["subpods"][0]["plaintext"]

                # If no specific integral pod found, return the first relevant information
                return (
                    pods[1]["subpods"][0]["plaintext"]
                    if len(pods) > 1
                    else "No integral found"
                )
            else:
                return "Unable to integrate expression with Wolfram Alpha"
        except Exception as e:
            return f"Error parsing Wolfram Alpha response: {str(e)}"

    async def plot(
        self, expression: str, variable: str, start: float = -10, end: float = 10
    ) -> str:
        """
        Plot a function using Wolfram Alpha.

        Args:
            expression: The expression to plot
            variable: The variable to use for the x-axis
            start: The starting x value
            end: The ending x value

        Returns:
            A base64 encoded image of the plot or a description
        """
        # Prepare the query
        query = f"plot {expression} from {variable}={start} to {variable}={end}"

        # Request image format
        params = {
            "format": "image",
            "width": 800,
            "height": 600,
        }

        response = await self._make_api_request(query, params)

        if "error" in response:
            return f"Error: {response['error']}"

        try:
            # Extract plot from Wolfram Alpha response
            if "queryresult" in response and response["queryresult"]["success"]:
                pods = response["queryresult"]["pods"]

                # Look for the Plot pod
                for pod in pods:
                    if "Plot" in pod["title"] or "plot" in pod["title"]:
                        if "img" in pod["subpods"][0]:
                            img_url = pod["subpods"][0]["img"]["src"]
                            # Here we would download the image and encode it as base64
                            return f"Plot available at: {img_url}"

                return "Plot generated but no image URL found"
            else:
                return "Unable to plot function with Wolfram Alpha"
        except Exception as e:
            return f"Error parsing Wolfram Alpha response: {str(e)}"

    async def solve_system(
        self, equations: List[str], variables: Optional[List[str]] = None
    ) -> str:
        """
        Solve a system of equations using Wolfram Alpha.

        Args:
            equations: A list of equation strings
            variables: A list of variables to solve for (optional)

        Returns:
            The solution as a string
        """
        # Prepare the query
        query = f"solve {', '.join(equations)}"
        if variables:
            query += f" for {', '.join(variables)}"

        response = await self._make_api_request(query)

        if "error" in response:
            return f"Error: {response['error']}"

        try:
            # Extract solution from Wolfram Alpha response
            if "queryresult" in response and response["queryresult"]["success"]:
                pods = response["queryresult"]["pods"]

                # Look for the Solution pod
                for pod in pods:
                    if pod["title"] == "Solution" or pod["title"] == "Solutions":
                        return pod["subpods"][0]["plaintext"]

                # If no specific solution pod found, return the first relevant information
                return (
                    pods[1]["subpods"][0]["plaintext"]
                    if len(pods) > 1
                    else "No solution found"
                )
            else:
                return "Unable to solve system of equations with Wolfram Alpha"
        except Exception as e:
            return f"Error parsing Wolfram Alpha response: {str(e)}"

    async def statistics(self, data: str, operation: str) -> str:
        """
        Perform statistical operations on data using Wolfram Alpha.

        Args:
            data: The data as a string (comma separated values)
            operation: The statistical operation to perform

        Returns:
            The result of the statistical operation as a string
        """
        # Prepare the query
        query = f"{operation} of {data}"

        response = await self._make_api_request(query)

        if "error" in response:
            return f"Error: {response['error']}"

        try:
            # Extract statistical result from Wolfram Alpha response
            if "queryresult" in response and response["queryresult"]["success"]:
                pods = response["queryresult"]["pods"]

                # Look for the Result pod
                for pod in pods:
                    if pod["title"] == "Result" or pod["title"] == "Statistical result":
                        return pod["subpods"][0]["plaintext"]

                # If no specific result pod found, return the first relevant information
                return (
                    pods[1]["subpods"][0]["plaintext"]
                    if len(pods) > 1
                    else "No statistical result found"
                )
            else:
                return f"Unable to perform {operation} on the provided data with Wolfram Alpha"
        except Exception as e:
            return f"Error parsing Wolfram Alpha response: {str(e)}"

    def get_math_type(self) -> str:
        """
        Get the type of math service implemented.

        Returns:
            The type of math service
        """
        return "wolfram"

    def get_math_classifications(self) -> List[str]:
        """
        Get the classifications for this math provider.

        Returns:
            List of math classifications
        """
        return ["basic", "symbolic", "numeric", "advanced", "plotting", "statistics"]

    @staticmethod
    def get_services() -> List[str]:
        """
        Get the list of services provided by this provider.

        Returns:
            List of service names
        """
        return [
            "symbolic mathematics",
            "equation solving",
            "calculus (differentiation, integration)",
            "plotting and visualization",
            "system solving",
            "advanced statistics",
            "step-by-step solutions",
        ]

    def get_extension_info(self) -> Dict[str, Any]:
        """
        Get information about this extension.

        Returns:
            Dictionary with extension information
        """
        return {
            "name": "Wolfram Alpha Provider",
            "description": "Advanced mathematical operations using Wolfram Alpha API",
            "version": "1.0.0",
            "author": "AGInfrastructure",
            "services": self.get_services(),
            "capabilities": self.get_math_classifications(),
            "requires_api_key": True,
            "api_documentation": "https://products.wolframalpha.com/api/",
        }
