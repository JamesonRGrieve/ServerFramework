import logging
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import requests

from .PRV_ECommerce import AbstractEcommerceProvider


class EbayProvider(AbstractEcommerceProvider):
    """
    eBay provider implementation.
    Handles eBay API interactions for marketplace management.
    """

    def _configure_provider(self, **kwargs) -> None:
        self.friendly_name = "eBay"
        self.access_token = kwargs.get("EBAY_ACCESS_TOKEN", None)
        self.refresh_token = kwargs.get("EBAY_REFRESH_TOKEN", None)
        self.seller_id = kwargs.get("EBAY_SELLER_ID", "")
        self.api_environment = kwargs.get("EBAY_API_ENVIRONMENT", "production")
        self.client_id = os.getenv("EBAY_CLIENT_ID", "")
        self.client_secret = os.getenv("EBAY_CLIENT_SECRET", "")

        # Set API URLs based on environment
        if self.api_environment == "sandbox":
            self.api_base_url = "https://api.sandbox.ebay.com"
        else:
            self.api_base_url = "https://api.ebay.com"

        self.register_capability("order_management")
        self.register_capability("inventory_management")
        self.register_capability("product_management")
        self.register_capability("returns_management")
        self.register_capability("fulfillment")

    @staticmethod
    def services() -> List[str]:
        return ["ecommerce", "ebay"]

    def verify_user(self):
        """
        Verify the user's authentication with eBay.
        If verification fails, raises an exception.
        """
        logging.info(f"Verifying user with eBay seller ID: {self.seller_id}")

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

        try:
            # Use the user API to verify credentials
            response = requests.get(f"{self.api_base_url}/ws/api.dll", headers=headers)

            if response.status_code != 200:
                raise Exception(f"eBay user verification failed: {response.text}")
        except Exception as e:
            logging.error(f"Error verifying eBay user: {str(e)}")
            raise Exception(f"eBay user verification failed: {str(e)}")

    async def get_orders(
        self,
        status: str = "ACTIVE",
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 50,
    ) -> List[Dict]:
        try:
            self.verify_user()

            if not start_date:
                start_date = datetime.now() - timedelta(days=90)
            if not end_date:
                end_date = datetime.now()

            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
            }

            params = {
                "filter": f"creationdate:[{start_date.isoformat()}..{end_date.isoformat()}]",
                "limit": limit,
                "offset": 0,
                "orderStatus": status,
            }

            response = requests.get(
                f"{self.api_base_url}/sell/fulfillment/v1/order",
                headers=headers,
                params=params,
            )

            if response.status_code != 200:
                raise Exception(f"Failed to fetch eBay orders: {response.text}")

            orders = []
            for order in response.json().get("orders", []):
                orders.append(
                    {
                        "order_id": order["orderId"],
                        "order_status": order["orderFulfillmentStatus"],
                        "creation_date": order["creationDate"],
                        "last_modified_date": order["lastModifiedDate"],
                        "buyer": {
                            "username": order.get("buyer", {}).get("username", ""),
                            "email": order.get("buyer", {}).get("email", ""),
                        },
                        "total": order.get("pricingSummary", {})
                        .get("total", {})
                        .get("value"),
                        "currency": order.get("pricingSummary", {})
                        .get("total", {})
                        .get("currency"),
                        "items": order.get("lineItems", []),
                    }
                )

            return orders

        except Exception as e:
            logging.error(f"Error retrieving eBay orders: {str(e)}")
            return []

    async def acknowledge_order(self, order_id: str) -> str:
        try:
            self.verify_user()
            # eBay doesn't have a direct acknowledgment process
            # This is a placeholder implementation
            return "Order acknowledged successfully."
        except Exception as e:
            logging.error(f"Error acknowledging eBay order: {str(e)}")
            return f"Failed to acknowledge order: {str(e)}"

    async def cancel_order(self, order_id: str, reason: str) -> str:
        try:
            self.verify_user()

            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
            }

            data = {
                "cancelStateRequest": {
                    "cancelState": "CANCEL_REQUESTED",
                    "cancelReason": reason,
                }
            }

            response = requests.post(
                f"{self.api_base_url}/sell/fulfillment/v1/order/{order_id}/cancel",
                headers=headers,
                json=data,
            )

            if response.status_code not in [200, 201, 204]:
                raise Exception(f"Failed to cancel eBay order: {response.text}")

            return "Order cancellation request submitted successfully."

        except Exception as e:
            logging.error(f"Error cancelling eBay order: {str(e)}")
            return f"Failed to cancel order: {str(e)}"

    async def get_inventory(self, sku_list: Optional[List[str]] = None) -> List[Dict]:
        try:
            self.verify_user()

            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
            }

            params = {"limit": 100, "offset": 0}

            # If SKUs are provided, we need to make separate requests for each SKU
            if sku_list:
                inventory = []
                for sku in sku_list:
                    sku_response = requests.get(
                        f"{self.api_base_url}/sell/inventory/v1/inventory_item/{sku}",
                        headers=headers,
                    )
                    if sku_response.status_code == 200:
                        item = sku_response.json()
                        inventory.append(
                            {
                                "sku": sku,
                                "availability": item.get("availability", {}).get(
                                    "availabilityStatus"
                                ),
                                "quantity": item.get("availability", {}).get(
                                    "quantity"
                                ),
                                "condition": item.get("condition"),
                                "product": item.get("product", {}),
                            }
                        )
                return inventory

            # Otherwise, get all inventory
            response = requests.get(
                f"{self.api_base_url}/sell/inventory/v1/inventory_item",
                headers=headers,
                params=params,
            )

            if response.status_code != 200:
                raise Exception(f"Failed to fetch eBay inventory: {response.text}")

            inventory = []
            for sku, item in response.json().get("inventoryItems", {}).items():
                inventory.append(
                    {
                        "sku": sku,
                        "availability": item.get("availability", {}).get(
                            "availabilityStatus"
                        ),
                        "quantity": item.get("availability", {}).get("quantity"),
                        "condition": item.get("condition"),
                        "product": item.get("product", {}),
                    }
                )

            return inventory

        except Exception as e:
            logging.error(f"Error retrieving eBay inventory: {str(e)}")
            return []

    async def update_inventory(self, sku: str, quantity: int) -> str:
        try:
            self.verify_user()

            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
            }

            # First, get the current inventory item
            get_response = requests.get(
                f"{self.api_base_url}/sell/inventory/v1/inventory_item/{sku}",
                headers=headers,
            )

            if get_response.status_code != 200:
                raise Exception(
                    f"Failed to fetch current inventory item: {get_response.text}"
                )

            current_item = get_response.json()

            # Update the quantity
            current_item["availability"] = {
                "availabilityStatus": "IN_STOCK" if quantity > 0 else "OUT_OF_STOCK",
                "quantity": quantity,
            }

            # Submit the update
            update_response = requests.put(
                f"{self.api_base_url}/sell/inventory/v1/inventory_item/{sku}",
                headers=headers,
                json=current_item,
            )

            if update_response.status_code not in [200, 201, 204]:
                raise Exception(f"Failed to update inventory: {update_response.text}")

            return "Inventory updated successfully."

        except Exception as e:
            logging.error(f"Error updating eBay inventory: {str(e)}")
            return f"Failed to update inventory: {str(e)}"

    async def get_products(self, limit: int = 100, offset: int = 0) -> List[Dict]:
        try:
            self.verify_user()

            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
            }

            params = {"limit": limit, "offset": offset}

            response = requests.get(
                f"{self.api_base_url}/sell/inventory/v1/inventory_item",
                headers=headers,
                params=params,
            )

            if response.status_code != 200:
                raise Exception(f"Failed to fetch eBay products: {response.text}")

            products = []
            for sku, item in response.json().get("inventoryItems", {}).items():
                # For each inventory item, we might need to get the offers as well
                offer_response = requests.get(
                    f"{self.api_base_url}/sell/inventory/v1/offer?sku={sku}",
                    headers=headers,
                )

                offers = []
                if offer_response.status_code == 200:
                    offers = offer_response.json().get("offers", [])

                products.append(
                    {
                        "sku": sku,
                        "availability": item.get("availability", {}).get(
                            "availabilityStatus"
                        ),
                        "quantity": item.get("availability", {}).get("quantity"),
                        "condition": item.get("condition"),
                        "product": item.get("product", {}),
                        "offers": offers,
                    }
                )

            return products

        except Exception as e:
            logging.error(f"Error retrieving eBay products: {str(e)}")
            return []

    async def update_product(self, sku: str, updates: Dict) -> str:
        try:
            self.verify_user()

            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
            }

            # First, get the current inventory item
            get_response = requests.get(
                f"{self.api_base_url}/sell/inventory/v1/inventory_item/{sku}",
                headers=headers,
            )

            if get_response.status_code != 200:
                raise Exception(
                    f"Failed to fetch current inventory item: {get_response.text}"
                )

            current_item = get_response.json()

            # Update the fields
            for key, value in updates.items():
                if key in current_item:
                    if isinstance(current_item[key], dict) and isinstance(value, dict):
                        # Merge nested dictionaries
                        current_item[key].update(value)
                    else:
                        current_item[key] = value

            # Submit the update
            update_response = requests.put(
                f"{self.api_base_url}/sell/inventory/v1/inventory_item/{sku}",
                headers=headers,
                json=current_item,
            )

            if update_response.status_code not in [200, 201, 204]:
                raise Exception(f"Failed to update product: {update_response.text}")

            return "Product updated successfully."

        except Exception as e:
            logging.error(f"Error updating eBay product: {str(e)}")
            return f"Failed to update product: {str(e)}"

    async def get_returns(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 50,
    ) -> List[Dict]:
        try:
            self.verify_user()

            if not start_date:
                start_date = datetime.now() - timedelta(days=90)
            if not end_date:
                end_date = datetime.now()

            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
            }

            params = {
                "creation_date_range_from": start_date.isoformat(),
                "creation_date_range_to": end_date.isoformat(),
                "limit": limit,
                "offset": 0,
            }

            response = requests.get(
                f"{self.api_base_url}/post-order/v2/return",
                headers=headers,
                params=params,
            )

            if response.status_code != 200:
                raise Exception(f"Failed to fetch eBay returns: {response.text}")

            returns = []
            for return_item in response.json().get("returns", []):
                returns.append(
                    {
                        "return_id": return_item["returnId"],
                        "status": return_item["status"],
                        "return_reason": return_item.get("returnReason", ""),
                        "creation_date": return_item["creationDate"],
                        "last_modified_date": return_item["lastModifiedDate"],
                        "refund_amount": return_item.get("refundAmount", {}).get(
                            "value"
                        ),
                        "currency": return_item.get("refundAmount", {}).get("currency"),
                    }
                )

            return returns

        except Exception as e:
            logging.error(f"Error retrieving eBay returns: {str(e)}")
            return []

    async def process_return(
        self, return_id: str, action: str, refund_amount: Optional[float] = None
    ) -> str:
        try:
            self.verify_user()

            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
            }

            if action.upper() == "APPROVE":
                data = {"decision": "APPROVE"}

                if refund_amount:
                    data["refundDetails"] = {
                        "amount": {
                            "value": str(refund_amount),
                            "currency": "USD",  # This should be configurable
                        }
                    }

                response = requests.post(
                    f"{self.api_base_url}/post-order/v2/return/{return_id}/decide",
                    headers=headers,
                    json=data,
                )

                if response.status_code not in [200, 201, 204]:
                    raise Exception(f"Failed to approve return: {response.text}")

                return "Return approved successfully."

            elif action.upper() == "DECLINE":
                data = {"decision": "DECLINE", "comments": "Return declined by seller."}

                response = requests.post(
                    f"{self.api_base_url}/post-order/v2/return/{return_id}/decide",
                    headers=headers,
                    json=data,
                )

                if response.status_code not in [200, 201, 204]:
                    raise Exception(f"Failed to decline return: {response.text}")

                return "Return declined successfully."

            else:
                return f"Unsupported action: {action}. Supported actions are APPROVE or DECLINE."

        except Exception as e:
            logging.error(f"Error processing eBay return: {str(e)}")
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

            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
            }

            # Map our report type to eBay's report types
            report_type_mapping = {
                "orders": "ORDERS_SNAPSHOT",
                "sales": "SALES_SNAPSHOT",
                "inventory": "INVENTORY_SNAPSHOT",
                "returns": "RETURNS_SNAPSHOT",
            }

            ebay_report_type = report_type_mapping.get(report_type.lower(), report_type)

            data = {
                "reportType": ebay_report_type,
                "dateFrom": start_date.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "dateTo": end_date.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "reportFormat": "CSV",
            }

            response = requests.post(
                f"{self.api_base_url}/sell/analytics/v1/report",
                headers=headers,
                json=data,
            )

            if response.status_code not in [200, 201]:
                raise Exception(f"Failed to request report: {response.text}")

            report_task_id = response.json().get("reportTaskId")
            return f"Report generation started. Task ID: {report_task_id}"

        except Exception as e:
            logging.error(f"Error generating eBay report: {str(e)}")
            return f"Failed to generate report: {str(e)}"
