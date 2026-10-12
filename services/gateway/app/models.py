"""Relational model: team members, their API keys, upstream MCP servers, and audit records."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def new_id() -> str:
    return str(uuid4())


def now_utc() -> datetime:
    return datetime.now(UTC)


class User(Base):
    """A team member. Role "admin" manages the gateway; role "member" only uses tools."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(16), default="member")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ApiKey(Base):
    """One personal API key. Only an HMAC of the key is stored; the key itself is shown once."""

    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # First characters of the key (e.g. "urm_Ab12"), so people can tell their keys apart.
    prefix: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Upstream(Base):
    """A registered MCP server. Its id is the tool-name prefix: <id>__<tool>."""

    __tablename__ = "upstreams"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    url: Mapped[str] = mapped_column(String(2048))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Optional static credential sent as "<auth_header>: <credential>"; stored encrypted (Fernet).
    auth_header: Mapped[str | None] = mapped_column(String(120), nullable=True)
    credential_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)


class ToolAudit(Base):
    """One tool call through the gateway, attributed to the authenticated user and key."""

    __tablename__ = "tool_audit"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    api_key_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # "mcp" for external MCP clients, "chat" for the dashboard assistant.
    channel: Mapped[str] = mapped_column(String(16), default="mcp")
    upstream_id: Mapped[str] = mapped_column(String(64), index=True)
    upstream_name: Mapped[str] = mapped_column(String(120))
    tool_name: Mapped[str] = mapped_column(String(256))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)
    duration_ms: Mapped[int] = mapped_column(Integer)
    succeeded: Mapped[bool] = mapped_column(Boolean)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class AdminEvent(Base):
    """An administrative change (key issued/revoked, upstream added/edited/disabled/removed, ...)."""

    __tablename__ = "admin_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[str] = mapped_column(String(256))
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)


class Setting(Base):
    """Small key/value table, e.g. remembering that default upstreams were seeded once."""

    __tablename__ = "gateway_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class AdminOnlyTool(Base):
    """A tool that only admins may see and call. No row = available to every role.

    Members don't get it in tools/list (MCP or chat) and a direct call is refused and audited.
    """

    __tablename__ = "admin_only_tools"

    upstream_id: Mapped[str] = mapped_column(ForeignKey("upstreams.id", ondelete="CASCADE"), primary_key=True)
    tool_name: Mapped[str] = mapped_column(String(256), primary_key=True)
