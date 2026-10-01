from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from zephyrex.extensions.ecommerce.EXT_ECommerce import EXT_ECommerce
from zephyrex.extensions.ecommerce.PRV_WooCommerce import WooCommerceProvider


class TestECommerceExtension:
    """Test cases for ECommerce Extension."""

    @pytest.fixture
    def extension(self):
        """Create an EXT_ECommerce instance for testing."""
        return EXT_ECommerce()

    def test_extension_metadata(self, extension):
        """Test extension metadata is correctly set."""
        assert extension.name == "ecommerce"
        assert extension.version == "1.0.0"
        assert "e-commerce" in extension.description.lower()
        assert "marketplace" in extension.description.lower()

    def test_dependencies(self, extension):
        """Test extension dependencies are properly defined."""
        # Check extension dependencies
        ext_deps = {dep.name for dep in extension.ext_dependencies}
        assert "core" in ext_deps
        assert "auth" in ext_deps

        # Check pip dependencies
        pip_deps = {dep.name for dep in extension.pip_dependencies}
        assert "requests" in pip_deps
        assert "pydantic" in pip_deps

        # Check sys dependencies
        assert isinstance(extension.sys_dependencies, list)

    def test_capabilities(self, extension):
        """Test extension capabilities are properly defined."""
        expected_capabilities = {
            "order_management",
            "inventory_management",
            "product_management",
            "returns_management",
            "reporting",
            "marketplace_integration",
        }
        assert set(extension.capabilities) == expected_capabilities

    def test_initialization(self, extension):
        """Test extension initialization."""
        assert hasattr(extension, "provider_type")
        assert hasattr(extension, "api_key")
        assert hasattr(extension, "provider")
        assert hasattr(extension, "commands")

    def test_default_configuration(self, extension):
        """Test default configuration values."""
        assert extension.provider_type == "walmart"
        assert extension.api_key == ""

    @patch("zephyrex.extensions.ecommerce.EXT_ECommerce.logger")
    def test_on_initialize_success(self, mock_logger, extension):
        """Test successful extension initialization."""
        with patch.object(extension, "_create_provider"), patch.object(
            extension, "_register_commands"
        ), patch.object(extension, "register_capability"):

            result = extension.on_initialize()
            assert result is True
            mock_logger.debug.assert_called()

    @patch("zephyrex.extensions.ecommerce.EXT_ECommerce.logger")
    def test_on_initialize_failure(self, mock_logger, extension):
        """Test extension initialization failure."""
        with patch.object(
            extension, "_create_provider", side_effect=Exception("Test error")
        ):
            result = extension.on_initialize()
            assert result is False
            mock_logger.error.assert_called()

    def test_create_provider_walmart(self, extension):
        """Test Walmart provider creation."""
        extension.provider_type = "walmart"

        with patch(
            "zephyrex.extensions.ecommerce.PRV_Walmart.WalmartProvider"
        ) as mock_provider:
            mock_instance = MagicMock()
            mock_provider.return_value = mock_instance

            extension._create_provider()

            assert extension.provider == mock_instance
            mock_provider.assert_called_once()

    def test_create_provider_amazon(self, extension):
        """Test Amazon provider creation."""
        extension.provider_type = "amazon"

        with patch(
            "zephyrex.extensions.ecommerce.PRV_Amazon.AmazonProvider"
        ) as mock_provider:
            mock_instance = MagicMock()
            mock_provider.return_value = mock_instance

            extension._create_provider()

            assert extension.provider == mock_instance
            mock_provider.assert_called_once()

    def test_create_provider_shopify(self, extension):
        """Test Shopify provider creation."""
        extension.provider_type = "shopify"

        with patch(
            "zephyrex.extensions.ecommerce.PRV_Shopify.ShopifyProvider"
        ) as mock_provider:
            mock_instance = MagicMock()
            mock_provider.return_value = mock_instance

            extension._create_provider()

            assert extension.provider == mock_instance
            mock_provider.assert_called_once()

    def test_create_provider_woocommerce(self, extension):
        """Test WooCommerce provider creation."""
        extension.provider_type = "woocommerce"

        with patch(
            "zephyrex.extensions.ecommerce.PRV_WooCommerce.WooCommerceProvider"
        ) as mock_provider:
            mock_instance = MagicMock()
            mock_provider.return_value = mock_instance

            extension._create_provider()

            assert extension.provider == mock_instance
            mock_provider.assert_called_once()

    def test_create_provider_unsupported_platform(self, extension):
        """Test provider creation with unsupported platform."""
        extension.provider_type = "unsupported_platform"

        with patch("zephyrex.extensions.ecommerce.EXT_ECommerce.logger") as mock_logger:
            extension._create_provider()

            assert extension.provider is None
            mock_logger.error.assert_called_with(
                "Unsupported e-commerce provider: unsupported_platform"
            )

    def test_create_provider_import_error(self, extension):
        """Test provider creation with import error."""
        extension.provider_type = "walmart"

        with patch(
            "zephyrex.extensions.ecommerce.PRV_Walmart.WalmartProvider",
            side_effect=ImportError("Module not found"),
        ), patch("zephyrex.extensions.ecommerce.EXT_ECommerce.logger") as mock_logger:

            extension._create_provider()

            assert extension.provider is None
            mock_logger.warning.assert_called()

    def test_register_commands_with_provider_capabilities(self, extension):
        """Test command registration when provider has capabilities."""
        mock_provider = MagicMock()
        mock_provider.has_capability = MagicMock(return_value=True)
        extension.provider = mock_provider

        extension._register_commands()

        # Should have all commands for all capabilities
        assert "Get Orders" in extension.commands
        assert "Acknowledge Order" in extension.commands
        assert "Cancel Order" in extension.commands
        assert "Get Inventory" in extension.commands
        assert "Update Inventory" in extension.commands
        assert "Get Products" in extension.commands
        assert "Update Product" in extension.commands
        assert "Get Returns" in extension.commands
        assert "Process Return" in extension.commands
        assert "Generate Report" in extension.commands

    def test_register_commands_without_provider(self, extension):
        """Test command registration when provider is not available."""
        extension.provider = None
        extension.provider_type = "walmart"

        extension._register_commands()

        assert len(extension.commands) == 3
        assert "Get WALMART Orders" in extension.commands
        assert "Manage WALMART Inventory" in extension.commands
        assert "Manage WALMART Products" in extension.commands

    @pytest.mark.asyncio
    async def test_no_provider_warning(self, extension):
        """Test warning message when no provider is available."""
        extension.provider_type = "walmart"

        result = await extension._no_provider_warning()

        assert "No e-commerce provider available for walmart" in result

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

        # Test has_capability
        assert extension.has_capability("test_capability") is True
        assert extension.has_capability("nonexistent_capability") is False

    @pytest.mark.asyncio
    async def test_get_orders_success(self, extension):
        """Test successful order retrieval."""
        mock_provider = MagicMock()
        mock_orders = [
            {"order_id": "1", "status": "shipped", "total": 25.99},
            {"order_id": "2", "status": "pending", "total": 49.99},
        ]
        mock_provider.get_orders = MagicMock(return_value=mock_orders)
        extension.provider = mock_provider

        result = await extension.get_orders(status="shipped", days=7, limit=25)

        assert result["success"] is True
        assert result["orders"] == mock_orders
        assert result["count"] == 2
        mock_provider.get_orders.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_orders_no_provider(self, extension):
        """Test order retrieval without provider."""
        extension.provider = None

        result = await extension.get_orders()

        assert result["success"] is False
        assert "No e-commerce provider available" in result["message"]

    @pytest.mark.asyncio
    async def test_get_orders_empty_result(self, extension):
        """Test order retrieval with empty result."""
        mock_provider = MagicMock()
        mock_provider.get_orders = MagicMock(return_value=None)
        extension.provider = mock_provider

        result = await extension.get_orders()

        assert result["success"] is True
        assert result["orders"] == []
        assert "No orders found" in result["message"]

    @pytest.mark.asyncio
    async def test_acknowledge_order_success(self, extension):
        """Test successful order acknowledgment."""
        mock_provider = MagicMock()
        mock_provider.acknowledge_order = MagicMock(return_value="order_acknowledged")
        extension.provider = mock_provider

        result = await extension.acknowledge_order("order_123")

        assert result["success"] is True
        assert result["result"] == "order_acknowledged"
        assert result["order_id"] == "order_123"
        mock_provider.acknowledge_order.assert_called_once_with("order_123")

    @pytest.mark.asyncio
    async def test_acknowledge_order_no_provider(self, extension):
        """Test order acknowledgment without provider."""
        extension.provider = None

        result = await extension.acknowledge_order("order_123")

        assert result["success"] is False
        assert "No e-commerce provider available" in result["message"]

    @pytest.mark.asyncio
    async def test_cancel_order_success(self, extension):
        """Test successful order cancellation."""
        mock_provider = MagicMock()
        mock_provider.cancel_order = MagicMock(return_value="order_cancelled")
        extension.provider = mock_provider

        result = await extension.cancel_order("order_123", "Customer request")

        assert result["success"] is True
        assert result["result"] == "order_cancelled"
        assert result["order_id"] == "order_123"
        assert result["reason"] == "Customer request"
        mock_provider.cancel_order.assert_called_once_with(
            "order_123", "Customer request"
        )

    @pytest.mark.asyncio
    async def test_cancel_order_no_provider(self, extension):
        """Test order cancellation without provider."""
        extension.provider = None

        result = await extension.cancel_order("order_123", "Test reason")

        assert result["success"] is False
        assert "No e-commerce provider available" in result["message"]

    @pytest.mark.asyncio
    async def test_get_inventory_success(self, extension):
        """Test successful inventory retrieval."""
        mock_provider = MagicMock()
        mock_inventory = [
            {"sku": "ABC123", "quantity": 50, "status": "available"},
            {"sku": "DEF456", "quantity": 0, "status": "out_of_stock"},
        ]
        mock_provider.get_inventory = MagicMock(return_value=mock_inventory)
        extension.provider = mock_provider

        result = await extension.get_inventory("ABC123")

        assert result["success"] is True
        assert result["inventory"] == mock_inventory
        assert result["count"] == 2
        mock_provider.get_inventory.assert_called_once_with(sku_list=["ABC123"])

    @pytest.mark.asyncio
    async def test_get_inventory_no_provider(self, extension):
        """Test inventory retrieval without provider."""
        extension.provider = None

        result = await extension.get_inventory()

        assert result["success"] is False
        assert "No e-commerce provider available" in result["message"]

    @pytest.mark.asyncio
    async def test_get_inventory_empty_result(self, extension):
        """Test inventory retrieval with empty result."""
        mock_provider = MagicMock()
        mock_provider.get_inventory = MagicMock(return_value=None)
        extension.provider = mock_provider

        result = await extension.get_inventory()

        assert result["success"] is True
        assert result["inventory"] == []
        assert "No inventory found" in result["message"]

    @pytest.mark.asyncio
    async def test_update_inventory_success(self, extension):
        """Test successful inventory update."""
        mock_provider = MagicMock()
        mock_provider.update_inventory = MagicMock(return_value="inventory_updated")
        extension.provider = mock_provider

        result = await extension.update_inventory("ABC123", 25)

        assert result["success"] is True
        assert result["result"] == "inventory_updated"
        assert result["sku"] == "ABC123"
        assert result["quantity"] == 25
        mock_provider.update_inventory.assert_called_once_with("ABC123", 25)

    @pytest.mark.asyncio
    async def test_update_inventory_no_provider(self, extension):
        """Test inventory update without provider."""
        extension.provider = None

        result = await extension.update_inventory("ABC123", 25)

        assert result["success"] is False
        assert "No e-commerce provider available" in result["message"]

    @pytest.mark.asyncio
    async def test_get_products_success(self, extension):
        """Test successful product retrieval."""
        mock_provider = MagicMock()
        mock_products = [
            {"sku": "ABC123", "title": "Product 1", "price": 19.99},
            {"sku": "DEF456", "title": "Product 2", "price": 29.99},
        ]
        mock_provider.get_products = MagicMock(return_value=mock_products)
        extension.provider = mock_provider

        result = await extension.get_products(25)

        assert result["success"] is True
        assert result["products"] == mock_products
        assert result["count"] == 2
        mock_provider.get_products.assert_called_once_with(limit=25)

    @pytest.mark.asyncio
    async def test_get_products_no_provider(self, extension):
        """Test product retrieval without provider."""
        extension.provider = None

        result = await extension.get_products()

        assert result["success"] is False
        assert "No e-commerce provider available" in result["message"]

    @pytest.mark.asyncio
    async def test_get_products_empty_result(self, extension):
        """Test product retrieval with empty result."""
        mock_provider = MagicMock()
        mock_provider.get_products = MagicMock(return_value=None)
        extension.provider = mock_provider

        result = await extension.get_products()

        assert result["success"] is True
        assert result["products"] == []
        assert "No products found" in result["message"]

    @pytest.mark.asyncio
    async def test_update_product_success(self, extension):
        """Test successful product update."""
        mock_provider = MagicMock()
        mock_provider.update_product = MagicMock(return_value="product_updated")
        extension.provider = mock_provider

        result = await extension.update_product("ABC123", "price", "24.99")

        assert result["success"] is True
        assert result["result"] == "product_updated"
        assert result["sku"] == "ABC123"
        assert result["field"] == "price"
        assert result["value"] == "24.99"
        mock_provider.update_product.assert_called_once_with("ABC123", "price", "24.99")

    @pytest.mark.asyncio
    async def test_update_product_no_provider(self, extension):
        """Test product update without provider."""
        extension.provider = None

        result = await extension.update_product("ABC123", "price", "24.99")

        assert result["success"] is False
        assert "No e-commerce provider available" in result["message"]

    @pytest.mark.asyncio
    async def test_get_returns_success(self, extension):
        """Test successful returns retrieval."""
        mock_provider = MagicMock()
        mock_returns = [
            {"return_id": "RET001", "order_id": "ORD123", "status": "pending"},
            {"return_id": "RET002", "order_id": "ORD456", "status": "approved"},
        ]
        mock_provider.get_returns = MagicMock(return_value=mock_returns)
        extension.provider = mock_provider

        result = await extension.get_returns(14, 30)

        assert result["success"] is True
        assert result["returns"] == mock_returns
        assert result["count"] == 2
        mock_provider.get_returns.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_returns_no_provider(self, extension):
        """Test returns retrieval without provider."""
        extension.provider = None

        result = await extension.get_returns()

        assert result["success"] is False
        assert "No e-commerce provider available" in result["message"]

    @pytest.mark.asyncio
    async def test_get_returns_empty_result(self, extension):
        """Test returns retrieval with empty result."""
        mock_provider = MagicMock()
        mock_provider.get_returns = MagicMock(return_value=None)
        extension.provider = mock_provider

        result = await extension.get_returns()

        assert result["success"] is True
        assert result["returns"] == []
        assert "No returns found" in result["message"]

    @pytest.mark.asyncio
    async def test_process_return_success(self, extension):
        """Test successful return processing."""
        mock_provider = MagicMock()
        mock_provider.process_return = MagicMock(return_value="return_processed")
        extension.provider = mock_provider

        result = await extension.process_return("RET001", "approve", 29.99)

        assert result["success"] is True
        assert result["result"] == "return_processed"
        assert result["return_id"] == "RET001"
        assert result["action"] == "approve"
        assert result["refund_amount"] == 29.99
        mock_provider.process_return.assert_called_once_with("RET001", "approve", 29.99)

    @pytest.mark.asyncio
    async def test_process_return_no_provider(self, extension):
        """Test return processing without provider."""
        extension.provider = None

        result = await extension.process_return("RET001", "approve")

        assert result["success"] is False
        assert "No e-commerce provider available" in result["message"]

    @pytest.mark.asyncio
    async def test_generate_report_success(self, extension):
        """Test successful report generation."""
        mock_provider = MagicMock()
        mock_report = {"total_sales": 1500.99, "order_count": 45, "return_rate": 0.05}
        mock_provider.generate_report = MagicMock(return_value=mock_report)
        extension.provider = mock_provider

        result = await extension.generate_report("sales", 7)

        assert result["success"] is True
        assert result["report"] == mock_report
        assert result["report_type"] == "sales"
        assert result["days"] == 7
        mock_provider.generate_report.assert_called_once()

    @pytest.mark.asyncio
    async def test_generate_report_no_provider(self, extension):
        """Test report generation without provider."""
        extension.provider = None

        result = await extension.generate_report("sales")

        assert result["success"] is False
        assert "No e-commerce provider available" in result["message"]

    @pytest.mark.asyncio
    async def test_ability_error_handling(self, extension):
        """Test error handling in abilities."""
        mock_provider = MagicMock()
        mock_provider.get_orders = MagicMock(side_effect=Exception("Provider error"))
        extension.provider = mock_provider

        result = await extension.get_orders()

        assert result["success"] is False
        assert "Error retrieving orders" in result["message"]

    def test_lifecycle_methods(self, extension):
        """Test extension lifecycle methods."""
        # Test on_start
        assert extension.on_start() is True

        # Test on_stop
        extension.provider = MagicMock()
        assert extension.on_stop() is True
        assert extension.provider is None

        # Test on_startup and on_shutdown
        extension.on_startup()  # Should not raise exception
        extension.on_shutdown()  # Should not raise exception

    def test_validate_config_success(self, extension):
        """Test successful configuration validation."""
        with patch("builtins.__import__"):
            issues = extension.validate_config()
            assert isinstance(issues, list)

    def test_validate_config_missing_requests(self, extension):
        """Test configuration validation with missing requests library."""
        with patch("builtins.__import__", side_effect=ImportError("Module not found")):
            issues = extension.validate_config()

            assert len(issues) > 0
            assert any("Requests library not installed" in issue for issue in issues)

    def test_validate_config_missing_pydantic(self, extension):
        """Test configuration validation with missing pydantic library."""

        def mock_import(name, *args, **kwargs):
            if name == "pydantic":
                raise ImportError("Module not found")
            return MagicMock()

        with patch("builtins.__import__", side_effect=mock_import):
            issues = extension.validate_config()

            assert any("Pydantic library not installed" in issue for issue in issues)

    def test_validate_config_no_provider_type(self, extension):
        """Test configuration validation with no provider type."""
        extension.provider_type = ""

        issues = extension.validate_config()

        assert any(
            "E-commerce provider type not specified" in issue for issue in issues
        )

    def test_validate_config_unsupported_provider(self, extension):
        """Test configuration validation with unsupported provider."""
        extension.provider_type = "unsupported"

        issues = extension.validate_config()

        assert any(
            "Unsupported e-commerce provider: unsupported" in issue for issue in issues
        )

    def test_validate_config_no_api_key(self, extension):
        """Test configuration validation with no API key."""
        extension.api_key = ""

        issues = extension.validate_config()

        assert any("Walmart integration requires API key" in issue for issue in issues)

    def test_get_required_permissions(self, extension):
        """Test required permissions."""
        permissions = extension.get_required_permissions()
        assert isinstance(permissions, list)
        assert len(permissions) > 0

        expected_permissions = [
            "ecommerce:orders:read",
            "ecommerce:orders:write",
            "ecommerce:inventory:read",
            "ecommerce:inventory:write",
            "ecommerce:products:read",
            "ecommerce:products:write",
            "ecommerce:returns:read",
            "ecommerce:returns:write",
            "ecommerce:reports:read",
        ]
        assert set(permissions) == set(expected_permissions)

    def test_custom_configuration(self):
        """Test extension with custom configuration."""
        extension = EXT_ECommerce(
            provider_type="amazon",
            api_key="test_api_key_123",
        )

        assert extension.provider_type == "amazon"
        assert extension.api_key == "test_api_key_123"

    def test_provider_settings_passed(self, extension):
        """Test that settings are passed to provider."""
        extension.settings = {"custom_setting": "value"}
        extension.provider_type = "walmart"

        with patch(
            "zephyrex.extensions.ecommerce.PRV_Walmart.WalmartProvider"
        ) as mock_provider:
            extension._create_provider()

            # Check that settings were passed to provider
            mock_provider.assert_called_once()
            call_kwargs = mock_provider.call_args[1]
            assert "custom_setting" in call_kwargs
            assert call_kwargs["custom_setting"] == "value"

    def test_provider_with_extension_id(self, extension):
        """Test that extension ID is passed to provider."""
        extension.provider_type = "walmart"

        with patch(
            "zephyrex.extensions.ecommerce.PRV_Walmart.WalmartProvider"
        ) as mock_provider:
            extension._create_provider()

            mock_provider.assert_called_once()
            call_kwargs = mock_provider.call_args[1]
            assert call_kwargs["extension_id"] == "ecommerce"

    def test_provider_with_api_key(self, extension):
        """Test that API key is passed to provider."""
        extension.provider_type = "walmart"
        extension.api_key = "test_api_key"

        with patch(
            "zephyrex.extensions.ecommerce.PRV_Walmart.WalmartProvider"
        ) as mock_provider:
            extension._create_provider()

            mock_provider.assert_called_once()
            call_kwargs = mock_provider.call_args[1]
            assert call_kwargs["api_key"] == "test_api_key"

    @pytest.mark.asyncio
    async def test_all_abilities_with_different_platforms(self, extension):
        """Test all abilities work with different platforms."""
        for platform in [
            "walmart",
            "amazon",
            "shopify",
            "etsy",
            "ebay",
            "aliexpress",
            "woocommerce",
        ]:
            extension.provider_type = platform
            extension.provider = None

            # All abilities should return provider not available error
            result = await extension.get_orders()
            assert result["success"] is False
            assert (
                f"No e-commerce provider available for {platform}" in result["message"]
            )

            result = await extension.acknowledge_order("test_order")
            assert result["success"] is False

            result = await extension.get_inventory()
            assert result["success"] is False

            result = await extension.get_products()
            assert result["success"] is False

            result = await extension.get_returns()
            assert result["success"] is False

    @pytest.mark.asyncio
    async def test_datetime_handling_in_get_orders(self, extension):
        """Test datetime handling in get_orders method."""
        mock_provider = MagicMock()
        mock_provider.get_orders = MagicMock(return_value=[])
        extension.provider = mock_provider

        await extension.get_orders(days=30)

        # Verify that the provider was called with a start_date
        mock_provider.get_orders.assert_called_once()
        args, kwargs = mock_provider.get_orders.call_args
        assert "start_date" in kwargs
        assert isinstance(kwargs["start_date"], datetime)

    @pytest.mark.asyncio
    async def test_datetime_handling_in_get_returns(self, extension):
        """Test datetime handling in get_returns method."""
        mock_provider = MagicMock()
        mock_provider.get_returns = MagicMock(return_value=[])
        extension.provider = mock_provider

        await extension.get_returns(days=15)

        # Verify that the provider was called with a start_date
        mock_provider.get_returns.assert_called_once()
        args, kwargs = mock_provider.get_returns.call_args
        assert "start_date" in kwargs
        assert isinstance(kwargs["start_date"], datetime)

    @pytest.mark.asyncio
    async def test_datetime_handling_in_generate_report(self, extension):
        """Test datetime handling in generate_report method."""
        mock_provider = MagicMock()
        mock_provider.generate_report = MagicMock(return_value={})
        extension.provider = mock_provider

        await extension.generate_report("sales", days=7)

        # Verify that the provider was called with a start_date
        mock_provider.generate_report.assert_called_once()
        args, kwargs = mock_provider.generate_report.call_args
        assert len(args) >= 2
        assert isinstance(args[1], datetime)  # start_date should be second argument


class _WooCommerceProviderHarness(WooCommerceProvider):
    """Test-only concrete subclass.

    ``WooCommerceProvider`` — like every other ``PRV_*.py`` in this
    extension — is never actually instantiated in production; ``EXT_ECommerce``
    only ever exercises providers through a mocked class (see
    ``test_create_provider_woocommerce`` above), because the framework's
    ``AbstractStaticProvider.bond_instance`` stays abstract until a
    rotation-bonded subclass supplies it. This harness supplies a no-op
    ``bond_instance`` purely so the *real* WooCommerce REST logic (URL
    building, params, response parsing, error handling) can be exercised
    directly against a mocked ``requests`` boundary below.
    """

    @classmethod
    def bond_instance(cls, instance):
        return None


class TestWooCommerceProvider:
    """Provider-level tests for WooCommerceProvider's real ability
    implementations, mocked at the `requests` boundary. No live store is
    contacted."""

    @pytest.fixture
    def provider(self):
        instance = _WooCommerceProviderHarness()
        instance.friendly_name = "WooCommerce"
        instance.store_url = "https://example-store.test"
        instance.api_version = "wc/v3"
        instance.consumer_key = "ck_test"
        instance.consumer_secret = "cs_test"
        return instance

    @staticmethod
    def _response(status_code=200, payload=None, text=""):
        mock_response = MagicMock()
        mock_response.status_code = status_code
        mock_response.json.return_value = payload if payload is not None else []
        mock_response.text = text
        return mock_response

    def test_services(self):
        assert WooCommerceProvider.services() == ["ecommerce", "woocommerce"]

    def test_api_url_and_auth(self, provider):
        assert (
            provider._api_url("orders")
            == "https://example-store.test/wp-json/wc/v3/orders"
        )
        assert provider._auth() == ("ck_test", "cs_test")

    def test_verify_user_failure_raises(self, provider):
        with patch(
            "zephyrex.extensions.ecommerce.PRV_WooCommerce.requests.get",
            return_value=self._response(401, text="Unauthorized"),
        ):
            with pytest.raises(Exception, match="WooCommerce user verification failed"):
                provider.verify_user()

    @pytest.mark.asyncio
    async def test_get_orders_success(self, provider):
        orders_response = self._response(
            200,
            payload=[
                {
                    "id": 101,
                    "number": "101",
                    "billing": {"email": "buyer@example.com"},
                    "total": "42.00",
                    "date_created": "2026-01-01T00:00:00",
                    "status": "processing",
                    "line_items": [{"name": "Widget", "quantity": 2}],
                }
            ],
        )

        with patch(
            "zephyrex.extensions.ecommerce.PRV_WooCommerce.requests.get",
            side_effect=[self._response(200), orders_response],
        ) as mock_get:
            orders = await provider.get_orders(status="processing", limit=10)

        assert orders == [
            {
                "id": 101,
                "order_number": "101",
                "customer": {"email": "buyer@example.com"},
                "total_price": "42.00",
                "created_at": "2026-01-01T00:00:00",
                "status": "processing",
                "line_items": [{"name": "Widget", "quantity": 2}],
            }
        ]
        assert mock_get.call_count == 2
        orders_call = mock_get.call_args_list[1]
        assert orders_call.kwargs["params"]["status"] == "processing"
        assert orders_call.kwargs["params"]["per_page"] == 10
        assert orders_call.kwargs["auth"] == ("ck_test", "cs_test")

    @pytest.mark.asyncio
    async def test_get_orders_error_returns_empty_list(self, provider):
        with patch(
            "zephyrex.extensions.ecommerce.PRV_WooCommerce.requests.get",
            return_value=self._response(401, text="Unauthorized"),
        ):
            orders = await provider.get_orders()

        assert orders == []

    @pytest.mark.asyncio
    async def test_acknowledge_order_success(self, provider):
        with patch(
            "zephyrex.extensions.ecommerce.PRV_WooCommerce.requests.get",
            return_value=self._response(200),
        ), patch(
            "zephyrex.extensions.ecommerce.PRV_WooCommerce.requests.put",
            return_value=self._response(200),
        ) as mock_put:
            result = await provider.acknowledge_order("101")

        assert result == "Order acknowledged successfully."
        assert mock_put.call_args.kwargs["json"] == {"status": "processing"}
        assert (
            mock_put.call_args.args[0]
            == "https://example-store.test/wp-json/wc/v3/orders/101"
        )

    @pytest.mark.asyncio
    async def test_acknowledge_order_error(self, provider):
        with patch(
            "zephyrex.extensions.ecommerce.PRV_WooCommerce.requests.get",
            return_value=self._response(200),
        ), patch(
            "zephyrex.extensions.ecommerce.PRV_WooCommerce.requests.put",
            return_value=self._response(500, text="Server error"),
        ):
            result = await provider.acknowledge_order("101")

        assert result.startswith("Failed to acknowledge order:")

    @pytest.mark.asyncio
    async def test_cancel_order_success(self, provider):
        with patch(
            "zephyrex.extensions.ecommerce.PRV_WooCommerce.requests.get",
            return_value=self._response(200),
        ), patch(
            "zephyrex.extensions.ecommerce.PRV_WooCommerce.requests.put",
            return_value=self._response(200),
        ) as mock_put:
            result = await provider.cancel_order("101", "Customer changed their mind")

        assert result == "Order cancelled successfully."
        assert mock_put.call_args.kwargs["json"] == {
            "status": "cancelled",
            "customer_note": "Customer changed their mind",
        }

    @pytest.mark.asyncio
    async def test_cancel_order_error(self, provider):
        with patch(
            "zephyrex.extensions.ecommerce.PRV_WooCommerce.requests.get",
            return_value=self._response(200),
        ), patch(
            "zephyrex.extensions.ecommerce.PRV_WooCommerce.requests.put",
            return_value=self._response(404, text="Not found"),
        ):
            result = await provider.cancel_order("999", "no such order")

        assert result.startswith("Failed to cancel order:")

    @pytest.mark.asyncio
    async def test_get_inventory_success(self, provider):
        products_response = self._response(
            200,
            payload=[
                {
                    "id": 5,
                    "sku": "SKU-1",
                    "stock_quantity": 12,
                    "stock_status": "instock",
                    "date_modified": "2026-01-02T00:00:00",
                }
            ],
        )

        with patch(
            "zephyrex.extensions.ecommerce.PRV_WooCommerce.requests.get",
            side_effect=[self._response(200), products_response],
        ):
            inventory = await provider.get_inventory()

        assert inventory == [
            {
                "product_id": 5,
                "sku": "SKU-1",
                "available": 12,
                "stock_status": "instock",
                "updated_at": "2026-01-02T00:00:00",
            }
        ]

    @pytest.mark.asyncio
    async def test_get_inventory_with_sku_list(self, provider):
        sku_response = self._response(
            200,
            payload=[
                {
                    "id": 11,
                    "sku": "SKU-11",
                    "stock_quantity": 3,
                    "stock_status": "instock",
                    "date_modified": "2026-01-04T00:00:00",
                }
            ],
        )

        with patch(
            "zephyrex.extensions.ecommerce.PRV_WooCommerce.requests.get",
            side_effect=[self._response(200), sku_response],
        ) as mock_get:
            inventory = await provider.get_inventory(sku_list=["SKU-11"])

        assert inventory == [
            {
                "product_id": 11,
                "sku": "SKU-11",
                "available": 3,
                "stock_status": "instock",
                "updated_at": "2026-01-04T00:00:00",
            }
        ]
        assert mock_get.call_args_list[1].kwargs["params"] == {"sku": "SKU-11"}

    @pytest.mark.asyncio
    async def test_get_inventory_error_returns_empty_list(self, provider):
        with patch(
            "zephyrex.extensions.ecommerce.PRV_WooCommerce.requests.get",
            side_effect=[self._response(200), self._response(401, text="Unauthorized")],
        ):
            inventory = await provider.get_inventory()

        assert inventory == []

    @pytest.mark.asyncio
    async def test_update_inventory_success(self, provider):
        lookup_response = self._response(200, payload=[{"id": 7, "sku": "SKU-2"}])

        with patch(
            "zephyrex.extensions.ecommerce.PRV_WooCommerce.requests.get",
            side_effect=[self._response(200), lookup_response],
        ), patch(
            "zephyrex.extensions.ecommerce.PRV_WooCommerce.requests.put",
            return_value=self._response(200),
        ) as mock_put:
            result = await provider.update_inventory("SKU-2", 30)

        assert result == "Inventory updated successfully."
        assert (
            mock_put.call_args.args[0]
            == "https://example-store.test/wp-json/wc/v3/products/7"
        )
        assert mock_put.call_args.kwargs["json"] == {
            "manage_stock": True,
            "stock_quantity": 30,
        }

    @pytest.mark.asyncio
    async def test_update_inventory_sku_not_found(self, provider):
        with patch(
            "zephyrex.extensions.ecommerce.PRV_WooCommerce.requests.get",
            side_effect=[self._response(200), self._response(200, payload=[])],
        ):
            result = await provider.update_inventory("MISSING-SKU", 5)

        assert result.startswith("Failed to update inventory:")

    @pytest.mark.asyncio
    async def test_get_products_success(self, provider):
        products_response = self._response(
            200,
            payload=[
                {
                    "id": 9,
                    "name": "Widget",
                    "sku": "SKU-9",
                    "price": "19.99",
                    "status": "publish",
                    "type": "simple",
                    "date_created": "2026-01-01T00:00:00",
                    "date_modified": "2026-01-03T00:00:00",
                }
            ],
        )

        with patch(
            "zephyrex.extensions.ecommerce.PRV_WooCommerce.requests.get",
            side_effect=[self._response(200), products_response],
        ) as mock_get:
            products = await provider.get_products(limit=25, offset=25)

        assert products == [
            {
                "id": 9,
                "title": "Widget",
                "sku": "SKU-9",
                "price": "19.99",
                "status": "publish",
                "type": "simple",
                "created_at": "2026-01-01T00:00:00",
                "updated_at": "2026-01-03T00:00:00",
            }
        ]
        assert mock_get.call_args_list[1].kwargs["params"] == {
            "per_page": 25,
            "page": 2,
        }

    @pytest.mark.asyncio
    async def test_get_products_error_returns_empty_list(self, provider):
        with patch(
            "zephyrex.extensions.ecommerce.PRV_WooCommerce.requests.get",
            side_effect=[self._response(200), self._response(500, text="boom")],
        ):
            products = await provider.get_products()

        assert products == []

    @pytest.mark.asyncio
    async def test_update_product_success(self, provider):
        lookup_response = self._response(200, payload=[{"id": 3, "sku": "SKU-3"}])

        with patch(
            "zephyrex.extensions.ecommerce.PRV_WooCommerce.requests.get",
            side_effect=[self._response(200), lookup_response],
        ), patch(
            "zephyrex.extensions.ecommerce.PRV_WooCommerce.requests.put",
            return_value=self._response(200),
        ) as mock_put:
            result = await provider.update_product("SKU-3", {"regular_price": "24.99"})

        assert result == "Product updated successfully."
        assert mock_put.call_args.kwargs["json"] == {"regular_price": "24.99"}

    @pytest.mark.asyncio
    async def test_update_product_error(self, provider):
        lookup_response = self._response(200, payload=[{"id": 3, "sku": "SKU-3"}])

        with patch(
            "zephyrex.extensions.ecommerce.PRV_WooCommerce.requests.get",
            side_effect=[self._response(200), lookup_response],
        ), patch(
            "zephyrex.extensions.ecommerce.PRV_WooCommerce.requests.put",
            return_value=self._response(422, text="Invalid price"),
        ):
            result = await provider.update_product("SKU-3", {"regular_price": "-1"})

        assert result.startswith("Failed to update product:")

    @pytest.mark.asyncio
    async def test_get_returns_success(self, provider):
        returns_response = self._response(
            200,
            payload=[
                {
                    "id": 55,
                    "status": "refunded",
                    "total": "10.00",
                    "date_created": "2026-01-05T00:00:00",
                }
            ],
        )

        with patch(
            "zephyrex.extensions.ecommerce.PRV_WooCommerce.requests.get",
            side_effect=[self._response(200), returns_response],
        ) as mock_get:
            returns = await provider.get_returns(limit=5)

        assert returns == [
            {
                "id": 55,
                "order_id": 55,
                "status": "refunded",
                "total": "10.00",
                "created_at": "2026-01-05T00:00:00",
            }
        ]
        assert mock_get.call_args_list[1].kwargs["params"]["status"] == "refunded"

    @pytest.mark.asyncio
    async def test_get_returns_error_returns_empty_list(self, provider):
        with patch(
            "zephyrex.extensions.ecommerce.PRV_WooCommerce.requests.get",
            side_effect=[self._response(200), self._response(503, text="down")],
        ):
            returns = await provider.get_returns()

        assert returns == []

    @pytest.mark.asyncio
    async def test_process_return_success(self, provider):
        with patch(
            "zephyrex.extensions.ecommerce.PRV_WooCommerce.requests.get",
            return_value=self._response(200),
        ), patch(
            "zephyrex.extensions.ecommerce.PRV_WooCommerce.requests.post",
            return_value=self._response(201),
        ) as mock_post:
            result = await provider.process_return("101", "Refund", refund_amount=15.5)

        assert result == "Return refunded successfully."
        assert mock_post.call_args.kwargs["json"] == {
            "reason": "Return processed with action: Refund",
            "amount": "15.5",
        }

    @pytest.mark.asyncio
    async def test_process_return_error(self, provider):
        with patch(
            "zephyrex.extensions.ecommerce.PRV_WooCommerce.requests.get",
            return_value=self._response(200),
        ), patch(
            "zephyrex.extensions.ecommerce.PRV_WooCommerce.requests.post",
            return_value=self._response(400, text="Refund exceeds order total"),
        ):
            result = await provider.process_return("101", "Refund", refund_amount=999)

        assert result.startswith("Failed to process return:")

    @pytest.mark.asyncio
    async def test_generate_report_success(self, provider):
        report_response = self._response(200, payload={"total_sales": "500.00"})

        with patch(
            "zephyrex.extensions.ecommerce.PRV_WooCommerce.requests.get",
            side_effect=[self._response(200), report_response],
        ) as mock_get:
            result = await provider.generate_report("sales")

        assert result == "Report generated successfully."
        assert (
            mock_get.call_args_list[1].args[0]
            == "https://example-store.test/wp-json/wc/v3/reports/sales"
        )

    @pytest.mark.asyncio
    async def test_generate_report_error(self, provider):
        with patch(
            "zephyrex.extensions.ecommerce.PRV_WooCommerce.requests.get",
            side_effect=[self._response(200), self._response(500, text="boom")],
        ):
            result = await provider.generate_report("sales")

        assert result.startswith("Failed to generate report:")


if __name__ == "__main__":
    pytest.main([__file__])
