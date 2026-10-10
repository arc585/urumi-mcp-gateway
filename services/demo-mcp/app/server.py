"""Store-information MCP server (demo upstream #2): store policies the assistant can quote."""

from typing import Literal

from mcp.server import MCPServer

from .common import add_health_routes, build_app, simulated_delay

server = MCPServer("Store Info", instructions="Policies of the Urumi demo store: hours, returns, payment, contact.")

POLICIES = {
    "hours": "Open Monday to Saturday, 9 AM to 6 PM IST. Closed on Sundays and public holidays.",
    "returns": "Unused items can be returned within 14 days of delivery for a full refund.",
    "payment": "We accept Cash on delivery. Prices are in Indian rupees (INR) and include no hidden fees.",
    "contact": "Email support@urumi-demo.example or call +91 80 0000 0000 during business hours.",
    "shipping": "Orders ship from Bengaluru within one business day. Ask the shipping tool for delivery estimates.",
}

Topic = Literal["hours", "returns", "payment", "contact", "shipping"]


@server.tool()
async def lookup_store_info(topic: Topic) -> dict[str, str]:
    """Look up one store policy: hours, returns, payment, contact, or shipping."""
    await simulated_delay()
    return {"topic": topic, "answer": POLICIES[topic]}


@server.tool()
async def list_store_info_topics() -> dict[str, list[str]]:
    """List the policy topics this server can answer."""
    await simulated_delay()
    return {"topics": sorted(POLICIES)}


add_health_routes(server)
app = build_app(server, default_host="urumi-store-info-mcp")
