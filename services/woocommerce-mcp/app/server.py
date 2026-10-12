"""Our own WooCommerce MCP server: product and order tools over WooCommerce's REST API.

Transport: MCP over Streamable HTTP at /mcp (stateless, JSON responses).
Store access: WooCommerce REST API v3 (/wp-json/wc/v3) with a REST key pair injected from a
Kubernetes Secret. The key belongs to a shop_manager user created by the store setup Job.
"""

import os
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import httpx
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

REQUEST_TIMEOUT_SECONDS = float(os.getenv("WOOCOMMERCE_TIMEOUT_SECONDS", "15"))

OrderStatus = Literal["pending", "processing", "on-hold", "completed", "cancelled", "refunded", "failed"]

server = MCPServer(
    "WooCommerce",
    instructions="Products and orders of the Urumi WooCommerce store. Prices are decimal strings in the store currency.",
)


def _settings() -> tuple[str, str, str]:
    base_url = os.getenv("WOOCOMMERCE_URL", "").rstrip("/")
    consumer_key = os.getenv("WOOCOMMERCE_CONSUMER_KEY", "")
    consumer_secret = os.getenv("WOOCOMMERCE_CONSUMER_SECRET", "")
    if not base_url or not consumer_key or not consumer_secret:
        raise ToolError("WooCommerce connection is not configured on the MCP server.")
    return base_url, consumer_key, consumer_secret


def _store_timezone() -> ZoneInfo:
    return ZoneInfo(os.getenv("STORE_TIMEZONE", "Asia/Kolkata"))


async def _request(method: str, path: str, **kwargs: Any) -> Any:
    """Call the WooCommerce REST API and turn every failure into a clear ToolError.

    ToolError messages are returned to the caller (is_error=True). Any other exception would
    reach the client only as an opaque "Error executing tool".
    """
    base_url, consumer_key, consumer_secret = _settings()
    endpoint = f"{base_url}/wp-json/wc/v3/{path.lstrip('/')}"
    # WooCommerce accepts REST keys via Basic auth only on HTTPS requests. Inside the cluster we
    # use plain HTTP to the WordPress Service, so we declare the original scheme the same way a
    # TLS-terminating proxy would; the official WordPress image honours X-Forwarded-Proto.
    headers = {"X-Forwarded-Proto": "https", "Accept": "application/json", "User-Agent": "urumi-woocommerce-mcp"}
    try:
        async with httpx.AsyncClient(
            auth=(consumer_key, consumer_secret),
            headers=headers,
            timeout=httpx.Timeout(REQUEST_TIMEOUT_SECONDS),
        ) as client:
            response = await client.request(method, endpoint, **kwargs)
    except httpx.TimeoutException as exc:
        raise ToolError(f"WooCommerce did not respond within {REQUEST_TIMEOUT_SECONDS:g} seconds.") from exc
    except httpx.HTTPError as exc:
        raise ToolError("Could not reach the WooCommerce store.") from exc

    if response.status_code >= 400:
        try:
            body = response.json()
            message = body.get("message") or body.get("code") or response.reason_phrase
        except ValueError:
            message = response.reason_phrase
        raise ToolError(f"WooCommerce returned HTTP {response.status_code}: {message}")
    return response.json()


def _public_url(url: str | None) -> str | None:
    """Rewrite a WooCommerce link onto the store's public URL.

    Side effect of X-Forwarded-Proto above: WordPress believes the request is HTTPS and builds
    https:// links even when the storefront is plain HTTP (local Kind). STORE_PUBLIC_URL is the
    URL shoppers actually use, set by the Helm chart.
    """
    public = os.getenv("STORE_PUBLIC_URL", "").rstrip("/")
    if not url or not public:
        return url
    parts, base = urlsplit(url), urlsplit(public)
    return urlunsplit((base.scheme, base.netloc, parts.path, parts.query, parts.fragment))


def _local_iso(value: str | None) -> str | None:
    """WooCommerce's date_created_gmt (naive UTC) → ISO-8601 in the store's timezone."""
    if not value:
        return None
    moment = datetime.fromisoformat(value).replace(tzinfo=UTC)
    return moment.astimezone(_store_timezone()).isoformat(timespec="seconds")


def _resolve_day(value: str) -> date:
    today = datetime.now(_store_timezone()).date()
    if value.lower() == "today":
        return today
    if value.lower() == "yesterday":
        return today - timedelta(days=1)
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ToolError("on_date must be 'today', 'yesterday', or a date like 2026-10-09.") from exc


def _utc_param(day: date, at: time) -> str:
    """Start/end of a store-local day, expressed in UTC for the REST API."""
    local = datetime.combine(day, at, tzinfo=_store_timezone())
    return local.astimezone(UTC).replace(tzinfo=None).isoformat(timespec="seconds")


def _order_summary(order: dict[str, Any]) -> dict[str, Any]:
    billing = order.get("billing") or {}
    return {
        "id": order["id"],
        "number": order.get("number"),
        "status": order.get("status"),
        "total": order.get("total"),
        "currency": order.get("currency"),
        "created": _local_iso(order.get("date_created_gmt")),
        "customer": f"{billing.get('first_name', '')} {billing.get('last_name', '')}".strip() or None,
        "payment_method": order.get("payment_method_title"),
        "items": [
            {"name": item.get("name"), "quantity": item.get("quantity"), "total": item.get("total")}
            for item in order.get("line_items", [])
        ],
    }


@server.tool()
async def list_products(
    search: Annotated[str, Field(description="Optional text to match in product names.")] = "",
    status: Literal["any", "publish", "draft", "pending", "private"] = "any",
    limit: Annotated[int, Field(ge=1, le=100)] = 10,
) -> dict[str, Any]:
    """List products in the WooCommerce catalogue, newest first, optionally filtered by name or status."""
    params: dict[str, Any] = {"per_page": limit, "orderby": "date", "order": "desc", "status": status}
    if search.strip():
        params["search"] = search.strip()
    products = await _request("GET", "products", params=params)
    return {
        "products": [
            {
                "id": p["id"],
                "name": p.get("name"),
                "price": p.get("price"),
                "status": p.get("status"),
                "stock_status": p.get("stock_status"),
                "url": _public_url(p.get("permalink")),
            }
            for p in products
        ]
    }


@server.tool()
async def create_product(
    name: Annotated[str, Field(min_length=1, max_length=200)],
    price: Annotated[str, Field(description="Regular price as a decimal string, e.g. '299' or '299.50'.")],
    description: Annotated[str, Field(max_length=5000)] = "",
    sku: Annotated[str, Field(max_length=100, description="Optional unique stock-keeping unit.")] = "",
    status: Literal["publish", "draft"] = "publish",
) -> dict[str, Any]:
    """Create a simple product. Published products appear on the storefront immediately."""
    try:
        amount = Decimal(price.strip())
    except InvalidOperation as exc:
        raise ToolError("price must be a decimal number such as 299 or 299.50.") from exc
    if amount < 0 or amount.as_tuple().exponent < -2:
        raise ToolError("price must be zero or more, with at most two decimal places.")
    payload: dict[str, Any] = {
        "name": name.strip(),
        "type": "simple",
        "status": status,
        "regular_price": format(amount, "f"),
        "description": description,
    }
    if sku.strip():
        payload["sku"] = sku.strip()
    product = await _request("POST", "products", json=payload)
    return {
        "id": product["id"],
        "name": product.get("name"),
        "price": product.get("price"),
        "status": product.get("status"),
        "url": _public_url(product.get("permalink")),
    }


@server.tool()
async def list_orders(
    on_date: Annotated[
        str,
        Field(description="Only orders created on this store-local day: 'today', 'yesterday', or YYYY-MM-DD."),
    ] = "",
    status: Literal["any", "pending", "processing", "on-hold", "completed", "cancelled", "refunded", "failed"] = "any",
    limit: Annotated[int, Field(ge=1, le=100)] = 10,
) -> dict[str, Any]:
    """List orders, newest first, optionally only those created on one day and/or with one status."""
    params: dict[str, Any] = {"per_page": limit, "orderby": "date", "order": "desc", "status": status}
    day: date | None = None
    if on_date.strip():
        day = _resolve_day(on_date.strip())
        params["after"] = _utc_param(day, time.min)
        params["before"] = _utc_param(day + timedelta(days=1), time.min)
        params["dates_are_gmt"] = "true"
    orders = await _request("GET", "orders", params=params)
    return {
        "store_timezone": str(_store_timezone()),
        "day": day.isoformat() if day else None,
        "count": len(orders),
        "orders": [_order_summary(order) for order in orders],
    }


@server.tool()
async def get_order(order_id: Annotated[int, Field(ge=1)]) -> dict[str, Any]:
    """Get one order by its numeric ID, including line items and the customer's name and city."""
    order = await _request("GET", f"orders/{order_id}")
    summary = _order_summary(order)
    billing = order.get("billing") or {}
    summary["items"] = [
        {
            "product_id": item.get("product_id"),
            "name": item.get("name"),
            "quantity": item.get("quantity"),
            "total": item.get("total"),
        }
        for item in order.get("line_items", [])
    ]
    # Data minimisation: city/country are enough for shipping questions; no email, phone, or street.
    summary["customer_city"] = billing.get("city") or None
    summary["customer_country"] = billing.get("country") or None
    summary["customer_note"] = order.get("customer_note") or None
    return summary


@server.tool()
async def update_order_status(order_id: Annotated[int, Field(ge=1)], status: OrderStatus) -> dict[str, Any]:
    """Change an order's status, for example from processing to completed."""
    before = await _request("GET", f"orders/{order_id}")
    order = await _request("PUT", f"orders/{order_id}", json={"status": status})
    return {"id": order["id"], "previous_status": before.get("status"), "status": order.get("status")}


@server.custom_route("/health/live", methods=["GET"])
async def liveness(request: Request) -> Response:
    return JSONResponse({"status": "alive"})


@server.custom_route("/health/ready", methods=["GET"])
async def readiness(request: Request) -> Response:
    # Ready = configured. We deliberately do not call WooCommerce here: if the store is down, tool
    # calls fail with a clear error, but this Pod should not be restarted or pulled from its Service.
    try:
        _settings()
    except ToolError as exc:
        return JSONResponse({"status": "not-ready", "reason": str(exc)}, status_code=503)
    return JSONResponse({"status": "ready"})


def _transport_security() -> TransportSecuritySettings:
    # DNS-rebinding protection: only accept requests whose Host header is our Service name
    # (set by the Helm chart) or localhost (kubectl port-forward during development).
    hosts = os.getenv("MCP_ALLOWED_HOSTS", "localhost:*,127.0.0.1:*").split(",")
    origins = os.getenv("MCP_ALLOWED_ORIGINS", "").split(",")
    return TransportSecuritySettings(
        allowed_hosts=[host.strip() for host in hosts if host.strip()],
        allowed_origins=[origin.strip() for origin in origins if origin.strip()],
    )


# Stateless: every request stands alone (no server-side MCP session), so any replica can serve
# any request and a Pod restart loses nothing. The gateway is our only client.
app = server.streamable_http_app(
    stateless_http=True,
    json_response=True,
    transport_security=_transport_security(),
)
