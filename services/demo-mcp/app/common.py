"""Shared plumbing for the demo MCP servers: health routes, Host checks, optional bearer token, delay."""

import asyncio
import hmac
import os
from typing import Any

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


def add_health_routes(server: MCPServer) -> None:
    @server.custom_route("/health/live", methods=["GET"])
    async def liveness(request: Request) -> Response:
        return JSONResponse({"status": "alive"})

    @server.custom_route("/health/ready", methods=["GET"])
    async def readiness(request: Request) -> Response:
        return JSONResponse({"status": "ready"})


async def simulated_delay() -> None:
    """DEMO_DELAY_SECONDS > 0 makes every tool slow, to demonstrate gateway timeouts."""
    delay = float(os.getenv("DEMO_DELAY_SECONDS", "0"))
    if delay > 0:
        await asyncio.sleep(delay)


class BearerTokenMiddleware:
    """If REQUIRED_BEARER_TOKEN is set, /mcp requires "Authorization: Bearer <token>".

    This is the upstream's own credential. Only the gateway holds it (encrypted in its database);
    team members and their MCP clients never see it.
    """

    def __init__(self, app: Any) -> None:
        self.app = app
        self.token = os.getenv("REQUIRED_BEARER_TOKEN", "")

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if self.token and scope["type"] == "http" and scope.get("path", "").startswith("/mcp"):
            headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
            supplied = headers.get("authorization", "")
            if not hmac.compare_digest(supplied.encode(), f"Bearer {self.token}".encode()):
                await JSONResponse({"detail": "invalid upstream credential"}, status_code=401)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def build_app(server: MCPServer, default_host: str) -> Any:
    hosts = os.getenv("MCP_ALLOWED_HOSTS", f"{default_host},{default_host}:8000,localhost:*,127.0.0.1:*").split(",")
    origins = os.getenv("MCP_ALLOWED_ORIGINS", "").split(",")
    app = server.streamable_http_app(
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            allowed_hosts=[h.strip() for h in hosts if h.strip()],
            allowed_origins=[o.strip() for o in origins if o.strip()],
        ),
    )
    return BearerTokenMiddleware(app)
