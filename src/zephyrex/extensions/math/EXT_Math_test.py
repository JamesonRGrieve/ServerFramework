from unittest.mock import patch

import pytest

from zephyrex.extensions.math.EXT_Math import EXT_Math


class TestMathExtension:
    """Test cases for Math Extension."""

    @pytest.fixture
    def extension(self):
        """Create an EXT_Math instance for testing."""
        return EXT_Math()

    def test_extension_metadata(self, extension):
        """Test extension metadata is correctly set."""
        assert extension.name == "math"
        assert extension.version == "1.0.0"
        assert "mathematical" in extension.description.lower()
        assert "calculations" in extension.description.lower()

    def test_dependencies(self, extension):
        """Test extension dependencies are properly defined."""
        # Check extension dependencies
        ext_deps = {dep.name for dep in extension.ext_dependencies}
        assert "core" in ext_deps

        # Check pip dependencies
        pip_deps = {dep.name for dep in extension.pip_dependencies}
        assert "numpy" in pip_deps
        assert "scipy" in pip_deps
        assert "sympy" in pip_deps
        assert "matplotlib" in pip_deps

        # Check sys dependencies
        assert isinstance(extension.sys_dependencies, list)

    def test_capabilities(self, extension):
        """Test extension capabilities are properly defined."""
        expected_capabilities = {
            "basic_arithmetic",
            "advanced_math",
            "statistical_analysis",
            "graphing",
            "symbolic_math",
            "equation_solving",
        }
        assert set(extension.capabilities) == expected_capabilities

    def test_initialization(self, extension):
        """Test extension initialization."""
        assert hasattr(extension, "calculation_history")
        assert hasattr(extension, "precision")
        assert extension.calculation_history == []
        assert extension.precision == 10

    def test_db_tables(self, extension):
        """Test database tables list."""
        assert isinstance(extension.db_tables, list)
        assert len(extension.db_tables) == 0  # Empty by default

    @patch("zephyrex.extensions.math.EXT_Math.logger")
    def test_on_initialize_success(self, mock_logger, extension):
        """Test successful extension initialization."""
        with patch.object(extension, "register_capability"):
            result = extension.on_initialize()
            assert result is True
            mock_logger.debug.assert_called()

    @patch("zephyrex.extensions.math.EXT_Math.logger")
    def test_on_initialize_failure(self, mock_logger, extension):
        """Test extension initialization failure."""
        with patch.object(
            extension, "register_capability", side_effect=Exception("Test error")
        ):
            result = extension.on_initialize()
            assert result is False
            mock_logger.error.assert_called()

    def test_capability_management(self, extension):
        """Test capability management methods."""
        # Test register_capability
        extension.register_capability("test_capability")
        assert "test_capability" in extension.capabilities

        # Test get_registered_capabilities
        capabilities = extension.get_registered_capabilities()
        assert isinstance(capabilities, set)
        assert "test_capability" in capabilities

        # Test get_capabilities
        capabilities = extension.get_capabilities()
        assert isinstance(capabilities, set)

    @pytest.mark.asyncio
    async def test_calculate_expression_success(self, extension):
        """Test successful expression calculation."""
        result = await extension.calculate_expression("2 + 3 * 4")

        assert result["success"] is True
        assert result["result"] == 14
        assert "2 + 3 * 4" in result["expression"]

    @pytest.mark.asyncio
    async def test_calculate_expression_error(self, extension):
        """Test expression calculation with error."""
        result = await extension.calculate_expression("2 + invalid")

        assert result["success"] is False
        assert "error" in result
        assert "Failed to calculate expression" in result["message"]

    @pytest.mark.asyncio
    async def test_calculate_expression_division_by_zero(self, extension):
        """Test expression calculation with division by zero."""
        result = await extension.calculate_expression("5 / 0")

        assert result["success"] is False
        assert "division by zero" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_solve_equation_success(self, extension):
        """Test successful equation solving."""
        result = await extension.solve_equation("x**2 - 4", "x")

        assert result["success"] is True
        assert "solutions" in result
        assert len(result["solutions"]) == 2  # x = 2 and x = -2

    @pytest.mark.asyncio
    async def test_solve_equation_no_solutions(self, extension):
        """Test equation solving with no solutions."""
        result = await extension.solve_equation("x**2 + 1", "x")

        assert result["success"] is True
        # Should have complex solutions or empty list depending on implementation

    @pytest.mark.asyncio
    async def test_solve_equation_error(self, extension):
        """Test equation solving with error."""
        result = await extension.solve_equation("invalid equation", "x")

        assert result["success"] is False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_solve_equation_default_variable(self, extension):
        """Test equation solving with default variable."""
        result = await extension.solve_equation("x**2 - 9")

        assert result["success"] is True
        # Should solve for 'x' by default

    @pytest.mark.asyncio
    async def test_analyze_statistics_success(self, extension):
        """Test successful statistical analysis."""
        data = [1, 2, 3, 4, 5]
        result = await extension.analyze_statistics(data)

        assert result["success"] is True
        assert "mean" in result
        assert "median" in result
        assert "std_dev" in result
        assert "variance" in result
        assert result["mean"] == 3.0
        assert result["median"] == 3.0

    @pytest.mark.asyncio
    async def test_analyze_statistics_empty_data(self, extension):
        """Test statistical analysis with empty data."""
        result = await extension.analyze_statistics([])

        assert result["success"] is False
        assert "Cannot analyze empty dataset" in result["message"]

    @pytest.mark.asyncio
    async def test_analyze_statistics_single_value(self, extension):
        """Test statistical analysis with single value."""
        result = await extension.analyze_statistics([5])

        assert result["success"] is True
        assert result["mean"] == 5.0
        assert result["median"] == 5.0
        assert result["std_dev"] == 0.0
        assert result["variance"] == 0.0

    @pytest.mark.asyncio
    async def test_analyze_statistics_error(self, extension):
        """Test statistical analysis with error."""
        with patch(
            "extensions.math.EXT_Math.np.mean", side_effect=Exception("Calc error")
        ):
            result = await extension.analyze_statistics([1, 2, 3])

            assert result["success"] is False
            assert "Failed to analyze statistics" in result["message"]

    @pytest.mark.asyncio
    async def test_create_graph_success(self, extension):
        """Test successful graph creation."""
        x_data = [1, 2, 3, 4]
        y_data = [1, 4, 9, 16]

        result = await extension.create_graph(x_data, y_data, "Quadratic Function")

        assert result["success"] is True
        assert "graph_path" in result
        assert result["title"] == "Quadratic Function"

    @pytest.mark.asyncio
    async def test_create_graph_mismatched_data(self, extension):
        """Test graph creation with mismatched data lengths."""
        x_data = [1, 2, 3]
        y_data = [1, 4]

        result = await extension.create_graph(x_data, y_data)

        assert result["success"] is False
        assert "Data arrays must have the same length" in result["message"]

    @pytest.mark.asyncio
    async def test_create_graph_empty_data(self, extension):
        """Test graph creation with empty data."""
        result = await extension.create_graph([], [])

        assert result["success"] is False
        assert "Data arrays cannot be empty" in result["message"]

    @pytest.mark.asyncio
    async def test_create_graph_error(self, extension):
        """Test graph creation with error."""
        with patch(
            "extensions.math.EXT_Math.plt.plot", side_effect=Exception("Plot error")
        ):
            result = await extension.create_graph([1, 2], [1, 4])

            assert result["success"] is False
            assert "Failed to create graph" in result["message"]

    @pytest.mark.asyncio
    async def test_create_graph_default_title(self, extension):
        """Test graph creation with default title."""
        result = await extension.create_graph([1, 2], [3, 4])

        assert result["success"] is True
        assert result["title"] == "Mathematical Graph"

    def test_add_to_history(self, extension):
        """Test adding calculations to history."""
        extension._add_to_history("2 + 2", 4)

        assert len(extension.calculation_history) == 1
        assert extension.calculation_history[0]["expression"] == "2 + 2"
        assert extension.calculation_history[0]["result"] == 4
        assert "timestamp" in extension.calculation_history[0]

    def test_history_limit(self, extension):
        """Test calculation history limit."""
        # Add more than 100 calculations
        for i in range(105):
            extension._add_to_history(f"{i} + 1", i + 1)

        # Should maintain only last 100
        assert len(extension.calculation_history) == 100
        assert (
            extension.calculation_history[0]["expression"] == "5 + 1"
        )  # First 5 removed

    def test_get_calculation_history(self, extension):
        """Test getting calculation history."""
        extension._add_to_history("3 * 4", 12)
        extension._add_to_history("5 / 2", 2.5)

        history = extension.get_calculation_history()

        assert len(history) == 2
        assert history[0]["expression"] == "3 * 4"
        assert history[1]["expression"] == "5 / 2"

    def test_clear_history(self, extension):
        """Test clearing calculation history."""
        extension._add_to_history("1 + 1", 2)
        extension.clear_history()

        assert len(extension.calculation_history) == 0

    def test_lifecycle_methods(self, extension):
        """Test extension lifecycle methods."""
        # Test on_start
        assert extension.on_start() is True

        # Test on_stop
        assert extension.on_stop() is True

        # Test on_startup and on_shutdown
        extension.on_startup()  # Should not raise exception
        extension.on_shutdown()  # Should not raise exception

    def test_validate_config_success(self, extension):
        """Test successful configuration validation."""
        issues = extension.validate_config()
        assert isinstance(issues, list)

    def test_validate_config_invalid_precision(self, extension):
        """Test configuration validation with invalid precision."""
        extension.precision = -1

        issues = extension.validate_config()

        assert any("Precision must be a positive integer" in issue for issue in issues)

    def test_validate_config_zero_precision(self, extension):
        """Test configuration validation with zero precision."""
        extension.precision = 0

        issues = extension.validate_config()

        assert any("Precision must be a positive integer" in issue for issue in issues)

    def test_get_required_permissions(self, extension):
        """Test required permissions."""
        permissions = extension.get_required_permissions()
        assert isinstance(permissions, list)
        assert len(permissions) > 0

        expected_permissions = [
            "math:calculate",
            "math:analyze",
            "math:graph",
            "file:write",  # For saving graphs
        ]
        assert set(permissions) == set(expected_permissions)

    def test_custom_configuration(self):
        """Test extension with custom configuration."""
        extension = EXT_Math(precision=15)

        assert extension.precision == 15

    def test_safe_eval_basic_operations(self, extension):
        """Test safe_eval with basic operations."""
        result = extension._safe_eval("2 + 3")
        assert result == 5

        result = extension._safe_eval("10 - 4")
        assert result == 6

        result = extension._safe_eval("3 * 7")
        assert result == 21

        result = extension._safe_eval("15 / 3")
        assert result == 5

    def test_safe_eval_complex_expressions(self, extension):
        """Test safe_eval with complex expressions."""
        result = extension._safe_eval("2 ** 3")
        assert result == 8

        result = extension._safe_eval("(2 + 3) * 4")
        assert result == 20

        result = extension._safe_eval("10 % 3")
        assert result == 1

    def test_safe_eval_math_functions(self, extension):
        """Test safe_eval with math functions."""
        result = extension._safe_eval("sqrt(16)")
        assert result == 4

        result = extension._safe_eval("abs(-5)")
        assert result == 5

        result = extension._safe_eval("sin(0)")
        assert result == 0

    def test_safe_eval_restricted_functions(self, extension):
        """Test safe_eval blocks restricted functions."""
        with pytest.raises(Exception):
            extension._safe_eval("__import__('os')")

        with pytest.raises(Exception):
            extension._safe_eval("eval('2+2')")

        with pytest.raises(Exception):
            extension._safe_eval("exec('print(1)')")

    def test_safe_eval_invalid_syntax(self, extension):
        """Test safe_eval with invalid syntax."""
        with pytest.raises(Exception):
            extension._safe_eval("2 + + 3")

        with pytest.raises(Exception):
            extension._safe_eval("(2 + 3")

    def test_format_result_precision(self, extension):
        """Test result formatting with precision."""
        extension.precision = 3
        result = extension._format_result(3.14159265)
        assert result == 3.142

        extension.precision = 0
        result = extension._format_result(3.14159265)
        assert result == 3

    def test_format_result_integer(self, extension):
        """Test result formatting with integers."""
        result = extension._format_result(5)
        assert result == 5
        assert isinstance(result, int)

    def test_format_result_large_numbers(self, extension):
        """Test result formatting with large numbers."""
        result = extension._format_result(1234567890.123456789)
        assert isinstance(result, float)

    def test_has_capability(self, extension):
        """Test has_capability method."""
        assert extension.has_capability("basic_arithmetic") is True
        assert extension.has_capability("advanced_math") is True
        assert extension.has_capability("statistical_analysis") is True
        assert extension.has_capability("graphing") is True
        assert extension.has_capability("symbolic_math") is True
        assert extension.has_capability("equation_solving") is True
        assert extension.has_capability("nonexistent_capability") is False

    @pytest.mark.asyncio
    async def test_scientific_notation_handling(self, extension):
        """Test handling of scientific notation."""
        result = await extension.calculate_expression("1e6")
        assert result["success"] is True
        assert result["result"] == 1000000

        result = await extension.calculate_expression("2.5e-3")
        assert result["success"] is True
        assert result["result"] == 0.0025

    @pytest.mark.asyncio
    async def test_trigonometric_functions(self, extension):
        """Test trigonometric function calculations."""
        result = await extension.calculate_expression("cos(0)")
        assert result["success"] is True
        assert result["result"] == 1

        result = await extension.calculate_expression("tan(0)")
        assert result["success"] is True
        assert result["result"] == 0

    @pytest.mark.asyncio
    async def test_logarithmic_functions(self, extension):
        """Test logarithmic function calculations."""
        result = await extension.calculate_expression("log(10)")
        assert result["success"] is True
        # Natural log of 10

        result = await extension.calculate_expression("log10(100)")
        assert result["success"] is True
        assert result["result"] == 2

    @pytest.mark.asyncio
    async def test_constants_usage(self, extension):
        """Test usage of mathematical constants."""
        result = await extension.calculate_expression("pi")
        assert result["success"] is True
        assert 3.14 < result["result"] < 3.15

        result = await extension.calculate_expression("e")
        assert result["success"] is True
        assert 2.71 < result["result"] < 2.72

    def test_precision_setting_and_getting(self, extension):
        """Test setting and getting precision."""
        extension.set_precision(5)
        assert extension.precision == 5

        precision = extension.get_precision()
        assert precision == 5

    def test_invalid_precision_setting(self, extension):
        """Test setting invalid precision values."""
        with pytest.raises(ValueError):
            extension.set_precision(-1)

        with pytest.raises(ValueError):
            extension.set_precision(0)

        with pytest.raises(TypeError):
            extension.set_precision("invalid")

    @pytest.mark.asyncio
    async def test_very_large_numbers(self, extension):
        """Test calculations with very large numbers."""
        result = await extension.calculate_expression("2 ** 100")
        assert result["success"] is True
        assert result["result"] > 0

    @pytest.mark.asyncio
    async def test_very_small_numbers(self, extension):
        """Test calculations with very small numbers."""
        result = await extension.calculate_expression("1e-100")
        assert result["success"] is True
        assert result["result"] > 0

    def test_history_persistence_across_calculations(self, extension):
        """Test that history persists across multiple calculations."""
        # Perform multiple calculations
        extension._add_to_history("1 + 1", 2)
        extension._add_to_history("2 * 3", 6)
        extension._add_to_history("10 / 2", 5)

        history = extension.get_calculation_history()
        assert len(history) == 3

        # Verify order (most recent first or chronological)
        expressions = [h["expression"] for h in history]
        assert "1 + 1" in expressions
        assert "2 * 3" in expressions
        assert "10 / 2" in expressions

    @pytest.mark.asyncio
    async def test_edge_case_equations(self, extension):
        """Test edge case equations."""
        # Linear equation
        result = await extension.solve_equation("x - 5", "x")
        assert result["success"] is True

        # Quadratic with single solution
        result = await extension.solve_equation("x**2 - 2*x + 1", "x")
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_statistical_edge_cases(self, extension):
        """Test statistical analysis edge cases."""
        # All same values
        result = await extension.analyze_statistics([5, 5, 5, 5, 5])
        assert result["success"] is True
        assert result["std_dev"] == 0.0
        assert result["variance"] == 0.0

        # Two values
        result = await extension.analyze_statistics([1, 2])
        assert result["success"] is True
        assert result["mean"] == 1.5
        assert result["median"] == 1.5

    @pytest.mark.asyncio
    async def test_graph_with_negative_values(self, extension):
        """Test graph creation with negative values."""
        x_data = [-2, -1, 0, 1, 2]
        y_data = [4, 1, 0, 1, 4]

        result = await extension.create_graph(x_data, y_data, "Parabola")
        assert result["success"] is True

    def test_memory_management_large_history(self, extension):
        """Test memory management with large calculation history."""
        # Add exactly 100 calculations
        for i in range(100):
            extension._add_to_history(f"operation_{i}", i)

        assert len(extension.calculation_history) == 100

        # Add one more - should remove the oldest
        extension._add_to_history("operation_100", 100)
        assert len(extension.calculation_history) == 100

        # First calculation should be removed
        expressions = [h["expression"] for h in extension.calculation_history]
        assert "operation_0" not in expressions
        assert "operation_100" in expressions


if __name__ == "__main__":
    pytest.main([__file__])
