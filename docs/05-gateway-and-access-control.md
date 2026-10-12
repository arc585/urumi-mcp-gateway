# 05 — The gateway and access control (Milestone 4)

## What the gateway is

One service (`services/gateway`, FastAPI + MCP Python SDK) that is **both**:

- an **MCP server** to people: one URL, `http://app.urumi.test:8880/mcp` (prod: `https://app.<domain>/mcp`),
  authenticated with a personal API key;
- an **MCP client** to upstreams: WooCommerce MCP, Store Info, and Shipping Info, reached over the cluster network.

Nothing else may talk to an upstream: NetworkPolicies admit only Pods labelled `component=gateway`.

```text
Claude Code / Cursor / Inspector / dashboard chat
   │  POST /mcp  Authorization: Bearer urm_…           (Ingress app.urumi.test → Service urumi-gateway)
   ▼
MCPAuthMiddleware ── HMAC(key, pepper) → api_keys row, not revoked, user active? ── no → 401
   │ yes: Caller{user, role, key} attached to *this* HTTP request
   ▼
tools/list → every enabled upstream, concurrently (cached; dead ones answered from cache)
           → publish "<upstream-id>__<tool>"
tools/call "woocommerce__get_order" → split at first "__" → upstream "woocommerce" enabled?
           → decrypt its credential (if any) → new MCP client session → call "get_order"
           → write tool_audit row (user, key, channel, upstream, tool, time, duration, outcome)
           → return the upstream's result (or a clear is_error result)
```

## Key decisions (and why)

| Decision | Why |
|---|---|
| Tool names `<upstream-id>__<tool>` | Deterministic collision handling: ids are unique and match `^[a-z0-9][a-z0-9-]{1,62}$` (no `_`), so splitting at the first `__` always finds the upstream. Two servers can both have `search`. |
| Identity read from the **request**, not shared state | The SDK hands each handler `ctx.request` (the HTTP request carrying that message). The scaffold used a context variable, which can leak the first caller's identity into a long-lived session. Test: 10 interleaved calls from two users, all attributed correctly. |
| **Stateless** Streamable HTTP + JSON responses | Every request is authenticated on its own, a revoked key fails on the *next* request, and any replica can serve any request (ready for >1 replica). |
| API keys: random 256-bit `urm_…`, stored as **HMAC-SHA256 with a pepper** | Keys are high-entropy, so a fast keyed hash suffices; without the pepper (in a Secret, not the DB) a leaked DB cannot even test guesses. Shown once; only a prefix (`urm_rm8N…`) is kept for display. |
| **Several named keys per user** | "laptop", "claude-code"… revoke one without breaking the others. Admin "revoke user" revokes all of them. |
| Upstream credentials **Fernet-encrypted** in Postgres | AES + HMAC with a key from a Secret. Decrypted only in memory for the server-to-server call; APIs return only `has_credential`. |
| Client keys are **never forwarded** upstream | Upstreams see the gateway's credential, not the member's key. A malicious upstream cannot replay member keys, and revoking a member never requires touching upstreams. |
| Timeouts: discovery 5 s, call 30 s, **connect 5 s** | A Service with no Pods made connections *hang* (not refuse); without a separate connect timeout a dead upstream cost 30 s. |
| Failed upstream answered **from cache**, re-checked in background | MCP clients list tools before their first call; a dead upstream added 5–6 s to every client. Now healthy calls stay at 0.7–1 s while one upstream is dead. |
| SSRF guard on admin-entered URLs | Only exact internal Service names (from the chart) may be HTTP/internal; everything else must be HTTPS and resolve to public IPs only; no redirects followed; no credentials in URLs. |
| Admin actions audited too | The definition of done says "all preceding actions" appear in the audit log: invites, key issue/revoke, upstream add/edit/disable/remove. |
| Separate Postgres for the gateway | Different trust domain from the store's MariaDB. Waits for the DB at startup instead of crash-looping. |
| **Database sessions never span an upstream call** | A session that has run a query pins one pooled connection until it commits, and the pool holds 15. Left open across a 30 s upstream call, 15 callers on one slow upstream would block every other request, including authentication and the readiness probe. `call_tool_as` reads policy in one short session, calls the upstream holding none, and audits in another; the status probe does the same (`docs/08-hardening.md` §4). |
| Start-up takes a Postgres advisory lock | Several replicas starting on an empty database would all create tables and insert the seed markers; the losers crash once. `pg_advisory_lock` on a dedicated connection makes them take turns (SQLite, used in tests, has neither replicas nor the lock). |
| Discovery is single-flight and stale-while-revalidate | Concurrent `tools/list` calls and dashboard tabs share one probe per upstream; an expired cache entry is answered at once while one background probe refreshes it. A URL's passing SSRF verdict is reused for 60 s (the egress NetworkPolicy is the real rebinding control). |

## Kubernetes resources added

`urumi-gateway` Deployment/Service/Ingress (`/api`, `/mcp` on `app.urumi.test`), `urumi-postgres` StatefulSet + PVC,
`urumi-store-info-mcp` and `urumi-shipping-info-mcp` Deployments/Services, Secret `urumi-gateway`
(pepper, Fernet key, bootstrap admin key, Postgres password, shipping token), and NetworkPolicies:

| Target | Who may connect |
|---|---|
| gateway :8000 | ingress controller only |
| postgres :5432 | gateway only |
| woocommerce / store-info / shipping-info MCP :8000 | gateway only |
| wordpress :80 | ingress controller, WooCommerce MCP |
| mariadb :3306 | WordPress, setup Job |

## Build and deploy

```sh
.venv/bin/python -m pytest services/gateway/tests -q        # 20 end-to-end tests (real processes)
for s in gateway demo-mcp; do docker build -f services/$s/Dockerfile -t urumi-$s:dev .; done
kind load docker-image urumi-gateway:dev urumi-demo-mcp:dev --name urumi
helm upgrade urumi deploy/helm/urumi -n urumi -f deploy/helm/urumi/values-local.yaml --wait --wait-for-jobs
# Rebuilt the same :dev tag? Kubernetes cannot see the change; restart:
kubectl -n urumi rollout restart deploy/urumi-gateway
```

## Using it

```sh
# Your admin key (prints only in your terminal):
kubectl -n urumi get secret urumi-gateway -o jsonpath='{.data.bootstrap-admin-key}' | base64 -d; echo

# Create a member (returns their first key once):
curl -s -X POST -H "Authorization: Bearer $ADMIN_KEY" -H 'Content-Type: application/json' \
  -d '{"display_name":"User B","email":"b@example.test"}' http://app.urumi.test:8880/api/v1/users

# Any MCP check from the terminal (key from the environment, not the command line):
MCP_API_KEY=urm_… .venv/bin/python scripts/mcp_call.py http://app.urumi.test:8880/mcp
```

Connect an external client with only the URL and a key:

- **Claude Code:** `claude mcp add --transport http urumi http://app.urumi.test:8880/mcp --header "Authorization: Bearer urm_…"`
- **MCP Inspector:** `npx @modelcontextprotocol/inspector` → Transport *Streamable HTTP*, URL
  `http://app.urumi.test:8880/mcp`, header `Authorization: Bearer urm_…`.
- **Cursor** (`.cursor/mcp.json`): `{"mcpServers":{"urumi":{"url":"http://app.urumi.test:8880/mcp","headers":{"Authorization":"Bearer urm_…"}}}}`

## What we verified in the cluster

| Check | Result |
|---|---|
| No key / bogus key on `/mcp` | 401 with `WWW-Authenticate: Bearer` |
| Admin `tools/list` | 8 tools: `woocommerce__*` (5), `store-info__*` (2), `shipping-info__*` (1) |
| Member B: today's orders; then order → shipping estimate (two upstreams) | worked |
| Revoke B | B: 200 → **401** on the next request; A unaffected |
| Disable `store-info` | its tools vanish, status `Disabled`, calls refused; re-enable restores |
| Scale shipping to 0 | status `Unreachable`; shipping call fails in ~5 s with a clear reason; WooCommerce calls 0.7–1 s |
| Audit | every call attributed (user, channel, upstream, tool, ms, outcome, error); admin events for invite/revoke/disable/enable |
| Postgres contents | key **hashes** + prefixes only; shipping credential is Fernet ciphertext `gAAAAA…` |
| Random Pod → gateway / postgres / demo MCPs | all **BLOCKED** |
| Restart Postgres and gateway together | gateway logs "Database not reachable yet… retrying", then starts; 0 restarts |

## Zero-downtime rollouts

All web Deployments (gateway, dashboard, MCP servers) have a `preStop` hook that sleeps 5 s, so a terminating Pod
keeps serving until Traefik and the Service endpoints stop routing to it. Without it, a request during a rollout
got an empty response; with it, 150/150 requests succeeded while the gateway rolled.

## Known limits (documented, not hidden)

- `create_all` instead of migrations: fine for this version; schema changes would need Alembic.
- Tool lists are cached up to 15 s, so an upstream that *adds* a tool appears within 15 s; disabling is immediate.
- Clients are not pushed `tools/list_changed` (stateless mode); they see changes when they next list tools.
- DNS rebinding between the SSRF check and the connection is possible in theory; the gateway's egress NetworkPolicy
  (`docs/08-hardening.md`) is the second layer that closes it at the network level.

## Interview check

- Walk a `tools/call` from Claude Code through Ingress, auth, routing, credential decryption, upstream, audit.
- Why is the identity taken from `ctx.request`, and what bug would a context variable cause?
- Why HMAC + pepper for keys but Fernet (reversible) for upstream credentials?
- Why aren't member keys passed to upstreams?
- How does one dead upstream stay isolated — in listing, in calls, and in the dashboard?
- What does the SSRF guard block, and what is its known gap?
