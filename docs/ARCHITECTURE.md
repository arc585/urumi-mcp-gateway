# System design and trade-offs

## Components and responsibilities

```text
                 Internet / laptop
   Shopper ─────────────┐        Admin / Member (browser)     External MCP client (Claude Code, Cursor, Inspector)
                        │                 │                                 │  Authorization: Bearer urm_…
                        ▼                 ▼                                 ▼
             ┌──────────────────────── Traefik (Ingress controller) ────────────────────────┐
             │ store.<domain>/        app.<domain>/            app.<domain>/api   app.<domain>/mcp │
             └──────┬─────────────────────┬──────────────────────────┬────────────────┬──────┘
                    ▼                     ▼                          ▼                ▼
              WordPress +            Dashboard (nginx,          ┌────────── Gateway (FastAPI) ───────────┐
              WooCommerce            static React app)          │ auth (hashed keys) · admin API · audit │
              (Deployment, PVC)                                 │ MCP server (one endpoint, aggregation) │
                    │                                           │ MCP client (routing, timeouts, creds)  │
                    ▼                                           │ chat agent (LLM tool loop, as user)    │
              MariaDB (StatefulSet, PVC)                        └───┬──────────┬──────────┬──────┬───────┘
                    ▲                                               │          │          │      │ HTTPS
                    │ REST /wp-json/wc/v3 (key from Secret)         ▼          ▼          ▼      ▼
              WooCommerce MCP  ◄──────────── MCP/HTTP ──── woocommerce   store-info  shipping   Gemini API
              (our server)                                  __*          MCP __*     MCP __*   (OpenAI-compatible)
                                                      Postgres (StatefulSet, PVC): users, key hashes,
                                                      encrypted upstream credentials, audit
```

| Component | Kubernetes resources | Responsibility |
|---|---|---|
| Store | WordPress `Deployment` (Recreate) + PVC; MariaDB `StatefulSet` + PVC; setup `Job` (Helm hook); Ingress | The WooCommerce shop and its data |
| WooCommerce MCP | `Deployment` + ClusterIP `Service`, no Ingress | Our MCP server: 5 tools over WooCommerce's REST API |
| Demo MCPs | 2 × `Deployment` + `Service` (one image) | Store policies; shipping estimates (token-protected) |
| Gateway | `Deployment` + `Service` + Ingress (`/api`, `/mcp`); Postgres `StatefulSet` + PVC | Auth, team/keys, upstream registry, aggregation and routing, audit, chat agent |
| Dashboard | `Deployment` (nginx) + `Service` + Ingress (`/`) | Admin and member UI; holds no secrets |
| Cross-cutting | Deny-by-default `NetworkPolicy` (ingress + egress) with per-component allows, `Secret`s, probes, requests/limits, non-root, `preStop` | Isolation, credentials, health, zero-downtime rollouts |

All components run in one namespace (`urumi`) from one Helm chart (`deploy/helm/urumi`) with `values-local.yaml`
(Kind) and `values-prod.yaml` (k3s).

## Architecture choices and trade-offs

| Choice | Why | Trade-off accepted |
|---|---|---|
| **One chart, one namespace** | One release to install, upgrade, and roll back; shared helpers for names, labels, secrets | Multi-store would need per-store namespaces/releases (the bonus) |
| **Own Helm templates with official images** (no Bitnami) | Every resource is explainable; Bitnami images moved behind a paywall | More YAML to maintain |
| **Traefik** on Kind as on k3s | Same `ingressClassName` locally and in production; ingress-nginx is retired | — |
| **WooCommerce REST API** for the MCP server | Supported, versioned, applies WooCommerce's own logic | Basic auth requires HTTPS → in-cluster `X-Forwarded-Proto: https` (traffic stays on the node network) |
| **Gateway = MCP server + MCP client + API + chat, in one service** | One place for identity, policy, credentials, and audit; the chat cannot bypass it | One service to scale; it is stateless, so replicas are possible |
| **Stateless Streamable HTTP everywhere** | Every request authenticated on its own (revocation is immediate); any replica can answer; Pod restarts lose nothing | No server-initiated messages (progress, `list_changed`); clients re-list tools |
| **New upstream session per call** | Simple, isolates failures, credentials attached per request | Extra connect/initialize latency (~10–50 ms in-cluster) |
| **Postgres for the gateway, separate from MariaDB** | Different trust domain and data; independent backups | Two databases to operate |
| **Keys: random 256-bit, HMAC-SHA256 + pepper** | Fast lookup by hash, useless without the pepper | Rotating the pepper invalidates all keys |
| **Upstream credentials: Fernet (AES + HMAC)** | Must be decryptable to call the upstream; tamper-evident | In-place key rotation not implemented |
| **Chat inside the gateway**, OpenAI-compatible client | Reuses the exact routing/audit path; provider is a Helm value | Free-tier quotas (fallback model configured) |
| **Dashboard on the same host as the API** | No CORS; one TLS certificate; path routing | Two Ingresses share a host (cert requested by one) |

## How tool aggregation, routing, and failure handling work

**Aggregation (`tools/list`).** The gateway loads enabled upstreams from Postgres and asks each for its tools
**concurrently** (`asyncio.gather`), with a 5 s connect+discovery deadline per upstream. Each tool is republished
as `<upstream-id>__<tool>`, e.g. `woocommerce__list_orders`. Results are cached for 15 s; failures are cached and
re-checked in the background, so a dead upstream adds no latency to everyone else's listing.

**Collision scheme.** Upstream ids are unique and match `^[a-z0-9][a-z0-9-]{1,62}$` (no underscores), so splitting
a public name at the first `__` always yields exactly one upstream and its original tool name. Two servers may both
expose `search`: `crm__search` and `docs__search`.

**Routing (`tools/call`).** Split the name → load the upstream → refuse if unknown or disabled → decrypt its
credential → open an MCP session (Streamable HTTP, no redirects, SSRF-checked URL) → call the original tool →
return the upstream's result unchanged (including `isError`) → write one audit row (user, key, channel, upstream,
tool, time, duration, outcome, error).

**Failure handling.**

| Failure | Behaviour |
|---|---|
| Upstream Pod gone (connect hangs or is refused) | 5 s connect timeout → `is_error` "X is unavailable: could not connect within 5s"; status **Unreachable**; others unaffected (0.7–1 s) |
| Upstream slow | 30 s call deadline → "timed out after 30s" |
| Upstream returns a tool error | passed through as `is_error` with its message; audited as failed |
| Upstream disabled | its tools disappear from listings; calls refused "not available"; status **Disabled** |
| Store (WordPress) down | WooCommerce MCP returns "Could not reach the WooCommerce store" / HTTP status; MCP server stays Ready |
| Postgres not ready at gateway start | gateway waits and retries (no crash loop) |
| Rollout of any web Deployment | `preStop` 5 s pause: 150/150 requests succeeded during a gateway rollout |
| LLM provider errors | 429/402/401/404 mapped to clear messages; quota → fallback model on the first call |

## What changes for production (details: `docs/PRODUCTION.md`)

| | Local | Production (same chart) |
|---|---|---|
| DNS | `/etc/hosts` → 127.0.0.1 | A records → VPS IP |
| Ingress | Traefik in `traefik`, ports 8880/8843 | k3s' Traefik in `kube-system`, ports 80/443, TLS via cert-manager/Let's Encrypt |
| Storage class | Kind `standard` | k3s `local-path` (+ backups; or a CSI driver for durable volumes) |
| Secrets | generated by the chart (`lookup` keeps them stable) | pre-created `existingSecret`s; k3s `--secrets-encryption` |
| Images | `kind load`, `:dev` | GHCR, immutable version tags, multi-arch via CI |

## Scaling and evolution

- Gateway and MCP servers are stateless → raise `replicaCount` (sessions are not pinned; the tool cache is per
  replica, which only affects freshness). Start-up is replica-safe: schema creation and seeding run under a
  Postgres advisory lock, and no database session spans an upstream call.
- Many tools: listings grow linearly; a "search tools, then call" meta-tool would keep the LLM context small.
- Per-role tool permissions and per-user rate limits are enforced in `list_tools_for` / `call_tool_as`, the single
  path shared by MCP clients and the assistant (`docs/08-hardening.md`). Rate-limit buckets are per replica; a
  shared store would be needed for exact limits across replicas.
- Multi-store: one release (or set of namespaced resources) per store, auto-registering its WooCommerce MCP as an
  upstream with id `store-<name>`.

## Learning notes

Kubernetes does not understand MCP. It schedules containers, gives them stable names (Services), durable disks
(PVCs), credentials (Secrets), health checks (probes), resource bounds (requests/limits), a front door (Ingress),
and a firewall (NetworkPolicies). The MCP semantics — tools, sessions, routing, identity — live entirely in our
application code.
