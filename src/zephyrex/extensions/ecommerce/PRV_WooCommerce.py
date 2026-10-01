import logging
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import requests

from .PRV_ECommerce import AbstractEcommerceProvider

# The official ``woocommerce`` SDK is optional — this provider talks to the
# WooCommerce REST API (``/wp-json/wc/v3/...``) directly via ``requests`` so
# it works with zero extra dependencies. The guarded import only records
# whether the SDK happens to be installed (surfaced via ``sdk_available``);
# it is never used to make calls.
try:
    import woocommerce as woocommerce_sdk
except ImportError:
    woocommerce_sdk = None  # type: ignore[assignment]


class WooCommerceProvider(AbstractEcommerceProvider):
    """
    WooCommerce provider implementation.
    Handles WooCommerce REST API (``/wp-json/wc/v3``) interactions for
    WordPress-based store management, authenticated via consumer key/secret.
    """

    def _configure_provider(self, **kwargs) -> None:
        self.friendly_name = "WooCommerce"
        self.store_url = kwargs.get("WOOCOMMERCE_STORE_URL", "").rstrip("/")
        self.api_version = kwargs.get("WOOCOMMERCE_API_VERSION", "wc/v3")
        self.consumer_key = kwargs.get(
            "WOOCOMMERCE_CONSUMER_KEY", os.getenv("WOOCOMMERCE_CONSUMER_KEY", "")
        )
        self.consumer_secret = os.getenv("WOOCOMMERCE_CONSUMER_SECRET", "")
        self.sdk_available = woocommerce_sdk is not None

        self.register_capability("order_management")
        self.register_capability("inventory_management")
        self.register_capability("product_management")
        self.register_capability("returns_management")
        self.register_capability("reporting")

    @staticmethod
    def services() -> List[str]:
        return ["ecommerce", "woocommerce"]

    def _auth(self):
        return (self.consumer_key, self.consumer_secret)

    def _api_url(self, path: str) -> str:
        return f"{self.store_url}/wp-json/{self.api_version}/{path.lstrip('/')}"

    def verify_user(self):
        """
        Verify the user's authentication with WooCommerce.
        If verification fails, raises an exception.
        """
        logging.info(f"Verifying user with WooCommerce store: {self.store_url}")

        try:
            response = requests.get(self._api_url("system_status"), auth=self._auth())

            if response.status_code != 200:
                raise Exception(
                    f"WooCommerce user verification failed: {response.text}"
                )
        except Exception as e:
            logging.error(f"Error verifying WooCommerce user: {str(e)}")
            raise Exception(f"WooCommerce user verification failed: {str(e)}")

    async def get_orders(
        self,
        status: str = "any",
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 50,
    ) -> List[Dict]:
        try:
            self.verify_user()

            if not start_date:
                start_date = datetime.now() - timedelta(days=30)
            if not end_date:
                end_date = datetime.now()

            params = {
                "per_page": limit,
                "after": start_date.isoformat(),
                "before": end_date.isoformat(),
            }
            if status and status != "any":
                params["status"] = status

            response = requests.get(
                self._api_url("orders"), params=params, auth=self._auth()
            )

            if response.status_code != 200:
                raise Exception(f"Failed to fetch WooCommerce orders: {response.text}")

            orders = []
            for order in response.json():
                orders.append(
                    {
                        "id": order["id"],
                        "order_number": order.get("number", order["id"]),
                        "customer": order.get("billing", {}),
                        "total_price": order["total"],
                        "created_at": order["date_created"],
                        "status": order["status"],
                        "line_items": order.get("line_items", []),
                    }
                )

            return orders

        except Exception as e:
            logging.error(f"Error retrieving WooCommerce orders: {str(e)}")
            return []

    async def acknowledge_order(self, order_id: str) -> str:
        try:
            self.verify_user()

            response = requests.put(
                self._api_url(f"orders/{order_id}"),
                auth=self._auth(),
                json={"status": "processing"},
            )

            if response.status_code != 200:
                raise Exception(
                    f"Failed to acknowledge WooCommerce order: {response.text}"
                )

            return "Order acknowledged successfully."

        except Exception as e:
            logging.error(f"Error acknowledging WooCommerce order: {str(e)}")
            return f"Failed to acknowledge order: {str(e)}"

    async def cancel_order(self, order_id: str, reason: str) -> str:
        try:
            self.verify_user()

            response = requests.put(
                self._api_url(f"orders/{order_id}"),
                auth=self._auth(),
                json={"status": "cancelled", "customer_note": reason},
            )

            if response.status_code != 200:
                raise Exception(f"Failed to cancel WooCommerce order: {response.text}")

            return "Order cancelled successfully."

        except Exception as e:
            logging.error(f"Error cancelling WooCommerce order: {str(e)}")
            return f"Failed to cancel order: {str(e)}"

    async def get_inventory(self, sku_list: Optional[List[str]] = None) -> List[Dict]:
        try:
            self.verify_user()

            inventory = []

            if sku_list:
                for sku in sku_list:
                    response = requests.get(
                        self._api_url("products"),
                        params={"sku": sku},
                        auth=self._auth(),
                    )

                    if response.status_code != 200:
                        raise Exception(
                            f"Failed to fetch WooCommerce inventory for SKU {sku}: {response.text}"
                        )

                    for product in response.json():
                        inventory.append(self._inventory_entry(product))
            else:
                response = requests.get(self._api_url("products"), auth=self._auth())

                if response.status_code != 200:
                    raise Exception(
                        f"Failed to fetch WooCommerce inventory: {response.text}"
                    )

                for product in response.json():
                    inventory.append(self._inventory_entry(product))

            return inventory

        except Exception as e:
            logging.error(f"Error retrieving WooCommerce inventory: {str(e)}")
            return []

    @staticmethod
    def _inventory_entry(product: Dict) -> Dict:
        return {
            "product_id": product["id"],
            "sku": product.get("sku", ""),
            "available": product.get("stock_quantity"),
            "stock_status": product.get("stock_status"),
            "updated_at": product.get("date_modified"),
        }

    async def update_inventory(self, sku: str, quantity: int) -> str:
        try:
            self.verify_user()

            lookup = requests.get(
                self._api_url("products"), params={"sku": sku}, auth=self._auth()
            )

            if lookup.status_code != 200:
                raise Exception(
                    f"Failed to look up WooCommerce SKU {sku}: {lookup.text}"
                )

            products = lookup.json()
            if not products:
                raise Exception(f"No WooCommerce product found for SKU {sku}")

            product_id = products[0]["id"]

            response = requests.put(
                self._api_url(f"products/{product_id}"),
                auth=self._auth(),
                json={"manage_stock": True, "stock_quantity": quantity},
            )

            if response.status_code != 200:
                raise Exception(
                    f"Failed to update WooCommerce inventory: {response.text}"
                )

            return "Inventory updated successfully."

        except Exception as e:
            logging.error(f"Error updating WooCommerce inventory: {str(e)}")
            return f"Failed to update inventory: {str(e)}"

    async def get_products(self, limit: int = 50, offset: int = 0) -> List[Dict]:
        try:
            self.verify_user()

            params = {"per_page": limit, "page": (offset // limit) + 1}

            response = requests.get(
                self._api_url("products"), params=params, auth=self._auth()
            )

            if response.status_code != 200:
                raise Exception(
                    f"Failed to fetch WooCommerce products: {response.text}"
                )

            products = []
            for product in response.json():
                products.append(
                    {
                        "id": product["id"],
                        "title": product["name"],
                        "sku": product.get("sku", ""),
                        "price": product.get("price", ""),
                        "status": product.get("status"),
                        "type": product.get("type"),
                        "created_at": product.get("date_created"),
                        "updated_at": product.get("date_modified"),
                    }
                )

            return products

        except Exception as e:
            logging.error(f"Error retrieving WooCommerce products: {str(e)}")
            return []

    async def update_product(self, sku: str, updates: Dict) -> str:
        try:
            self.verify_user()

            lookup = requests.get(
                self._api_url("products"), params={"sku": sku}, auth=self._auth()
            )

            if lookup.status_code != 200:
                raise Exception(
                    f"Failed to look up WooCommerce SKU {sku}: {lookup.text}"
                )

            products = lookup.json()
            if not products:
                raise Exception(f"No WooCommerce product found for SKU {sku}")

            product_id = products[0]["id"]

            response = requests.put(
                self._api_url(f"products/{product_id}"),
                auth=self._auth(),
                json=updates,
            )

            if response.status_code != 200:
                raise Exception(
                    f"Failed to update WooCommerce product: {response.text}"
                )

            return "Product updated successfully."

        except Exception as e:
            logging.error(f"Error updating WooCommerce product: {str(e)}")
            return f"Failed to update product: {str(e)}"

    async def get_returns(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 50,
    ) -> List[Dict]:
        try:
            self.verify_user()

            # WooCommerce core has no dedicated returns endpoint; refunded
            # orders are the closest analog (mirrors how Shopify treats
            # returns as order refunds).
            if not start_date:
                start_date = datetime.now() - timedelta(days=30)
            if not end_date:
                end_date = datetime.now()

            params = {
                "status": "refunded",
                "per_page": limit,
                "after": start_date.isoformat(),
                "before": end_date.isoformat(),
            }

            response = requests.get(
                self._api_url("orders"), params=params, auth=self._auth()
            )

            if response.status_code != 200:
                raise Exception(f"Failed to fetch WooCommerce returns: {response.text}")

            returns = []
            for order in response.json():
                returns.append(
                    {
                        "id": order["id"],
                        "order_id": order["id"],
                        "status": order["status"],
                        "total": order.get("total"),
                        "created_at": order.get("date_created"),
                    }
                )

            return returns

        except Exception as e:
            logging.error(f"Error retrieving WooCommerce returns: {str(e)}")
            return []

    async def process_return(
        self, return_id: str, action: str, refund_amount: Optional[float] = None
    ) -> str:
        try:
            self.verify_user()

            # In WooCommerce, returns are processed as refunds on orders.
            data: Dict[str, str] = {"reason": f"Return processed with action: {action}"}
            if refund_amount is not None:
                data["amount"] = str(refund_amount)

            response = requests.post(
                self._api_url(f"orders/{return_id}/refunds"),
                auth=self._auth(),
                json=data,
            )

            if response.status_code not in (200, 201):
                raise Exception(
                    f"Failed to process WooCommerce return: {response.text}"
                )

            return f"Return {action.lower()}ed successfully."

        except Exception as e:
            logging.error(f"Error processing WooCommerce return: {str(e)}")
            return f"Failed to process return: {str(e)}"

    async def generate_report(
        self,
        report_type: str,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> str:
        try:
            self.verify_user()

            if not start_date:
                start_date = datetime.now() - timedelta(days=30)
            if not end_date:
                end_date = datetime.now()

            params = {
                "date_min": start_date.date().isoformat(),
                "date_max": end_date.date().isoformat(),
            }

            response = requests.get(
                self._api_url(f"reports/{report_type}"),
                params=params,
                auth=self._auth(),
            )

            if response.status_code != 200:
                raise Exception(
                    f"Failed to generate WooCommerce report: {response.text}"
                )

            return "Report generated successfully."

        except Exception as e:
            logging.error(f"Error generating WooCommerce report: {str(e)}")
            return f"Failed to generate report: {str(e)}"
