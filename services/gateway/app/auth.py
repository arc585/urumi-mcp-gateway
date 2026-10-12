"""Authenticate API keys for both the MCP endpoint and the dashboard API.

Every request is checked against the database, so a revoked key or deactivated user is rejected
on the very next request (no cached sessions to wait out).
"""

from dataclasses import dataclass
from datetime import UTC, timedelta

from sqlalchemy import select

from .db import SessionLocal
from .models import ApiKey, User, now_utc
from .security import KEY_PREFIX, hash_api_key

LAST_USED_WRITE_INTERVAL = timedelta(seconds=60)


@dataclass(frozen=True)
class Caller:
    """The authenticated identity attached to one request."""

    user_id: str
    email: str
    display_name: str
    role: str
    key_id: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def extract_key(headers: dict[str, str]) -> str | None:
    """Read "Authorization: Bearer <key>" (standard for MCP clients) or "X-API-Key: <key>"."""
    authorization = headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip() or None
    return headers.get("x-api-key", "").strip() or None


async def authenticate(raw_key: str | None) -> Caller | None:
    if not raw_key or not raw_key.startswith(KEY_PREFIX) or len(raw_key) > 200:
        return None
    key_hash = hash_api_key(raw_key)
    async with SessionLocal() as session:
        row = (
            await session.execute(
                select(ApiKey, User)
                .join(User, User.id == ApiKey.user_id)
                .where(ApiKey.key_hash == key_hash, ApiKey.revoked_at.is_(None), User.active.is_(True))
            )
        ).first()
        if row is None:
            return None
        api_key, user = row
        now = now_utc()
        last_used = api_key.last_used_at
        if last_used is not None and last_used.tzinfo is None:  # SQLite drops tz info; values are UTC
            last_used = last_used.replace(tzinfo=UTC)
        if last_used is None or now - last_used > LAST_USED_WRITE_INTERVAL:
            api_key.last_used_at = now
            await session.commit()
        return Caller(user.id, user.email, user.display_name, user.role, api_key.id)
