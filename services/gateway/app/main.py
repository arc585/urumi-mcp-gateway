"""Gateway service: the dashboard API (/api/v1) and the team's single MCP endpoint (/mcp)."""

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from mcp.server.transport_security import TransportSecuritySettings
from sqlalchemy import select, text
from starlette.middleware import Middleware

from . import models  # noqa: F401  (registers ORM tables before create_all)
from .api import router
from .chat import router as chat_router
from .db import Base, SessionLocal, engine
from .gateway_mcp import MCPAuthMiddleware, gateway_server
from .models import AdminOnlyTool, ApiKey, Setting, Upstream, User
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


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await _wait_for_database()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await _seed()
    async with gateway_server.session_manager.run():
        yield
    await engine.dispose()


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
    middleware=[Middleware(MCPAuthMiddleware)],
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
