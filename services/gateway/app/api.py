"""Dashboard API (/api/v1): members and keys, upstream servers, health status, and audit logs.

Authorization is enforced here, on the server. The dashboard hiding a button is a convenience,
not a control: every admin endpoint depends on `require_admin`.
"""

import asyncio
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import Caller, authenticate, extract_key
from .db import SessionLocal, get_session
from .models import AdminEvent, AdminOnlyTool, ApiKey, ToolAudit, Upstream, User, now_utc
from .schemas import (
    AdminEventView,
    AuditView,
    KeyCreate,
    KeyCreated,
    KeyView,
    UpstreamCreate,
    UpstreamPublicView,
    UpstreamStatus,
    UpstreamUpdate,
    UpstreamView,
    UserCreate,
    UserCreated,
    UserUpdate,
    UserView,
)
from .security import create_api_key, encrypt_upstream_credential, hash_api_key
from .upstream_client import UpstreamTarget, discover_tools, invalidate
from .upstream_url import validate_upstream_url

router = APIRouter(prefix="/api/v1")
HEADER_NAME = re.compile(r"^[A-Za-z0-9-]{1,120}$")


# --------------------------------------------------------------------------- auth dependencies


async def current_caller(request: Request) -> Caller:
    raw_key = extract_key({key.lower(): value for key, value in request.headers.items()})
    caller = await authenticate(raw_key)
    if caller is None:
        detail = "API key required" if not raw_key else "Invalid or revoked API key"
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail, headers={"WWW-Authenticate": "Bearer"})
    return caller


def require_admin(caller: Caller = Depends(current_caller)) -> Caller:
    if not caller.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin role required")
    return caller


def record(session: AsyncSession, caller: Caller, action: str, target: str, detail: str | None = None) -> None:
    session.add(AdminEvent(actor_id=caller.user_id, action=action, target=target, detail=detail))


async def _new_key(session: AsyncSession, user_id: str, name: str) -> tuple[ApiKey, str]:
    raw = create_api_key()
    key = ApiKey(user_id=user_id, name=name.strip(), key_hash=hash_api_key(raw), prefix=raw[:8])
    session.add(key)
    await session.flush()
    return key, raw


def _key_created(key: ApiKey, raw: str) -> KeyCreated:
    return KeyCreated(**KeyView.model_validate(key).model_dump(), api_key=raw)


async def _user_view(session: AsyncSession, user: User) -> UserView:
    count = await session.scalar(
        select(func.count()).select_from(ApiKey).where(ApiKey.user_id == user.id, ApiKey.revoked_at.is_(None))
    )
    return UserView.model_validate(user).model_copy(update={"active_keys": count or 0})


# --------------------------------------------------------------------------- me


@router.get("/me", response_model=UserView)
async def me(caller: Caller = Depends(current_caller), session: AsyncSession = Depends(get_session)) -> UserView:
    user = await session.get(User, caller.user_id)
    return await _user_view(session, user)


@router.get("/me/keys", response_model=list[KeyView])
async def my_keys(caller: Caller = Depends(current_caller), session: AsyncSession = Depends(get_session)) -> list[ApiKey]:
    rows = await session.scalars(select(ApiKey).where(ApiKey.user_id == caller.user_id).order_by(ApiKey.created_at))
    return list(rows.all())


@router.post("/me/keys", response_model=KeyCreated, status_code=status.HTTP_201_CREATED)
async def create_my_key(
    data: KeyCreate, caller: Caller = Depends(current_caller), session: AsyncSession = Depends(get_session)
) -> KeyCreated:
    """Any member can generate personal keys, e.g. one for the browser and one for Claude Code."""
    key, raw = await _new_key(session, caller.user_id, data.name)
    record(session, caller, "key.created", caller.email, f"key {key.prefix}… ({key.name})")
    await session.commit()
    return _key_created(key, raw)


@router.post("/me/keys/{key_id}/revoke", response_model=KeyView)
async def revoke_my_key(
    key_id: str, caller: Caller = Depends(current_caller), session: AsyncSession = Depends(get_session)
) -> ApiKey:
    key = await session.get(ApiKey, key_id)
    if key is None or key.user_id != caller.user_id:
        raise HTTPException(404, "Key not found")
    if key.revoked_at is None:
        key.revoked_at = now_utc()
        record(session, caller, "key.revoked", caller.email, f"key {key.prefix}… ({key.name})")
        await session.commit()
    return key


# --------------------------------------------------------------------------- team (admin)


@router.get("/users", response_model=list[UserView])
async def list_users(_: Caller = Depends(require_admin), session: AsyncSession = Depends(get_session)) -> list[UserView]:
    users = (await session.scalars(select(User).order_by(User.created_at))).all()
    return [await _user_view(session, user) for user in users]


@router.post("/users", response_model=UserCreated, status_code=status.HTTP_201_CREATED)
async def create_user(
    data: UserCreate, caller: Caller = Depends(require_admin), session: AsyncSession = Depends(get_session)
) -> UserCreated:
    email = data.email.strip().lower()
    if await session.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(409, "A user with this email already exists")
    user = User(display_name=data.display_name.strip(), email=email, role=data.role)
    session.add(user)
    await session.flush()
    key, raw = await _new_key(session, user.id, "initial")
    record(session, caller, "user.invited", email, f"role={data.role}, key {key.prefix}…")
    await session.commit()
    view = await _user_view(session, user)
    return UserCreated(**view.model_dump(), api_key=raw)


async def _admin_count(session: AsyncSession) -> int:
    return await session.scalar(
        select(func.count()).select_from(User).where(User.role == "admin", User.active.is_(True))
    ) or 0


@router.patch("/users/{user_id}", response_model=UserView)
async def update_user(
    user_id: str, data: UserUpdate, caller: Caller = Depends(require_admin), session: AsyncSession = Depends(get_session)
) -> UserView:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(404, "User not found")
    losing_admin = user.role == "admin" and user.active and (data.role == "member" or data.active is False)
    if losing_admin and await _admin_count(session) <= 1:
        raise HTTPException(409, "The workspace must keep at least one active admin")
    if user.id == caller.user_id and data.active is False:
        raise HTTPException(409, "You cannot deactivate yourself")
    changes = data.model_dump(exclude_unset=True, exclude_none=True)
    for field, value in changes.items():
        setattr(user, field, value)
    record(session, caller, "user.updated", user.email, ", ".join(f"{k}={v}" for k, v in changes.items()))
    await session.commit()
    return await _user_view(session, user)


@router.get("/users/{user_id}/keys", response_model=list[KeyView])
async def user_keys(
    user_id: str, _: Caller = Depends(require_admin), session: AsyncSession = Depends(get_session)
) -> list[ApiKey]:
    rows = await session.scalars(select(ApiKey).where(ApiKey.user_id == user_id).order_by(ApiKey.created_at))
    return list(rows.all())


@router.post("/users/{user_id}/keys", response_model=KeyCreated, status_code=status.HTTP_201_CREATED)
async def issue_key(
    user_id: str, data: KeyCreate, caller: Caller = Depends(require_admin), session: AsyncSession = Depends(get_session)
) -> KeyCreated:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(404, "User not found")
    key, raw = await _new_key(session, user.id, data.name)
    record(session, caller, "key.issued", user.email, f"key {key.prefix}… ({key.name})")
    await session.commit()
    return _key_created(key, raw)


@router.post("/keys/{key_id}/revoke", response_model=KeyView)
async def revoke_key(
    key_id: str, caller: Caller = Depends(require_admin), session: AsyncSession = Depends(get_session)
) -> ApiKey:
    key = await session.get(ApiKey, key_id)
    if key is None:
        raise HTTPException(404, "Key not found")
    if key.revoked_at is None:
        owner = await session.get(User, key.user_id)
        key.revoked_at = now_utc()
        record(session, caller, "key.revoked", owner.email if owner else key.user_id, f"key {key.prefix}… ({key.name})")
        await session.commit()
    return key


@router.post("/users/{user_id}/revoke-keys", response_model=UserView)
async def revoke_all_keys(
    user_id: str, caller: Caller = Depends(require_admin), session: AsyncSession = Depends(get_session)
) -> UserView:
    """Revoke every key a user has; their very next request is rejected with 401."""
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(404, "User not found")
    if user.id == caller.user_id:
        raise HTTPException(409, "Revoke your own keys individually so you do not lock yourself out")
    keys = (await session.scalars(select(ApiKey).where(ApiKey.user_id == user.id, ApiKey.revoked_at.is_(None)))).all()
    now = now_utc()
    for key in keys:
        key.revoked_at = now
    record(session, caller, "key.revoked_all", user.email, f"{len(keys)} key(s)")
    await session.commit()
    return await _user_view(session, user)


# --------------------------------------------------------------------------- upstreams


TOOL_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


async def _restricted(session: AsyncSession) -> dict[str, list[str]]:
    rows = (await session.execute(select(AdminOnlyTool.upstream_id, AdminOnlyTool.tool_name))).all()
    restricted: dict[str, list[str]] = {}
    for upstream_id, tool in rows:
        restricted.setdefault(upstream_id, []).append(tool)
    return {key: sorted(value) for key, value in restricted.items()}


async def _set_admin_only(session: AsyncSession, upstream_id: str, tools: list[str]) -> list[str]:
    clean = sorted({tool.strip() for tool in tools if tool.strip()})
    if any(not TOOL_NAME.fullmatch(tool) for tool in clean):
        raise HTTPException(422, "admin_only_tools must be plain tool names, e.g. update_order_status")
    for row in (await session.scalars(select(AdminOnlyTool).where(AdminOnlyTool.upstream_id == upstream_id))).all():
        await session.delete(row)
    for tool in clean:
        session.add(AdminOnlyTool(upstream_id=upstream_id, tool_name=tool))
    return clean


def _upstream_view(upstream: Upstream, admin_only: list[str] | None = None) -> UpstreamView:
    return UpstreamView(
        id=upstream.id,
        name=upstream.name,
        url=upstream.url,
        enabled=upstream.enabled,
        auth_header=upstream.auth_header,
        has_credential=bool(upstream.credential_ciphertext),
        admin_only_tools=admin_only or [],
        updated_at=upstream.updated_at,
    )


@router.get("/upstreams", response_model=list[UpstreamView] | list[UpstreamPublicView])
async def list_upstreams(
    caller: Caller = Depends(current_caller), session: AsyncSession = Depends(get_session)
) -> list[UpstreamView] | list[UpstreamPublicView]:
    rows = (await session.scalars(select(Upstream).order_by(Upstream.name))).all()
    if caller.is_admin:
        restricted = await _restricted(session)
        return [_upstream_view(row, restricted.get(row.id)) for row in rows]
    return [UpstreamPublicView(id=row.id, name=row.name, enabled=row.enabled) for row in rows]


async def _check_url(url: str) -> None:
    error = await asyncio.to_thread(validate_upstream_url, url)
    if error:
        raise HTTPException(422, error)


def _check_header(header: str | None) -> str | None:
    header = header.strip() if header else None
    if header and not HEADER_NAME.fullmatch(header):
        raise HTTPException(422, "auth_header must be a valid HTTP header name, e.g. Authorization")
    return header


def _encrypt(credential: str) -> str:
    try:
        return encrypt_upstream_credential(credential)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.post("/upstreams", response_model=UpstreamView, status_code=status.HTTP_201_CREATED)
async def add_upstream(
    data: UpstreamCreate, caller: Caller = Depends(require_admin), session: AsyncSession = Depends(get_session)
) -> UpstreamView:
    if await session.get(Upstream, data.id):
        raise HTTPException(409, "Upstream ID already exists")
    await _check_url(str(data.url))
    header = _check_header(data.auth_header)
    if data.credential and not header:
        raise HTTPException(422, "auth_header is required when a credential is provided")
    upstream = Upstream(
        id=data.id,
        name=data.name.strip(),
        url=str(data.url),
        enabled=data.enabled,
        auth_header=header,
        credential_ciphertext=_encrypt(data.credential) if data.credential else None,
    )
    session.add(upstream)
    await session.flush()
    admin_only = await _set_admin_only(session, upstream.id, data.admin_only_tools)
    record(session, caller, "upstream.added", data.id,
           f"{upstream.url} credential={'yes' if data.credential else 'no'} admin_only={admin_only or 'none'}")
    await session.commit()
    await session.refresh(upstream)
    return _upstream_view(upstream, admin_only)


@router.patch("/upstreams/{upstream_id}", response_model=UpstreamView)
async def edit_upstream(
    upstream_id: str,
    data: UpstreamUpdate,
    caller: Caller = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> UpstreamView:
    upstream = await session.get(Upstream, upstream_id)
    if upstream is None:
        raise HTTPException(404, "Upstream not found")
    changes = data.model_dump(exclude_unset=True)
    summary: list[str] = []
    if changes.get("name"):
        upstream.name = changes["name"].strip()
        summary.append(f"name={upstream.name}")
    if changes.get("url"):
        await _check_url(str(changes["url"]))
        upstream.url = str(changes["url"])
        summary.append(f"url={upstream.url}")
    if changes.get("enabled") is not None:
        upstream.enabled = changes["enabled"]
        summary.append(f"enabled={upstream.enabled}")
    if "auth_header" in changes:
        upstream.auth_header = _check_header(changes["auth_header"])
        summary.append(f"auth_header={upstream.auth_header}")
    if "credential" in changes and changes["credential"] is not None:
        upstream.credential_ciphertext = _encrypt(changes["credential"]) if changes["credential"] else None
        summary.append("credential=replaced" if changes["credential"] else "credential=removed")
    if upstream.credential_ciphertext and not upstream.auth_header:
        raise HTTPException(422, "auth_header is required while a credential is stored")
    if changes.get("admin_only_tools") is not None:
        admin_only = await _set_admin_only(session, upstream.id, changes["admin_only_tools"])
        summary.append(f"admin_only={','.join(admin_only) or 'none'}")
    action = "upstream.disabled" if changes.get("enabled") is False else (
        "upstream.enabled" if changes.get("enabled") is True and len(summary) == 1 else
        "upstream.tool_policy" if list(changes) == ["admin_only_tools"] else "upstream.edited")
    record(session, caller, action, upstream.id, ", ".join(summary) or None)
    await session.commit()
    await session.refresh(upstream)
    invalidate(upstream.id)
    return _upstream_view(upstream, (await _restricted(session)).get(upstream.id))


@router.delete("/upstreams/{upstream_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_upstream(
    upstream_id: str, caller: Caller = Depends(require_admin), session: AsyncSession = Depends(get_session)
) -> None:
    upstream = await session.get(Upstream, upstream_id)
    if upstream is None:
        raise HTTPException(404, "Upstream not found")
    await _set_admin_only(session, upstream_id, [])
    await session.delete(upstream)
    record(session, caller, "upstream.removed", upstream_id)
    await session.commit()
    invalidate(upstream_id)


@router.get("/upstreams/status", response_model=list[UpstreamStatus])
async def upstream_status(caller: Caller = Depends(current_caller)) -> list[UpstreamStatus]:
    """Live health of every upstream, checked concurrently (each check has its own timeout).

    Members do not see the names of admin-only tools; admins see which tools are restricted.
    The session is closed before the probes start, so a 5 s health check never pins a pooled
    connection (same reasoning as call_tool_as).
    """
    async with SessionLocal() as session:
        rows = (await session.scalars(select(Upstream).order_by(Upstream.name))).all()
        restricted = await _restricted(session)
        targets = {row.id: UpstreamTarget.from_model(row) for row in rows}

    async def inspect(upstream: Upstream) -> UpstreamStatus:
        if not upstream.enabled:
            return UpstreamStatus(id=upstream.id, name=upstream.name, enabled=False, status="Disabled", tools=[])
        discovery = await discover_tools(targets[upstream.id], use_cache=False)
        hidden = set() if caller.is_admin else set(restricted.get(upstream.id, []))
        return UpstreamStatus(
            id=upstream.id,
            name=upstream.name,
            enabled=True,
            status="Connected" if discovery.reachable else "Unreachable",
            tools=[tool.name for tool in discovery.tools if tool.name not in hidden],
            admin_only_tools=restricted.get(upstream.id, []) if caller.is_admin else [],
            error=discovery.error,
            latency_ms=discovery.latency_ms,
            checked_at=datetime.fromtimestamp(discovery.checked_at, tz=timezone.utc),
        )

    return list(await asyncio.gather(*(inspect(row) for row in rows)))


# --------------------------------------------------------------------------- audit


@router.get("/audit", response_model=list[AuditView])
async def list_audit(
    limit: int = 100,
    user_id: str | None = None,
    upstream_id: str | None = None,
    caller: Caller = Depends(current_caller),
    session: AsyncSession = Depends(get_session),
) -> list[AuditView]:
    """Admins see everyone's tool calls; members see only their own."""
    if not 1 <= limit <= 500:
        raise HTTPException(422, "limit must be between 1 and 500")
    query = select(ToolAudit, User.email).join(User, User.id == ToolAudit.user_id)
    if not caller.is_admin:
        query = query.where(ToolAudit.user_id == caller.user_id)
    elif user_id:
        query = query.where(ToolAudit.user_id == user_id)
    if upstream_id:
        query = query.where(ToolAudit.upstream_id == upstream_id)
    rows = (await session.execute(query.order_by(ToolAudit.occurred_at.desc()).limit(limit))).all()
    return [
        AuditView(
            id=audit.id,
            user_id=audit.user_id,
            user_email=email,
            channel=audit.channel,
            upstream_id=audit.upstream_id,
            upstream_name=audit.upstream_name,
            tool_name=audit.tool_name,
            occurred_at=audit.occurred_at,
            duration_ms=audit.duration_ms,
            succeeded=audit.succeeded,
            error=audit.error,
        )
        for audit, email in rows
    ]


@router.get("/admin-events", response_model=list[AdminEventView])
async def list_admin_events(
    limit: int = 100, _: Caller = Depends(require_admin), session: AsyncSession = Depends(get_session)
) -> list[AdminEventView]:
    if not 1 <= limit <= 500:
        raise HTTPException(422, "limit must be between 1 and 500")
    rows = (
        await session.execute(
            select(AdminEvent, User.email)
            .join(User, User.id == AdminEvent.actor_id)
            .order_by(AdminEvent.occurred_at.desc())
            .limit(limit)
        )
    ).all()
    return [
        AdminEventView(
            id=event.id, actor_email=email, action=event.action, target=event.target,
            detail=event.detail, occurred_at=event.occurred_at,
        )
        for event, email in rows
    ]
