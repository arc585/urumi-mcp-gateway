"""Behaviour of the WooCommerce MCP server against a fake WooCommerce REST API."""

import json
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

pytestmark = pytest.mark.anyio
TZ = ZoneInfo("Asia/Kolkata")


async def call(stack, name: str, arguments: dict):
    async with httpx2.AsyncClient(timeout=20) as http:
        async with Client(streamable_http_client(stack["mcp"], http_client=http)) as client:
            return await client.call_tool(name, arguments)


async def tools(stack):
    async with httpx2.AsyncClient(timeout=20) as http:
        async with Client(streamable_http_client(stack["mcp"], http_client=http)) as client:
            return {tool.name: tool for tool in (await client.list_tools()).tools}


def last_request(stack) -> dict:
    return httpx.get(f"{stack['wc']}/_last").json()


def utc_bound(day, at=time.min) -> str:
    """Store-local midnight expressed the way the MCP server must send it to WooCommerce (naive UTC)."""
    return datetime.combine(day, at, tzinfo=TZ).astimezone(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- contract


async def test_five_tools_with_validating_schemas(stack):
    offered = await tools(stack)
    assert set(offered) == {"list_products", "create_product", "list_orders", "get_order", "update_order_status"}
    limit = offered["list_orders"].input_schema["properties"]["limit"]
    assert (limit["minimum"], limit["maximum"]) == (1, 100)
    assert "processing" in offered["update_order_status"].input_schema["properties"]["status"]["enum"]
    assert "today" in offered["list_orders"].input_schema["properties"]["on_date"]["description"]


def test_readiness_means_configured(stack):
    assert httpx.get(f"{stack['base']}/health/ready").json() == {"status": "ready"}


def test_forged_host_header_is_rejected(stack):
    """DNS-rebinding protection: only our Service name / localhost may appear in Host."""
    response = httpx.post(stack["mcp"], json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                          headers={"Host": "evil.example.com", "Content-Type": "application/json",
                                   "Accept": "application/json, text/event-stream"})
    assert response.status_code == 421


# --------------------------------------------------------------------------- talking to WooCommerce


async def test_requests_carry_the_rest_key_and_claim_https(stack):
    result = await call(stack, "list_products", {"limit": 5, "status": "publish"})
    assert not result.is_error
    sent = last_request(stack)
    assert sent["path"] == "/wp-json/wc/v3/products"
    assert sent["params"] == {"per_page": "5", "orderby": "date", "order": "desc", "status": "publish"}
    assert sent["headers"]["x-forwarded-proto"] == "https"  # without it WooCommerce ignores Basic auth
    assert sent["headers"]["authorization"].startswith("Basic ")
    assert sent["headers"]["user-agent"] == "urumi-woocommerce-mcp"


async def test_links_are_rewritten_onto_the_public_storefront(stack):
    result = await call(stack, "list_products", {})
    product = result.structured_content["products"][0]
    assert product["url"] == f"{stack['public']}/product/urumi-coffee-mug/"
    assert "urumi-wordpress" not in json.dumps(result.structured_content)


async def test_readable_errors_for_missing_slow_and_invalid_input(stack):
    missing = await call(stack, "get_order", {"order_id": 404})
    # The SDK prefixes ToolError text with "Error executing tool <name>: "; our message is the rest.
    assert missing.is_error and missing.content[0].text.endswith("WooCommerce returned HTTP 404: Invalid ID.")
    slow = await call(stack, "get_order", {"order_id": 999})
    assert slow.is_error and "did not respond within 1 seconds" in slow.content[0].text
    bad_date = await call(stack, "list_orders", {"on_date": "someday"})
    assert bad_date.is_error and "on_date must be" in bad_date.content[0].text
    too_many = await call(stack, "list_orders", {"limit": 500})
    assert too_many.is_error  # rejected by the schema before our code runs


# --------------------------------------------------------------------------- tools


@pytest.mark.parametrize("price, message", [("abc", "decimal number"), ("-1", "zero or more"), ("1.234", "two decimal places")])
async def test_create_product_validates_the_price(stack, price, message):
    result = await call(stack, "create_product", {"name": "Bad", "price": price})
    assert result.is_error and message in result.content[0].text


async def test_create_product_posts_a_simple_product(stack):
    result = await call(stack, "create_product", {"name": "Blue Ceramic Mug", "price": " 299.50 ", "sku": "MUG-BLUE"})
    assert not result.is_error
    sent = last_request(stack)
    assert (sent["method"], sent["path"]) == ("POST", "/wp-json/wc/v3/products")
    assert sent["json"] == {"name": "Blue Ceramic Mug", "type": "simple", "status": "publish", "regular_price": "299.50",
                            "description": "", "sku": "MUG-BLUE"}
    assert result.structured_content["url"] == f"{stack['public']}/product/blue-ceramic-mug/"


async def test_today_means_the_store_local_day(stack):
    today = datetime.now(TZ).date()
    result = await call(stack, "list_orders", {"on_date": "today"})
    assert not result.is_error
    sent = last_request(stack)
    assert sent["params"]["after"] == utc_bound(today)
    assert sent["params"]["before"] == utc_bound(today + timedelta(days=1))
    assert sent["params"]["dates_are_gmt"] == "true"
    body = result.structured_content
    assert (body["store_timezone"], body["day"], body["count"]) == ("Asia/Kolkata", today.isoformat(), 1)
    assert body["orders"][0]["id"] == 12  # yesterday's order 11 is excluded
    assert body["orders"][0]["created"] == f"{today.isoformat()}T10:00:00+05:30"


async def test_get_order_minimises_customer_data(stack):
    result = await call(stack, "get_order", {"order_id": 12})
    assert not result.is_error
    order = result.structured_content
    assert (order["customer"], order["customer_city"], order["customer_country"]) == ("Demo Shopper", "Mysuru", "IN")
    assert order["items"][0] == {"product_id": 7, "name": "Urumi Coffee Mug", "quantity": 2, "total": "998.00"}
    dumped = json.dumps(order)
    assert "secret@example.test" not in dumped and "9999999999" not in dumped and "MG Road" not in dumped


async def test_update_order_status_reads_then_writes(stack):
    result = await call(stack, "update_order_status", {"order_id": 11, "status": "processing"})
    assert not result.is_error
    assert result.structured_content == {"id": 11, "previous_status": "completed", "status": "processing"}
    sent = last_request(stack)
    assert (sent["method"], sent["path"], sent["json"]) == ("PUT", "/wp-json/wc/v3/orders/11", {"status": "processing"})
