"""HTTP input and output shapes. Secret material (key hashes, credentials) never appears in outputs."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

Role = Literal["admin", "member"]
UPSTREAM_ID_PATTERN = r"^[a-z0-9][a-z0-9-]{1,62}$"


class KeyCreate(BaseModel):
    name: str = Field(default="default", min_length=1, max_length=80)


class KeyView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    prefix: str
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class KeyCreated(KeyView):
    api_key: str = Field(description="Shown once. Only a hash is stored.")


class UserCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+$")
    role: Role = "member"


class UserUpdate(BaseModel):
    role: Role | None = None
    active: bool | None = None


class UserView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    display_name: str
    email: str
    role: str
    active: bool
    created_at: datetime
    active_keys: int = 0


class UserCreated(UserView):
    api_key: str = Field(description="The new member's first key. Shown once.")


class UpstreamCreate(BaseModel):
    id: str = Field(pattern=UPSTREAM_ID_PATTERN, description="Also the tool-name prefix: <id>__<tool>")
    name: str = Field(min_length=1, max_length=120)
    url: HttpUrl
    enabled: bool = True
    auth_header: str | None = Field(default=None, max_length=120)
    credential: str | None = Field(default=None, max_length=4096)
    # Tool names (without the <id>__ prefix) that only admins may see and call.
    admin_only_tools: list[str] = Field(default_factory=list, max_length=200)


class UpstreamUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    url: HttpUrl | None = None
    enabled: bool | None = None
    auth_header: str | None = Field(default=None, max_length=120)
    # Write-only: a new value replaces the stored credential; "" removes it; omitted keeps it.
    credential: str | None = Field(default=None, max_length=4096)
    # Replaces the set of admin-only tools when present.
    admin_only_tools: list[str] | None = Field(default=None, max_length=200)


class UpstreamView(BaseModel):
    """What admins see. Note: no credential value, only whether one is stored."""

    id: str
    name: str
    url: str
    enabled: bool
    auth_header: str | None
    has_credential: bool
    admin_only_tools: list[str] = []
    updated_at: datetime


class UpstreamPublicView(BaseModel):
    """What members see: no URLs or credential details."""

    id: str
    name: str
    enabled: bool


class UpstreamStatus(BaseModel):
    id: str
    name: str
    enabled: bool
    status: Literal["Connected", "Unreachable", "Disabled"]
    tools: list[str]
    admin_only_tools: list[str] = []  # shown to admins; members never see restricted tools at all
    error: str | None = None
    latency_ms: int | None = None
    checked_at: datetime | None = None


class AuditView(BaseModel):
    id: str
    user_id: str
    user_email: str
    channel: str
    upstream_id: str
    upstream_name: str
    tool_name: str
    occurred_at: datetime
    duration_ms: int
    succeeded: bool
    error: str | None


class AdminEventView(BaseModel):
    id: str
    actor_email: str
    action: str
    target: str
    detail: str | None
    occurred_at: datetime
