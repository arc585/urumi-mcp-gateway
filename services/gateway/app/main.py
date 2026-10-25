"""Gateway service: the dashboard API (/api/v1) and the team's single MCP endpoint (/mcp)."""

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator
from typing import Any
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from mcp.server.transport_security import TransportSecuritySettings
from sqlalchemy import delete, select, text
from starlette.middleware import Middleware

from . import models  # noqa: F401  (registers ORM tables before create_all)
from .api import router
from .chat import router as chat_router
from .db import Base, SessionLocal, engine
from .gateway_mcp import MCPAuthMiddleware, gateway_server
from .models import AdminOnlyTool, ApiKey, Setting, ToolAudit, Upstream, User, now_utc
from .security import encrypt_upstream_credential, hash_api_key

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("urumi.gateway")


class _DropProbeHits(logging.Filter):
    """Keep kubelet probe requests out of the access log: two every 10 s per Pod, and they say nothing."""

    def filter(self, record: logging.LogRecord) -> bool:
        return "/health/" not in record.getMessage()


logging.getLogger("uvicorn.access").addFilter(_DropProbeHits())


def _csv(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


async def _seed_once(session, marker: str) -> bool:
    """True the first time `marker` is seen; afterwards admins' changes are never overwritten."""
    if await session.get(Setting, marker):
        return False
    session.add(Setting(key=marker, value="done"))
    return True


async def _seed() -> None:
    async with SessionLocal() as session:
        # 1. First admin, from the Kubernetes Secret. Seeded once; manage further people in the dashboard.
        admin_key = os.getenv("BOOTSTRAP_ADMIN_KEY", "")
        admin_email = os.getenv("BOOTSTRAP_ADMIN_EMAIL", "").strip().lower()
        if admin_key and admin_email and await _seed_once(session, "bootstrap_admin"):
            admin = await session.scalar(select(User).where(User.email == admin_email))
            if admin is None:
                admin = User(email=admin_email, display_name=os.getenv("BOOTSTRAP_ADMIN_NAME", "Admin"), role="admin")
                session.add(admin)
                await session.flush()
            session.add(ApiKey(user_id=admin.id, name="bootstrap", key_hash=hash_api_key(admin_key), prefix=admin_key[:8]))
            log.info("Seeded bootstrap admin %s", admin_email)

        # 2. Default upstreams (JSON from the Helm chart). A credential is read from the env var named
        #    in "credential_env" (itself from a Secret) and stored encrypted.
        defaults = json.loads(os.getenv("DEFAULT_UPSTREAMS", "[]"))
        if defaults and await _seed_once(session, "default_upstreams"):
            for item in defaults:
                if await session.get(Upstream, item["id"]):
                    continue
                credential = os.getenv(item.get("credential_env", ""), "") if item.get("credential_env") else ""
                session.add(
                    Upstream(
                        id=item["id"],
                        name=item["name"],
                        url=item["url"],
                        enabled=True,
                        auth_header=item.get("auth_header") if credential else None,
                        credential_ciphertext=encrypt_upstream_credential(credential) if credential else None,
                    )
                )
                log.info("Seeded upstream %s", item["id"])
        await session.flush()

        # 3. Default tool permissions, e.g. {"woocommerce": ["update_order_status"]}: admin-only tools.
        #    Seeded once (also on existing installs); admins change them in the dashboard afterwards.
        policies = json.loads(os.getenv("DEFAULT_ADMIN_ONLY_TOOLS", "{}"))
        if policies and await _seed_once(session, "default_tool_policies"):
            for upstream_id, tools in policies.items():
                if await session.get(Upstream, upstream_id) is None:
                    continue
                for tool in tools:
                    if await session.get(AdminOnlyTool, (upstream_id, tool)) is None:
                        session.add(AdminOnlyTool(upstream_id=upstream_id, tool_name=tool))
                log.info("Seeded admin-only tools for %s: %s", upstream_id, tools)
        await session.commit()


async def _wait_for_database(timeout_seconds: float = 120) -> None:
    """On a fresh install Postgres may start after the gateway (its headless Service has no DNS
    record until the Pod is ready). Retry instead of crash-looping."""
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    delay = 1.0
    while True:
        try:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            return
        except Exception as exc:
            if asyncio.get_running_loop().time() > deadline:
                raise
            log.warning("Database not reachable yet (%s); retrying in %.0fs", type(exc).__name__, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 10)


SCHEMA_LOCK_ID = 20261010  # any constant; every replica must use the same one


async def _initialise_schema_and_seed() -> None:
    """Create missing tables and seed defaults, one replica at a time.

    Several replicas starting against an empty database would all run create_all and insert the same
    seed markers; the losers' commits fail and those Pods restart once. A Postgres session-level
    advisory lock, held on its own connection across both steps, makes replicas take turns: the next
    one finds the markers and does nothing. SQLite (tests, local development) has no advisory locks
    and no replicas.
    """
    async with engine.connect() as lock_connection:
        locked = lock_connection.dialect.name == "postgresql"
        if locked:
            await lock_connection.execute(text("SELECT pg_advisory_lock(:id)"), {"id": SCHEMA_LOCK_ID})
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            await _seed()
        finally:
            if locked:
                await lock_connection.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": SCHEMA_LOCK_ID})


AUDIT_RETENTION_DAYS = int(os.getenv("AUDIT_RETENTION_DAYS", "90"))
AUDIT_PRUNE_INTERVAL_SECONDS = float(os.getenv("AUDIT_PRUNE_INTERVAL_SECONDS", "3600"))


async def prune_audit(retention_days: int = AUDIT_RETENTION_DAYS) -> int:
    """Delete tool-call audit rows older than the retention window; returns how many went.

    Admin events are kept: they are few and security-relevant. 0 days disables pruning.
    """
    if retention_days <= 0:
        return 0
    cutoff = now_utc() - timedelta(days=retention_days)
    async with SessionLocal() as session:
        result = await session.execute(delete(ToolAudit).where(ToolAudit.occurred_at < cutoff))
        await session.commit()
        return result.rowcount or 0


async def _prune_loop() -> None:
    while True:
        try:
            removed = await prune_audit()
            if removed:
                log.info("Pruned %d tool-audit rows older than %d days", removed, AUDIT_RETENTION_DAYS)
        except Exception:  # never let housekeeping take the gateway down
            log.exception("Audit pruning failed")
        await asyncio.sleep(AUDIT_PRUNE_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await _wait_for_database()
    await _initialise_schema_and_seed()
    pruner = asyncio.create_task(_prune_loop())
    try:
        async with gateway_server.session_manager.run():
            yield
    finally:
        pruner.cancel()
        await engine.dispose()


MAX_REQUEST_BODY_BYTES = int(os.getenv("MAX_REQUEST_BODY_BYTES", "1048576"))


class MaxBodyMiddleware:
    """Refuse request bodies above MAX_REQUEST_BODY_BYTES with 413 (0 disables the limit).

    Enforced here and not with a Traefik "buffering" middleware: buffering holds a response until it
    is complete, which breaks the long-lived GET stream MCP clients such as Claude Code keep open on
    /mcp for server messages (found live: the client dropped the server after its first call).
    The body is read up to the limit and replayed to the application, so chunked requests without a
    Content-Length are limited too.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http" or MAX_REQUEST_BODY_BYTES <= 0:
            await self.app(scope, receive, send)
            return
        too_large = JSONResponse({"detail": f"request body too large (limit {MAX_REQUEST_BODY_BYTES} bytes)"}, status_code=413)
        declared = dict(scope.get("headers") or []).get(b"content-length", b"")
        if declared.isdigit() and int(declared) > MAX_REQUEST_BODY_BYTES:
            await too_large(scope, receive, send)
            return
        chunks: list[bytes] = []
        total = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                return  # client went away before sending a body
            chunk = message.get("body", b"")
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_REQUEST_BODY_BYTES:
                await too_large(scope, receive, send)
                return
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)
        replayed = False

        async def replay() -> dict[str, Any]:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()  # afterwards: disconnect notifications, as usual

        await self.app(scope, replay, send)


allowed_hosts = _csv("MCP_ALLOWED_HOSTS", "localhost:*,127.0.0.1:*")
allowed_origins = _csv("MCP_ALLOWED_ORIGINS", "http://localhost:*,http://127.0.0.1:*")

# Stateless Streamable HTTP: no server-side MCP session, so every request is authenticated on its
# own and any gateway replica can serve it. JSON responses keep proxies and clients simple.
mcp_app = gateway_server.streamable_http_app(
    stateless_http=True,
    json_response=True,
    transport_security=TransportSecuritySettings(allowed_hosts=allowed_hosts, allowed_origins=allowed_origins),
)

app = FastAPI(
    title="Urumi Team MCP Gateway",
    description="One authenticated MCP endpoint for a team's MCP servers, plus the dashboard API.",
    version=os.getenv("APP_VERSION", "dev"),
    lifespan=lifespan,
    middleware=[Middleware(MaxBodyMiddleware), Middleware(MCPAuthMiddleware)],
)
# Browser-based MCP clients (e.g. MCP Inspector in direct mode) need CORS; the dashboard is same-origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin for origin in allowed_origins if "*" not in origin],
    allow_origin_regex=r"^http://(localhost|127\.0\.0\.1):\d+$",
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "Mcp-Protocol-Version", "Mcp-Session-Id", "Last-Event-ID", "X-API-Key"],
    expose_headers=["Mcp-Session-Id"],
)
app.include_router(router)
app.include_router(chat_router)


@app.get("/health/live", tags=["health"])
async def liveness() -> dict[str, str]:
    return {"status": "alive"}


@app.get("/health/ready", tags=["health"])
async def readiness() -> JSONResponse:
    """Ready only when the database answers; upstream health is reported separately, per upstream."""
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse({"status": "not-ready", "reason": "database unavailable"}, status_code=503)
    return JSONResponse({"status": "ready"})


app.mount("/", mcp_app)
