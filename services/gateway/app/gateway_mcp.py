"""The gateway as an MCP *server*: one endpoint that aggregates every enabled upstream.

Tool naming: each upstream tool is published as "<upstream-id>__<tool-name>", for example
"woocommerce__list_orders". Upstream ids match ^[a-z0-9][a-z0-9-]{1,62}$ (no underscores), so
splitting at the first "__" always recovers the upstream id unambiguously, and two upstreams can
both offer a tool called "search" without colliding.
"""

import asyncio
import time
from typing import Any

from mcp.server import Server, ServerRequestContext
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
    TextContent,
    Tool,
)
from sqlalchemy import select
from starlette.responses import JSONResponse

from .auth import Caller, authenticate, extract_key
from .db import SessionLocal
from .models import ToolAudit, Upstream, now_utc
from .upstream_client import UpstreamError, UpstreamTarget, call_tool, discover_tools

SEPARATOR = "__"
MAX_DESCRIPTION_CHARS = 2000


def public_name(upstream_id: str, tool_name: str) -> str:
    return f"{upstream_id}{SEPARATOR}{tool_name}"


def split_name(name: str) -> tuple[str, str] | None:
    if SEPARATOR not in name:
        return None
    upstream_id, tool_name = name.split(SEPARATOR, 1)
    if not upstream_id or not tool_name:
        return None
    return upstream_id, tool_name


async def enabled_targets() -> list[UpstreamTarget]:
    async with SessionLocal() as session:
        rows = await session.scalars(select(Upstream).where(Upstream.enabled.is_(True)).order_by(Upstream.id))
        return [UpstreamTarget.from_model(row) for row in rows.all()]


async def list_tools_for(caller: Caller) -> list[Tool]:
    """All tools of all enabled upstreams, discovered concurrently. Dead upstreams contribute nothing."""
    targets = await enabled_targets()
    discoveries = await asyncio.gather(*(discover_tools(target) for target in targets))
    tools: list[Tool] = []
    for target, discovery in zip(targets, discoveries, strict=True):
        for remote in discovery.tools:
            description = f"[{target.name}] {remote.description or remote.name}"[:MAX_DESCRIPTION_CHARS]
            tools.append(
                Tool(
                    name=public_name(target.id, remote.name),
                    title=f"{target.name}: {remote.title or remote.name}",
                    description=description,
                    input_schema=remote.input_schema,
                    output_schema=remote.output_schema,
                    annotations=remote.annotations,
                )
            )
    return tools


def _error_result(message: str) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=message)], is_error=True)


def _first_text(result: CallToolResult) -> str:
    for item in result.content:
        text = getattr(item, "text", None)
        if text:
            return text[:500]
    return "upstream reported an error"


async def call_tool_as(caller: Caller, name: str, arguments: dict[str, Any], channel: str) -> CallToolResult:
    """Route one call to its upstream as `caller`, and write exactly one audit row for it."""
    started = time.perf_counter()
    occurred_at = now_utc()
    parsed = split_name(name)
    upstream_id, tool_name = parsed if parsed else ("unknown", name)
    upstream_name = upstream_id
    result: CallToolResult
    error: str | None = None

    async with SessionLocal() as session:
        upstream = await session.get(Upstream, upstream_id) if parsed else None
        if parsed is None:
            error = f"Tool name must look like <upstream>{SEPARATOR}<tool>"
            result = _error_result(error)
        elif upstream is None or not upstream.enabled:
            error = f"Upstream '{upstream_id}' is not available (unknown or disabled)"
            result = _error_result(error)
        else:
            upstream_name = upstream.name
            try:
                result = await call_tool(UpstreamTarget.from_model(upstream), tool_name, arguments)
                if result.is_error:
                    error = _first_text(result)
            except UpstreamError as exc:
                error = str(exc)
                result = _error_result(f"{upstream.name} is unavailable: {exc}")

        session.add(
            ToolAudit(
                user_id=caller.user_id,
                api_key_id=caller.key_id,
                channel=channel,
                upstream_id=upstream_id,
                upstream_name=upstream_name,
                tool_name=tool_name,
                occurred_at=occurred_at,
                duration_ms=round((time.perf_counter() - started) * 1000),
                succeeded=error is None,
                error=error,
            )
        )
        await session.commit()
    return result


# ---------------------------------------------------------------------------
# MCP protocol handlers. The caller comes from the HTTP request that carried *this* message
# (set by MCPAuthMiddleware), never from shared state, so one user's identity cannot leak
# into another user's request.
# ---------------------------------------------------------------------------


def _caller(ctx: ServerRequestContext) -> Caller | None:
    request = ctx.request
    state = getattr(request, "state", None)
    return getattr(state, "caller", None) if state is not None else None


async def handle_list_tools(ctx: ServerRequestContext, params: PaginatedRequestParams | None) -> ListToolsResult:
    caller = _caller(ctx)
    if caller is None:
        return ListToolsResult(tools=[])
    return ListToolsResult(tools=await list_tools_for(caller))


async def handle_call_tool(ctx: ServerRequestContext, params: CallToolRequestParams) -> CallToolResult:
    caller = _caller(ctx)
    if caller is None:
        return _error_result("Authentication required")
    return await call_tool_as(caller, params.name, dict(params.arguments or {}), channel="mcp")


gateway_server = Server(
    "Urumi Team MCP Gateway",
    instructions="Tools from the team's MCP servers. Tool names are <server>__<tool>.",
    on_list_tools=handle_list_tools,
    on_call_tool=handle_call_tool,
)


class MCPAuthMiddleware:
    """Reject unauthenticated /mcp requests with 401 and attach the Caller to authenticated ones."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith("/mcp") or scope.get("method") == "OPTIONS":
            await self.app(scope, receive, send)
            return
        headers = {key.decode().lower(): value.decode() for key, value in scope.get("headers", [])}
        raw_key = extract_key(headers)
        caller = await authenticate(raw_key)
        if caller is None:
            detail = "API key required" if not raw_key else "Invalid or revoked API key"
            response = JSONResponse(
                {"detail": detail}, status_code=401, headers={"WWW-Authenticate": 'Bearer realm="urumi-gateway"'}
            )
            await response(scope, receive, send)
            return
        scope.setdefault("state", {})["caller"] = caller
        await self.app(scope, receive, send)
