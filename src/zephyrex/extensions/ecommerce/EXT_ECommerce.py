from typing import Any, Dict, List, Optional, Set

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
    ability,
)
from zephyrex.lib.Dependencies import EXT_Dependency, PIP_Dependency
from zephyrex.lib.Logging import logger


class EXT_ECommerce(AbstractStaticExtension):
    """
    E-commerce extension for AGInfrastructure.

    Provides integration with various e-commerce platforms:
    - Walmart Marketplace
    - Amazon Marketplace
    - Aliexpress
    - Shopify
    - Etsy
    - eBay
    - WooCommerce

    Enables management of orders, inventory, products, returns and reporting.

    Component loading (DB, BLL, EP) is handled automatically by the import system
    based on file naming conventions.
    """

    # Extension metadata
    name = "ecommerce"
    version = "1.0.0"
    description = "E-commerce marketplace integration for various platforms including Walmart, Amazon, Shopify, etc."

    # Define dependencies
    ext_dependencies = [
        EXT_Dependency(
            name="core",
            friendly_name="Core Extension",
            optional=False,
            reason="Required for base e-commerce functionality",
        ),
        EXT_Dependency(
            name="auth",
            friendly_name="Authentication Extension",
            optional=False,
            reason="Required for API authentication with e-commerce platforms",
        ),
    ]

    pip_dependencies = [
        PIP_Dependency(
            name="requests",
            friendly_name="HTTP Requests Library",
            optional=False,
            reason="Required for API communications with e-commerce platforms",
            semver=">=2.28.0",
        ),
        PIP_Dependency(
            name="pydantic",
            friendly_name="Pydantic Data Validation",
            optional=False,
            reason="Required for e-commerce data validation",
            semver=">=2.0.0",
        ),
    ]

    sys_dependencies = []

    # Define database tables (none for this extension)
    db_tables = []

    # Define what capabilities this extension provides
    capabilities = [
        "order_management",
        "inventory_management",
        "product_management",
        "returns_management",
        "reporting",
        "marketplace_integration",
    ]

    def __init__(self, provider_type: str = "walmart", api_key: str = "", **kwargs):
        super().__init__(**kwargs)

        self.provider_type = provider_type.lower()
        self.api_key = api_key
        self.provider = None
        self.commands = {}

    def on_initialize(self) -> bool:
        """Initialize the e-commerce extension with the appropriate provider."""
        logger.debug("Initializing E-commerce Extension...")

        try:
            self._create_provider()
            self._register_commands()

            # Register capabilities
            for capability in self.capabilities:
                self.register_capability(capability)

            logger.debug("E-commerce extension initialized successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize E-commerce extension: {str(e)}")
            return False

    def _create_provider(self):
        """Create the appropriate e-commerce provider based on provider_type."""
        try:
            provider_mapping = {
                "walmart": (
                    "zephyrex.extensions.ecommerce.PRV_Walmart",
                    "WalmartProvider",
                ),
                "amazon": (
                    "zephyrex.extensions.ecommerce.PRV_Amazon",
                    "AmazonProvider",
                ),
                "aliexpress": (
                    "zephyrex.extensions.ecommerce.PRV_Aliexpress",
                    "AliexpressProvider",
                ),
                "shopify": (
                    "zephyrex.extensions.ecommerce.PRV_Shopify",
                    "ShopifyProvider",
                ),
                "etsy": ("zephyrex.extensions.ecommerce.PRV_Etsy", "EtsyProvider"),
                "ebay": ("zephyrex.extensions.ecommerce.PRV_Ebay", "EbayProvider"),
                "woocommerce": (
                    "zephyrex.extensions.ecommerce.PRV_WooCommerce",
                    "WooCommerceProvider",
                ),
            }

            if self.provider_type not in provider_mapping:
                logger.error(f"Unsupported e-commerce provider: {self.provider_type}")
                self.provider = None
                return

            module_name, class_name = provider_mapping[self.provider_type]

            try:
                module = __import__(module_name, fromlist=[class_name])
                provider_class = getattr(module, class_name)

                self.provider = provider_class(
                    api_key=self.api_key,
                    extension_id=self.name,
                    **getattr(self, "settings", {}),
                )

                logger.debug(
                    f"E-commerce provider for {self.provider_type} created successfully"
                )

            except ImportError as e:
                logger.warning(
                    f"Could not import e-commerce provider for {self.provider_type}: {e}"
                )
                self.provider = None
            except Exception as e:
                logger.error(f"Error creating e-commerce provider: {str(e)}")
                self.provider = None

        except Exception as e:
            logger.error(f"Error in provider creation: {str(e)}")
            self.provider = None

    def _register_commands(self):
        """Register commands based on available provider."""
        if self.provider and hasattr(self.provider, "has_capability"):
            # Set up available commands from the provider capabilities
            if self.provider.has_capability("order_management"):
                self.commands.update(
                    {
                        "Get Orders": self.get_orders,
                        "Acknowledge Order": self.acknowledge_order,
                        "Cancel Order": self.cancel_order,
                    }
                )

            if self.provider.has_capability("inventory_management"):
                self.commands.update(
                    {
                        "Get Inventory": self.get_inventory,
                        "Update Inventory": self.update_inventory,
                    }
                )

            if self.provider.has_capability("product_management"):
                self.commands.update(
                    {
                        "Get Products": self.get_products,
                        "Update Product": self.update_product,
                    }
                )

            if self.provider.has_capability("returns_management"):
                self.commands.update(
                    {
                        "Get Returns": self.get_returns,
                        "Process Return": self.process_return,
                    }
                )

            if self.provider.has_capability("reporting"):
                self.commands.update({"Generate Report": self.generate_report})
        else:
            # Provide placeholder commands that warn about missing provider
            platform_name = self.provider_type.upper()
            self.commands = {
                f"Get {platform_name} Orders": self._no_provider_warning,
                f"Manage {platform_name} Inventory": self._no_provider_warning,
                f"Manage {platform_name} Products": self._no_provider_warning,
            }

    async def _no_provider_warning(self, *args, **kwargs) -> str:
        """Warning message when a provider is not available."""
        return f"No e-commerce provider available for {self.provider_type}. Please check your configuration."

    def register_capability(self, capability: str):
        """Register a new capability."""
        if capability not in self.capabilities:
            self.capabilities.append(capability)

    def get_registered_capabilities(self) -> Set[str]:
        """Return currently registered capabilities."""
        return set(self.capabilities)

    def get_capabilities(self) -> Set[str]:
        """Return the capabilities this extension provides."""
        return set(self.capabilities)

    @ability("get_orders")
    async def get_orders(
        self, status: str = "", days: int = 30, limit: int = 50
    ) -> Dict[str, Any]:
        """
        Get orders from the e-commerce platform.
        """
        try:
            if not self.provider:
                return {"success": False, "message": await self._no_provider_warning()}

            from datetime import datetime, timedelta

            start_date = datetime.now() - timedelta(days=days)
            orders = self.provider.get_orders(
                status=status, start_date=start_date, limit=limit
            )

            if not orders:
                return {"success": True, "orders": [], "message": "No orders found"}

            return {"success": True, "orders": orders, "count": len(orders)}

        except Exception as e:
            logger.error(f"Error retrieving orders: {e}")
            return {"success": False, "message": f"Error retrieving orders: {str(e)}"}

    @ability("acknowledge_order")
    async def acknowledge_order(self, order_id: str) -> Dict[str, Any]:
        """
        Acknowledge an order on the e-commerce platform.
        """
        try:
            if not self.provider:
                return {"success": False, "message": await self._no_provider_warning()}

            result = self.provider.acknowledge_order(order_id)
            return {"success": True, "result": result, "order_id": order_id}

        except Exception as e:
            logger.error(f"Error acknowledging order: {e}")
            return {"success": False, "message": f"Error acknowledging order: {str(e)}"}

    @ability("cancel_order")
    async def cancel_order(self, order_id: str, reason: str) -> Dict[str, Any]:
        """
        Cancel an order on the e-commerce platform.
        """
        try:
            if not self.provider:
                return {"success": False, "message": await self._no_provider_warning()}

            result = self.provider.cancel_order(order_id, reason)
            return {
                "success": True,
                "result": result,
                "order_id": order_id,
                "reason": reason,
            }

        except Exception as e:
            logger.error(f"Error cancelling order: {e}")
            return {"success": False, "message": f"Error cancelling order: {str(e)}"}

    @ability("get_inventory")
    async def get_inventory(self, sku: str = "") -> Dict[str, Any]:
        """
        Get inventory from the e-commerce platform.
        """
        try:
            if not self.provider:
                return {"success": False, "message": await self._no_provider_warning()}

            sku_list = [sku] if sku else None
            inventory = self.provider.get_inventory(sku_list=sku_list)

            if not inventory:
                return {
                    "success": True,
                    "inventory": [],
                    "message": "No inventory found",
                }

            return {"success": True, "inventory": inventory, "count": len(inventory)}

        except Exception as e:
            logger.error(f"Error retrieving inventory: {e}")
            return {
                "success": False,
                "message": f"Error retrieving inventory: {str(e)}",
            }

    @ability("update_inventory")
    async def update_inventory(self, sku: str, quantity: int) -> Dict[str, Any]:
        """
        Update inventory for a specific SKU.
        """
        try:
            if not self.provider:
                return {"success": False, "message": await self._no_provider_warning()}

            result = self.provider.update_inventory(sku, quantity)
            return {"success": True, "result": result, "sku": sku, "quantity": quantity}

        except Exception as e:
            logger.error(f"Error updating inventory: {e}")
            return {"success": False, "message": f"Error updating inventory: {str(e)}"}

    @ability("get_products")
    async def get_products(self, limit: int = 50) -> Dict[str, Any]:
        """
        Get products from the e-commerce platform.
        """
        try:
            if not self.provider:
                return {"success": False, "message": await self._no_provider_warning()}

            products = self.provider.get_products(limit=limit)

            if not products:
                return {"success": True, "products": [], "message": "No products found"}

            return {"success": True, "products": products, "count": len(products)}

        except Exception as e:
            logger.error(f"Error retrieving products: {e}")
            return {"success": False, "message": f"Error retrieving products: {str(e)}"}

    @ability("update_product")
    async def update_product(self, sku: str, field: str, value: str) -> Dict[str, Any]:
        """
        Update a product field.
        """
        try:
            if not self.provider:
                return {"success": False, "message": await self._no_provider_warning()}

            result = self.provider.update_product(sku, field, value)
            return {
                "success": True,
                "result": result,
                "sku": sku,
                "field": field,
                "value": value,
            }

        except Exception as e:
            logger.error(f"Error updating product: {e}")
            return {"success": False, "message": f"Error updating product: {str(e)}"}

    @ability("get_returns")
    async def get_returns(self, days: int = 30, limit: int = 50) -> Dict[str, Any]:
        """
        Get returns from the e-commerce platform.
        """
        try:
            if not self.provider:
                return {"success": False, "message": await self._no_provider_warning()}

            from datetime import datetime, timedelta

            start_date = datetime.now() - timedelta(days=days)
            returns = self.provider.get_returns(start_date=start_date, limit=limit)

            if not returns:
                return {"success": True, "returns": [], "message": "No returns found"}

            return {"success": True, "returns": returns, "count": len(returns)}

        except Exception as e:
            logger.error(f"Error retrieving returns: {e}")
            return {"success": False, "message": f"Error retrieving returns: {str(e)}"}

    @ability("process_return")
    async def process_return(
        self, return_id: str, action: str, refund_amount: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Process a return with the specified action.
        """
        try:
            if not self.provider:
                return {"success": False, "message": await self._no_provider_warning()}

            result = self.provider.process_return(return_id, action, refund_amount)
            return {
                "success": True,
                "result": result,
                "return_id": return_id,
                "action": action,
                "refund_amount": refund_amount,
            }

        except Exception as e:
            logger.error(f"Error processing return: {e}")
            return {"success": False, "message": f"Error processing return: {str(e)}"}

    @ability("generate_report")
    async def generate_report(self, report_type: str, days: int = 30) -> Dict[str, Any]:
        """
        Generate a report for the specified type and time period.
        """
        try:
            if not self.provider:
                return {"success": False, "message": await self._no_provider_warning()}

            from datetime import datetime, timedelta

            start_date = datetime.now() - timedelta(days=days)
            report = self.provider.generate_report(report_type, start_date)

            return {
                "success": True,
                "report": report,
                "report_type": report_type,
                "days": days,
            }

        except Exception as e:
            logger.error(f"Error generating report: {e}")
            return {"success": False, "message": f"Error generating report: {str(e)}"}

    def on_start(self) -> bool:
        """Start the E-commerce extension."""
        try:
            logger.debug("E-commerce extension started successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to start E-commerce extension: {e}")
            return False

    def on_stop(self) -> bool:
        """Stop the E-commerce extension."""
        try:
            if self.provider:
                # Clean up provider resources if needed
                self.provider = None

            logger.debug("E-commerce extension stopped successfully")
            return True
        except Exception as e:
            logger.error(f"Error stopping E-commerce extension: {e}")
            return False

    def validate_config(self) -> List[str]:
        """Validate the extension configuration."""
        issues = []

        # Check for required Python packages
        try:
            import requests
        except ImportError:
            issues.append(
                "Requests library not installed - API communications will not work"
            )

        try:
            import pydantic
        except ImportError:
            issues.append(
                "Pydantic library not installed - data validation will not work"
            )

        # Platform-specific validation
        if not self.provider_type:
            issues.append("E-commerce provider type not specified")
        elif self.provider_type not in [
            "walmart",
            "amazon",
            "aliexpress",
            "shopify",
            "etsy",
            "ebay",
            "woocommerce",
        ]:
            issues.append(f"Unsupported e-commerce provider: {self.provider_type}")

        # Check required credentials
        if not self.api_key:
            issues.append(f"{self.provider_type.title()} integration requires API key")

        return issues

    def get_required_permissions(self) -> List[str]:
        """Return the list of permissions required by this extension."""
        return [
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

    def on_startup(self):
        """Called during application startup."""
        logger.debug("E-commerce extension startup hook called")

    def on_shutdown(self):
        """Called during application shutdown."""
        logger.debug("E-commerce extension shutdown hook called")

    def has_capability(self, capability: str) -> bool:
        """Check if this extension has a specific capability."""
        return capability in self.capabilities
