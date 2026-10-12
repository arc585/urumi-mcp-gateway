"""The gateway as an MCP *client*: discover and call tools on upstream servers, with isolation.

Isolation rules:
- every upstream operation has a hard deadline covering connect + initialize + the request;
- failures become one short, human-readable reason (never a stack trace or a credential);
- redirects are not followed (an approved URL must not bounce the gateway somewhere else);
- tool lists are cached briefly, so a hanging upstream cannot slow every tools/list.
"""

import asyncio
import os
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx2
from mcp import Client, MCPError
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, Tool

from .models import Upstream
from .security import decrypt_upstream_credential
from .upstream_url import validate_upstream_url

DISCOVERY_TIMEOUT_SECONDS = float(os.getenv("UPSTREAM_DISCOVERY_TIMEOUT_SECONDS", "5"))
CALL_TIMEOUT_SECONDS = float(os.getenv("UPSTREAM_CALL_TIMEOUT_SECONDS", "30"))
# Reaching the upstream at all must be quick: a Service with no running Pods can make connection
# attempts hang, and that must not cost the caller the whole call budget.
CONNECT_TIMEOUT_SECONDS = float(os.getenv("UPSTREAM_CONNECT_TIMEOUT_SECONDS", "5"))
TOOL_CACHE_SECONDS = float(os.getenv("UPSTREAM_TOOL_CACHE_SECONDS", "15"))
FAILURE_CACHE_SECONDS = float(os.getenv("UPSTREAM_FAILURE_CACHE_SECONDS", "5"))
# A URL that passed the SSRF check is trusted for this long before its DNS is resolved again. The
# gateway's egress NetworkPolicy is the layer that actually stops rebinding; this only saves lookups.
URL_CHECK_CACHE_SECONDS = float(os.getenv("UPSTREAM_URL_CHECK_CACHE_SECONDS", "60"))


class UpstreamError(Exception):
    """An upstream could not be used. str(exc) is safe to show users and to store in the audit log."""


@dataclass(frozen=True)
class UpstreamTarget:
    """Plain copy of the fields needed to reach an upstream (independent of any DB session)."""

    id: str
    name: str
    url: str
    auth_header: str | None
    credential_ciphertext: str | None

    @classmethod
    def from_model(cls, upstream: Upstream) -> "UpstreamTarget":
        return cls(upstream.id, upstream.name, upstream.url, upstream.auth_header, upstream.credential_ciphertext)

    @property
    def fingerprint(self) -> tuple[str, str | None, str | None]:
        return (self.url, self.auth_header, self.credential_ciphertext)


@dataclass
class Discovery:
    """Result of asking one upstream for its tools."""

    tools: list[Tool] = field(default_factory=list)
    error: str | None = None
    latency_ms: int | None = None
    checked_at: float = field(default_factory=time.time)

    @property
    def reachable(self) -> bool:
        return self.error is None


Fingerprint = tuple[str, str | None, str | None]
_cache: dict[str, tuple[Fingerprint, float, Discovery]] = {}
_inflight: dict[tuple[str, Fingerprint], asyncio.Task] = {}  # one probe per upstream at a time
_background: set[asyncio.Task] = set()
_url_checks: dict[str, float] = {}  # url -> monotonic time until which its passing SSRF verdict is trusted


def invalidate(upstream_id: str) -> None:
    _cache.pop(upstream_id, None)


async def _check_url(url: str) -> None:
    """SSRF check, with passing verdicts cached briefly (a failing one is re-checked every time)."""
    now = time.monotonic()
    if _url_checks.get(url, 0.0) > now:
        return
    unsafe = await asyncio.to_thread(validate_upstream_url, url)
    if unsafe:
        raise UpstreamError(f"URL blocked: {unsafe}")
    if len(_url_checks) > 1000:
        _url_checks.clear()
    _url_checks[url] = now + URL_CHECK_CACHE_SECONDS


def _leaves(exc: BaseException) -> Iterator[BaseException]:
    """Flatten exception groups (anyio task groups wrap errors) and follow causes."""
    if isinstance(exc, BaseExceptionGroup):
        for inner in exc.exceptions:
            yield from _leaves(inner)
        return
    seen = 0
    current: BaseException | None = exc
    while current is not None and seen < 5:
        yield current
        current = current.__cause__ or current.__context__
        seen += 1


def describe_error(exc: BaseException, timeout: float) -> str:
    leaves = list(_leaves(exc))
    for leaf in leaves:
        if isinstance(leaf, httpx2.ConnectTimeout):
            return f"could not connect within {CONNECT_TIMEOUT_SECONDS:g}s (server down or unreachable)"
    for leaf in leaves:
        if isinstance(leaf, (TimeoutError, httpx2.TimeoutException)):
            return f"timed out after {timeout:g}s"
    for leaf in leaves:
        if isinstance(leaf, httpx2.HTTPStatusError):
            code = leaf.response.status_code
            if code in (401, 403):
                return f"upstream rejected the gateway's credentials (HTTP {code})"
            return f"upstream returned HTTP {code}"
        if isinstance(leaf, httpx2.ConnectError):
            return "connection failed (server down or unreachable)"
        if isinstance(leaf, httpx2.HTTPError):
            return f"HTTP error talking to upstream ({type(leaf).__name__})"
        if isinstance(leaf, MCPError):
            return f"MCP protocol error: {str(leaf)[:200]}"
        if isinstance(leaf, UpstreamError):
            return str(leaf)
    return f"{type(exc).__name__}: upstream request failed"


def _headers(target: UpstreamTarget) -> dict[str, str]:
    credential = decrypt_upstream_credential(target.credential_ciphertext)
    if credential and target.auth_header:
        return {target.auth_header: credential}
    return {}


@asynccontextmanager
async def _session(target: UpstreamTarget, timeout: float) -> AsyncIterator[Client]:
    await _check_url(target.url)
    async with httpx2.AsyncClient(
        headers=_headers(target),
        timeout=httpx2.Timeout(timeout, connect=min(CONNECT_TIMEOUT_SECONDS, timeout)),
        follow_redirects=False,
    ) as http:
        async with Client(
            streamable_http_client(target.url, http_client=http),
            read_timeout_seconds=timeout,
            raise_exceptions=True,
        ) as client:
            yield client


async def discover_tools(target: UpstreamTarget, *, use_cache: bool = True) -> Discovery:
    """List an upstream's tools. Never raises: failures become Discovery.error.

    Cached per upstream. An expired entry is answered immediately from the last result while one
    background probe refreshes it (stale-while-revalidate), so neither a dead upstream nor a crowd of
    simultaneous tools/list calls makes anyone wait. The dashboard's status view passes
    use_cache=False to check live; concurrent live checks still share one probe (see _probe).
    """
    now = time.monotonic()
    cached = _cache.get(target.id)
    if use_cache and cached and cached[0] == target.fingerprint:
        if cached[1] <= now:
            task = asyncio.create_task(_probe(target))
            _background.add(task)
            task.add_done_callback(_background.discard)
        return cached[2]
    return await _probe(target)


async def _probe(target: UpstreamTarget) -> Discovery:
    """Single-flight: callers that arrive while a probe of this upstream is running await that probe
    instead of each opening their own MCP session to the same server."""
    key = (target.id, target.fingerprint)
    task = _inflight.get(key)
    if task is None:
        task = asyncio.create_task(_probe_now(target))
        _inflight[key] = task
        task.add_done_callback(lambda done: _inflight.pop(key, None) if _inflight.get(key) is done else None)
    return await asyncio.shield(task)  # a cancelled caller must not cancel the shared probe


async def _probe_now(target: UpstreamTarget) -> Discovery:
    started = time.perf_counter()
    try:
        async with asyncio.timeout(DISCOVERY_TIMEOUT_SECONDS):
            async with _session(target, DISCOVERY_TIMEOUT_SECONDS) as client:
                result = await client.list_tools()
        discovery = Discovery(tools=list(result.tools))
    except Exception as exc:  # one failed upstream must not affect the others
        discovery = Discovery(error=describe_error(exc, DISCOVERY_TIMEOUT_SECONDS))
    discovery.latency_ms = round((time.perf_counter() - started) * 1000)
    ttl = TOOL_CACHE_SECONDS if discovery.reachable else FAILURE_CACHE_SECONDS
    _cache[target.id] = (target.fingerprint, time.monotonic() + ttl, discovery)
    return discovery


async def call_tool(target: UpstreamTarget, name: str, arguments: dict[str, Any]) -> CallToolResult:
    """Call one tool. Returns the upstream's result (which may itself be is_error) or raises UpstreamError."""
    try:
        async with asyncio.timeout(CALL_TIMEOUT_SECONDS):
            async with _session(target, CALL_TIMEOUT_SECONDS) as client:
                return await client.call_tool(name, arguments)
    except Exception as exc:
        invalidate(target.id)  # force a fresh health check next time
        raise UpstreamError(describe_error(exc, CALL_TIMEOUT_SECONDS)) from exc
