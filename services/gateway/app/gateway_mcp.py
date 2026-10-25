"""The gateway as an MCP *server*: one endpoint that aggregates every enabled upstream.

Tool naming: each upstream tool is published as "<upstream-id>__<tool-name>", for example
"woocommerce__list_orders". Upstream ids match ^[a-z0-9][a-z0-9-]{1,62}$ (no underscores), so
splitting at the first "__" always recovers the upstream id unambiguously, and two upstreams can
both offer a tool called "search" without colliding.
"""

import asyncio
import contextlib
import logging
import os
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
from .models import AdminOnlyTool, ToolAudit, Upstream, now_utc
from .ratelimit import tool_calls
from .upstream_client import UpstreamError, UpstreamTarget, call_tool, discover_tools

SEPARATOR = "__"
MAX_DESCRIPTION_CHARS = 2000
# Some MCP clients refuse tool names longer than this (Claude prepends its own "mcp__<server>__" too).
MAX_PUBLIC_NAME_CHARS = 64
_warned_names: set[str] = set()
log = logging.getLogger("urumi.gateway.mcp")


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


async def admin_only_tools() -> set[tuple[str, str]]:
    """(upstream_id, tool_name) pairs restricted to admins."""
    async with SessionLocal() as session:
        rows = await session.execute(select(AdminOnlyTool.upstream_id, AdminOnlyTool.tool_name))
        return {(upstream_id, tool) for upstream_id, tool in rows.all()}


async def list_tools_for(caller: Caller) -> list[Tool]:
    """All tools of all enabled upstreams the caller may use, discovered concurrently.

    Dead upstreams contribute nothing; admin-only tools are hidden from members.
    """
    restricted = set() if caller.is_admin else await admin_only_tools()
    targets = await enabled_targets()
    discoveries = await asyncio.gather(*(discover_tools(target) for target in targets))
    tools: list[Tool] = []
    for target, discovery in zip(targets, discoveries, strict=True):
        for remote in discovery.tools:
            if (target.id, remote.name) in restricted:
                continue
            description = f"[{target.name}] {remote.description or remote.name}"[:MAX_DESCRIPTION_CHARS]
            name = public_name(target.id, remote.name)
            if len(name) > MAX_PUBLIC_NAME_CHARS and name not in _warned_names:
                _warned_names.add(name)
                log.warning("Tool name %r is %d characters; some MCP clients refuse names over %d",
                            name, len(name), MAX_PUBLIC_NAME_CHARS)
            tools.append(
                Tool(
                    name=name,
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
    """Route one call to its upstream as `caller`, and write exactly one audit row for it.

    The database is used in two short sessions, before and after the upstream call, never across it.
    A session that has run a query keeps one pooled connection until it commits, and the pool holds
    15 connections by default: a session left open during a (up to 30 s) upstream call would let one
    slow upstream with 15 callers in flight block every other request, authentication included.
    """
    started = time.perf_counter()
    occurred_at = now_utc()
    parsed = split_name(name)
    upstream_id, tool_name = parsed if parsed else ("unknown", name)
    upstream_name = upstream_id
    target: UpstreamTarget | None = None
    error: str | None = None

    # 1. Policy checks in one short read-only session, released before any network call.
    if parsed is None:
        error = f"Tool name must look like <upstream>{SEPARATOR}<tool>"
    else:
        async with SessionLocal() as session:
            upstream = await session.get(Upstream, upstream_id)
            if upstream is None or not upstream.enabled:
                error = f"Upstream '{upstream_id}' is not available (unknown or disabled)"
            elif not caller.is_admin and await session.get(AdminOnlyTool, (upstream_id, tool_name)):
                # Hidden from the member's tool list; a direct call by name is refused too.
                error = f"'{name}' is restricted to admins"
            else:
                target = UpstreamTarget.from_model(upstream)
    if error is None and not (allowance := tool_calls.allow(caller.user_id))[0]:
        error = f"rate limit exceeded: too many tool calls, retry in {allowance[1]:.0f}s"

    # 2. The upstream call, with no database connection held.
    if error is not None or target is None:
        error = error or "upstream could not be resolved"
        result = _error_result(error)
    else:
        upstream_name = target.name
        try:
            result = await call_tool(target, tool_name, arguments)
            if result.is_error:
                error = _first_text(result)
        except UpstreamError as exc:
            error = str(exc)
            result = _error_result(f"{target.name} is unavailable: {exc}")

    # 3. Exactly one audit row, in its own short session.
    async with SessionLocal() as session:
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


GET_KEEPALIVE_SECONDS = float(os.getenv("MCP_GET_KEEPALIVE_SECONDS", "15"))


async def _keepalive_stream(scope: dict[str, Any], receive: Any, send: Any) -> None:
    """An SSE stream that only carries keep-alive comments, until the client disconnects."""
    await send({
        "type": "http.response.start",
        "status": 200,
        "headers": [(b"content-type", b"text/event-stream"), (b"cache-control", b"no-cache, no-transform"),
                    (b"connection", b"keep-alive"), (b"x-accel-buffering", b"no")],
    })
    disconnected = asyncio.Event()

    async def watch_disconnect() -> None:
        while not disconnected.is_set():
            message = await receive()
            if message["type"] == "http.disconnect":
                disconnected.set()

    watcher = asyncio.create_task(watch_disconnect())
    try:
        while not disconnected.is_set():
            await send({"type": "http.response.body", "body": b": keepalive\n\n", "more_body": True})
            try:
                await asyncio.wait_for(disconnected.wait(), timeout=GET_KEEPALIVE_SECONDS)
            except TimeoutError:
                continue
    except Exception as exc:  # the client went away mid-send; nothing to clean up
        log.debug("GET stream ended: %s", exc)
    finally:
        watcher.cancel()
        with contextlib.suppress(Exception):
            await send({"type": "http.response.body", "body": b"", "more_body": False})


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
        if scope.get("method") == "GET":
            # Clients such as Claude Code open this standalone GET stream after initialize and keep it for
            # the life of the session. This server is stateless and never pushes a message on it, and the
            # SDK's default stream is silent forever: proxies and port mappings cut silent connections, and
            # the client then treats the gateway as gone and drops its tools (seen live). So the gateway
            # serves the stream itself and sends an SSE comment every few seconds to keep it alive.
            await _keepalive_stream(scope, receive, send)
            return
        scope.setdefault("state", {})["caller"] = caller
        await self.app(scope, receive, send)
