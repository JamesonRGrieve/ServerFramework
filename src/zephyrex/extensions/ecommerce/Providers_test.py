# SPDX-License-Identifier: AGPL-3.0-or-later
"""The store providers: each platform's answer (as its API documents it)
read into the shared shapes, what each refuses before any call, the
AliExpress request signature, and real calls refusing bogus credentials
(xfail when the platform is unreachable)."""

import hashlib
import hmac
from datetime import UTC, datetime

import httpx
import pytest

from zephyrex.extensions.ecommerce.PRV_Aliexpress import (
    PRV_Aliexpress_ECommerce,
    aliexpress_order,
    pacific,
    sign,
    sku_parts,
)
from zephyrex.extensions.ecommerce.PRV_Amazon import (
    PRV_Amazon_ECommerce,
    amazon_order,
    amazon_product,
)
from zephyrex.extensions.ecommerce.PRV_Ebay import PRV_Ebay_ECommerce, ebay_order
from zephyrex.extensions.ecommerce.PRV_Etsy import PRV_Etsy_ECommerce, etsy_order
from zephyrex.extensions.ecommerce.PRV_Shopify import PRV_Shopify_ECommerce
from zephyrex.extensions.ecommerce.PRV_Walmart import (
    PRV_Walmart_ECommerce,
    walmart_order,
)
from zephyrex.extensions.ecommerce.PRV_WooCommerce import (
    PRV_WooCommerce_ECommerce,
    woo_order,
)
from zephyrex.extensions.ExternalErrors import (
    AuthExternalError,
    InvalidInputExternalError,
    PermanentExternalError,
)

NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)


def _online(url: str) -> bool:
    try:
        httpx.head(url, timeout=5)
        return True
    except httpx.HTTPError:
        return False


def reachable(url: str) -> pytest.MarkDecorator:
    return pytest.mark.xfail(not _online(url), reason=f"{url} is unreachable")


class TestReading:
    def test_woocommerce(self):
        found = woo_order(
            {
                "id": 77,
                "number": "77",
                "status": "completed",
                "currency": "USD",
                "total": "30.00",
                "date_created_gmt": "2026-10-01T10:00:00",
                "billing": {
                    "first_name": "Ada",
                    "last_name": "Lovelace",
                    "email": "a@b.co",
                },
                "shipping": {"city": "London"},
                "line_items": [
                    {"sku": "S1", "name": "Scarf", "quantity": 1, "price": 30}
                ],
            }
        )
        assert found["status"] == "shipped" and found["customer_name"] == "Ada Lovelace"
        assert found["order_date"] == "2026-10-01T10:00:00+00:00"
        assert found["line_items"][0]["price"] == "30"

    def test_etsy(self):
        found = etsy_order(
            {
                "receipt_id": 9001,
                "name": "Ada",
                "status": "Paid",
                "is_shipped": True,
                "create_timestamp": 1790000000,
                "grandtotal": {"amount": 2550, "divisor": 100, "currency_code": "EUR"},
                "transactions": [
                    {
                        "sku": "MUG",
                        "title": "Mug",
                        "quantity": 1,
                        "price": {"amount": 2550, "divisor": 100},
                    }
                ],
            }
        )
        assert found["status"] == "shipped" and found["total"] == "25.5"
        assert found["currency"] == "EUR" and found["line_items"][0]["price"] == "25.5"

    def test_ebay(self):
        found = ebay_order(
            {
                "orderId": "12-34567-89012",
                "creationDate": "2026-10-01T10:00:00.000Z",
                "orderFulfillmentStatus": "IN_PROGRESS",
                "orderPaymentStatus": "PAID",
                "pricingSummary": {"total": {"value": "19.99", "currency": "USD"}},
                "fulfillmentStartInstructions": [
                    {"shippingStep": {"shipTo": {"fullName": "Ada", "email": "a@b.co"}}}
                ],
                "lineItems": [
                    {
                        "sku": "S1",
                        "title": "Scarf",
                        "quantity": 1,
                        "lineItemCost": {"value": "19.99"},
                    }
                ],
            }
        )
        assert found["status"] == "processing" and found["customer_name"] == "Ada"
        cancelled = ebay_order(
            {"orderId": "1", "cancelStatus": {"cancelState": "CANCELED"}}
        )
        assert cancelled["status"] == "cancelled"

    def test_amazon(self):
        found = amazon_order(
            {
                "orderId": "111-2222222-3333333",
                "createdTime": "2026-10-01T10:00:00Z",
                "fulfillment": {"fulfillmentStatus": "UNSHIPPED"},
                "proceeds": {"grandTotal": {"amount": "45.00", "currencyCode": "USD"}},
                "orderItems": [
                    {
                        "quantityOrdered": 3,
                        "product": {
                            "sellerSku": "SKU-1",
                            "title": "Widget",
                            "price": {"unitPrice": {"amount": "15.00"}},
                        },
                    }
                ],
            }
        )
        assert found["status"] == "processing" and found["total"] == "45"
        assert found["line_items"] == [
            {"sku": "SKU-1", "title": "Widget", "quantity": 3, "price": "15"}
        ]
        listing = amazon_product(
            {
                "sku": "SKU-1",
                "summaries": [{"itemName": "Widget", "status": ["BUYABLE"]}],
                "offers": [{"price": {"amount": "15.00", "currencyCode": "USD"}}],
                "fulfillmentAvailability": [{"quantity": 7}],
            }
        )
        assert listing["is_active"] and listing["quantity"] == 7

    def test_walmart(self):
        found = walmart_order(
            {
                "purchaseOrderId": "4792",
                "customerOrderId": "1581",
                "orderDate": 1790000000000,
                "shippingInfo": {"postalAddress": {"name": "Ada"}},
                "orderLines": {
                    "orderLine": [
                        {
                            "lineNumber": "1",
                            "item": {"sku": "S1", "productName": "Scarf"},
                            "orderLineQuantity": {"amount": "2"},
                            "charges": {
                                "charge": [
                                    {
                                        "chargeType": "PRODUCT",
                                        "chargeAmount": {
                                            "amount": 20,
                                            "currency": "USD",
                                        },
                                    },
                                    {
                                        "chargeType": "SHIPPING",
                                        "chargeAmount": {
                                            "amount": 5,
                                            "currency": "USD",
                                        },
                                    },
                                ]
                            },
                            "orderLineStatuses": {
                                "orderLineStatus": [{"status": "Acknowledged"}]
                            },
                        }
                    ]
                },
            }
        )
        assert found["status"] == "processing" and found["total"] == "25"
        assert found["order_date"].startswith("2026-")

    def test_aliexpress(self):
        listed = aliexpress_order(
            {
                "order_id": 8123456789012345678,
                "order_status": "WAIT_SELLER_SEND_GOODS",
                "gmt_create": "2026-10-01 03:00:00",
                "pay_amount": {"amount": "9.90", "currency_code": "USD"},
                "product_list": {
                    "order_product_dto": [
                        {
                            "sku_code": "RED",
                            "product_name": "Cap",
                            "product_count": 1,
                            "product_unit_price": {
                                "amount": "9.90",
                                "currency_code": "USD",
                            },
                        }
                    ]
                },
            }
        )
        assert listed["platform_order_id"] == "8123456789012345678"
        assert listed["status"] == "processing" and listed["total"] == "9.9"


class TestRules:
    def test_aliexpress_signature(self):
        expected = hmac.new(b"secret", b"a1b2", hashlib.sha256).hexdigest().upper()
        assert sign({"b": "2", "a": "1"}, "secret") == expected
        with_path = hmac.new(b"secret", b"/auth/token/refresha1", hashlib.sha256)
        assert (
            sign({"a": "1"}, "secret", "/auth/token/refresh")
            == with_path.hexdigest().upper()
        )

    def test_aliexpress_skus_and_times(self):
        assert sku_parts("1005001:RED-L") == (1005001, "RED-L")
        for bad in ("RED-L", "abc:RED", "123:"):
            with pytest.raises(InvalidInputExternalError):
                sku_parts(bad)
        assert pacific(NOW) == "2026-10-01 05:00:00"

    async def test_up_front_refusals(self, provider_instance):
        etsy = provider_instance(PRV_Etsy_ECommerce)
        with pytest.raises(PermanentExternalError):
            await PRV_Etsy_ECommerce.cancel_order(etsy, "1", "no stock")
        with pytest.raises(PermanentExternalError):
            await PRV_Etsy_ECommerce.returns(etsy, NOW, 10)
        woo = provider_instance(PRV_WooCommerce_ECommerce)
        with pytest.raises(PermanentExternalError):
            await PRV_WooCommerce_ECommerce.process_return(woo, "1", "reject", None)
        walmart = provider_instance(PRV_Walmart_ECommerce)
        with pytest.raises(PermanentExternalError):
            await PRV_Walmart_ECommerce.update_product(walmart, "S1", {"title": "New"})
        shopify = provider_instance(PRV_Shopify_ECommerce)
        with pytest.raises(PermanentExternalError):
            await PRV_Shopify_ECommerce.process_return(shopify, "1", "approve", "5")

    async def test_woocommerce_keys_only_over_https(self, provider_instance):
        woo = provider_instance(
            PRV_WooCommerce_ECommerce,
            api_key="ck_x",
            settings={"consumer_secret": "cs_x", "site_url": "http://shop.example.com"},
        )
        with pytest.raises(InvalidInputExternalError, match="https"):
            await PRV_WooCommerce_ECommerce.products(woo, 5)

    async def test_shipping_needs_tracking_where_the_platform_does(
        self, provider_instance
    ):
        for provider in (PRV_Etsy_ECommerce, PRV_Ebay_ECommerce, PRV_Amazon_ECommerce):
            with pytest.raises(InvalidInputExternalError, match="tracking"):
                await provider.fulfill_order(
                    provider_instance(provider), "1", None, None
                )


class TestRefusedCredentials:
    @reachable("https://api.etsy.com")
    async def test_etsy(self, provider_instance):
        etsy = provider_instance(
            PRV_Etsy_ECommerce,
            api_key="bogus",
            settings={
                "shared_secret": "bogus",
                "refresh_token": "bogus",
                "shop_id": "1",
            },
        )
        with pytest.raises(AuthExternalError):
            await PRV_Etsy_ECommerce.orders(etsy, NOW, 5)

    @reachable("https://api.ebay.com")
    async def test_ebay(self, provider_instance):
        ebay = provider_instance(
            PRV_Ebay_ECommerce,
            api_key="bogus",
            settings={"client_secret": "bogus", "refresh_token": "bogus"},
        )
        with pytest.raises(AuthExternalError):
            await PRV_Ebay_ECommerce.orders(ebay, NOW, 5)

    @reachable("https://api.amazon.com")
    async def test_amazon(self, provider_instance):
        amazon = provider_instance(
            PRV_Amazon_ECommerce,
            api_key="bogus",
            settings={
                "client_secret": "bogus",
                "refresh_token": "bogus",
                "seller_id": "A1",
            },
        )
        with pytest.raises(AuthExternalError):
            await PRV_Amazon_ECommerce.orders(amazon, NOW, 5)

    @reachable("https://marketplace.walmartapis.com")
    async def test_walmart(self, provider_instance):
        walmart = provider_instance(
            PRV_Walmart_ECommerce, api_key="bogus", settings={"client_secret": "bogus"}
        )
        with pytest.raises(AuthExternalError):
            await PRV_Walmart_ECommerce.orders(walmart, NOW, 5)

    @reachable("https://api-sg.aliexpress.com")
    async def test_aliexpress(self, provider_instance):
        aliexpress = provider_instance(
            PRV_Aliexpress_ECommerce,
            api_key="12345",
            settings={"app_secret": "bogus", "access_token": "bogus"},
        )
        with pytest.raises(AuthExternalError):
            await PRV_Aliexpress_ECommerce.orders(aliexpress, NOW, 5)


# platform -> (provider, credentials group, instance api_key variable,
# other settings by environment variable)
LIVE = {
    "shopify": (
        PRV_Shopify_ECommerce,
        "SHOPIFY_TOKEN",
        {"shop_domain": "SHOPIFY_SHOP"},
    ),
    "woocommerce": (
        PRV_WooCommerce_ECommerce,
        "WOOCOMMERCE_KEY",
        {"consumer_secret": "WOOCOMMERCE_SECRET", "site_url": "WOOCOMMERCE_URL"},
    ),
    "etsy": (
        PRV_Etsy_ECommerce,
        "ETSY_KEYSTRING",
        {
            "shared_secret": "ETSY_SHARED_SECRET",
            "refresh_token": "ETSY_REFRESH_TOKEN",
            "shop_id": "ETSY_SHOP_ID",
        },
    ),
    "ebay": (
        PRV_Ebay_ECommerce,
        "EBAY_CLIENT_ID",
        {
            "client_secret": "EBAY_CLIENT_SECRET",
            "refresh_token": "EBAY_REFRESH_TOKEN",
            "environment": "EBAY_ENVIRONMENT",
        },
    ),
    "amazon": (
        PRV_Amazon_ECommerce,
        "AMAZON_LWA_CLIENT_ID",
        {
            "client_secret": "AMAZON_LWA_CLIENT_SECRET",
            "refresh_token": "AMAZON_REFRESH_TOKEN",
            "seller_id": "AMAZON_SELLER_ID",
        },
    ),
    "walmart": (
        PRV_Walmart_ECommerce,
        "WALMART_CLIENT_ID",
        {"client_secret": "WALMART_CLIENT_SECRET"},
    ),
    "aliexpress": (
        PRV_Aliexpress_ECommerce,
        "ALIEXPRESS_APP_KEY",
        {
            "app_secret": "ALIEXPRESS_APP_SECRET",
            "access_token": "ALIEXPRESS_ACCESS_TOKEN",
        },
    ),
}


class TestLive:
    """Read-only: a test store's recent orders."""

    @pytest.mark.parametrize(
        "platform",
        [
            pytest.param(name, marks=pytest.mark.external_api(provider=f"{name}_store"))
            for name in LIVE
        ],
    )
    async def test_recent_orders(
        self, platform, provider_instance, sandbox_credentials_for
    ):
        provider, key, settings = LIVE[platform]
        creds = sandbox_credentials_for(f"{platform}_store")
        instance = provider_instance(
            provider,
            api_key=creds[key],
            settings={
                setting: creds[variable] for setting, variable in settings.items()
            },
        )
        assert isinstance(
            await provider.orders(instance, NOW.replace(year=2026, month=1), 5), list
        )
