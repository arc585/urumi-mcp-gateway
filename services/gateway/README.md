# Gateway service

The team's single MCP endpoint (`/mcp`) and the dashboard API (`/api/v1`). Full explanation:
`docs/05-gateway-and-access-control.md`.

| Module | Responsibility |
|---|---|
| `app/main.py` | App wiring, stateless Streamable HTTP mount, DB wait, one-time seeding (bootstrap admin, default upstreams), health |
| `app/auth.py` | API-key authentication shared by `/mcp` and `/api/v1` (HMAC lookup, revoked/inactive rejection) |
| `app/gateway_mcp.py` | MCP server side: aggregation, `<upstream>__<tool>` naming, routing, audit, per-request caller |
| `app/upstream_client.py` | MCP client side: timeouts, error classification, tool-list cache, credential headers |
| `app/upstream_url.py` | SSRF checks for admin-entered upstream URLs |
| `app/security.py` | Key generation/hashing (HMAC + pepper), Fernet encryption of upstream credentials |
| `app/api.py` | Members, keys, upstreams, live status, tool audit, admin events (admin checks server-side) |
| `app/chat.py` | Dashboard chat tool loop (finished in Milestone 6) |

## API summary (`/api/v1`, `Authorization: Bearer urm_…`)

Anyone: `GET /me`, `GET|POST /me/keys`, `POST /me/keys/{id}/revoke`, `GET /upstreams` (members: no URLs),
`GET /upstreams/status`, `GET /audit` (members: own calls).
Admin: `GET|POST /users`, `PATCH /users/{id}`, `GET|POST /users/{id}/keys`, `POST /keys/{id}/revoke`,
`POST /users/{id}/revoke-keys`, `POST /upstreams`, `PATCH|DELETE /upstreams/{id}`, `GET /admin-events`.

## Configuration (environment)

| Variable | Source | Purpose |
|---|---|---|
| `DATABASE_URL` | built from Secret | Postgres (`postgresql+asyncpg://…`) |
| `API_KEY_PEPPER`, `UPSTREAM_ENCRYPTION_KEY` | Secret | key hashing, credential encryption |
| `BOOTSTRAP_ADMIN_EMAIL/NAME/KEY` | values + Secret | first admin, seeded once |
| `DEFAULT_UPSTREAMS`, `SHIPPING_INFO_CREDENTIAL` | chart | upstreams seeded once (credential stored encrypted) |
| `INTERNAL_UPSTREAM_HOSTS` | chart | SSRF allow-list of internal MCP Service names |
| `UPSTREAM_*_SECONDS` | values | discovery / call / connect timeouts, tool cache |
| `MCP_ALLOWED_HOSTS`, `MCP_ALLOWED_ORIGINS` | chart | DNS-rebinding protection, CORS for browser MCP clients |
| `AUDIT_RETENTION_DAYS` | values | delete tool-call audit rows older than this, hourly (0 keeps everything) |
| `MAX_REQUEST_BODY_BYTES` | values | refuse larger request bodies with 413 (0 disables) |
| `UPSTREAM_URL_CHECK_CACHE_SECONDS` | default 60 | how long a URL's passing SSRF verdict is reused |

## Tests

```sh
.venv/bin/python -m pytest services/gateway/tests -q
```

Starts the gateway (SQLite), two demo upstreams, a slow upstream, and a dead address as real processes, then checks
auth, aggregation, isolation, timeouts, credential secrecy, audit attribution, revoke, disable, admin-only endpoints,
SSRF, the last-admin guard, tool permissions, rate limits, the chat loop (fake model), that slow upstreams
never exhaust the database pool, that a restart does not re-seed, and that old audit rows are pruned; plus unit
tests for the token bucket and the SSRF URL check.
