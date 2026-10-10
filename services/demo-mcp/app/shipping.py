"""Shipping MCP server (demo upstream #3): delivery estimates, protected by a bearer token.

Used for cross-upstream prompts such as "estimate delivery for today's orders", which needs
WooCommerce (order city and total) plus this server.
"""

from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from .common import add_health_routes, build_app, simulated_delay

server = MCPServer("Shipping Info", instructions="Delivery-time and shipping-cost estimates for Indian cities.")

FAST_CITIES = {"bengaluru", "bangalore", "mysuru", "chennai", "hyderabad"}
FREE_SHIPPING_FROM_INR = 999
STANDARD_SHIPPING_INR = 80


@server.tool()
async def estimate_shipping(
    destination_city: Annotated[str, Field(min_length=1, max_length=80)],
    order_total: Annotated[float, Field(ge=0, description="Order total in INR.")],
) -> dict[str, object]:
    """Estimate delivery days and shipping cost (INR) for an order going to an Indian city."""
    await simulated_delay()
    city = destination_city.strip()
    if not city.replace(" ", "").isalpha():
        raise ToolError("destination_city must be a city name, e.g. Bengaluru.")
    fast = city.lower() in FAST_CITIES
    return {
        "destination_city": city,
        "estimated_business_days": 2 if fast else 5,
        "shipping_cost_inr": 0 if order_total >= FREE_SHIPPING_FROM_INR else STANDARD_SHIPPING_INR,
        "free_shipping_threshold_inr": FREE_SHIPPING_FROM_INR,
    }


add_health_routes(server)
app = build_app(server, default_host="urumi-shipping-info-mcp")
