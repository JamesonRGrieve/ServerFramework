from abc import abstractmethod
from typing import Dict, List

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticProvider


class AbstractEcommerceProvider(AbstractStaticProvider):
    """
    Abstract provider for e-commerce platforms.
    Defines common methods that all e-commerce platforms should implement.
    """

    @staticmethod
    @abstractmethod
    def services() -> List[str]:
        """Return list of services provided by this provider."""
        return ["ecommerce"]

    @abstractmethod
    async def get_orders(self, **kwargs) -> List[Dict]:
        """Get orders from the platform."""
        pass

    @abstractmethod
    async def acknowledge_order(self, order_id: str) -> str:
        """Acknowledge an order on the platform."""
        pass

    @abstractmethod
    async def cancel_order(self, order_id: str, reason: str) -> str:
        """Cancel an order on the platform."""
        pass

    @abstractmethod
    async def get_inventory(self, **kwargs) -> List[Dict]:
        """Get inventory from the platform."""
        pass

    @abstractmethod
    async def update_inventory(self, sku: str, quantity: int) -> str:
        """Update inventory for a specific SKU."""
        pass

    @abstractmethod
    async def get_products(self, **kwargs) -> List[Dict]:
        """Get products from the platform."""
        pass

    @abstractmethod
    async def update_product(self, sku: str, updates: Dict) -> str:
        """Update product information."""
        pass

    @abstractmethod
    async def get_returns(self, **kwargs) -> List[Dict]:
        """Get returns from the platform."""
        pass

    @abstractmethod
    async def process_return(self, return_id: str, **kwargs) -> str:
        """Process a return request."""
        pass

    @abstractmethod
    async def generate_report(self, report_type: str, **kwargs) -> str:
        """Generate a report on the platform."""
        pass
