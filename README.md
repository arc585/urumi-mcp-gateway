# Urumi Team MCP Gateway

A WooCommerce store on Kubernetes plus **one authenticated MCP endpoint for a team**: an admin registers MCP
servers once in a dashboard; every member uses all enabled tools through a single URL — from Claude Code, Cursor,
MCP Inspector, or the dashboard's AI assistant — with per-user keys, server-side upstream credentials, isolation,
and a full audit log. Everything runs in-cluster from **one Helm chart** with local (Kind) and production (k3s)
values.

```text
External MCP client ─┐                         ┌─► WooCommerce MCP (ours) ─► WooCommerce REST ─► WordPress + MariaDB
Dashboard chat (LLM) ┼─► Gateway  /mcp  /api ──┼─► Store-info MCP
Dashboard (React) ───┘   auth · routing ·      └─► Shipping MCP (token-protected)
                         audit · credentials
Shopper ───────────────────────────────────────────► Storefront (Ingress)
```

Only the gateway can reach the MCP servers (ClusterIP + NetworkPolicies). Tools are published as
`<server>__<tool>` (e.g. `woocommerce__list_orders`).

## Deliverables

| | |
|---|---|
| Local setup and usage | this README, `docs/RUNBOOK.md` |
| Production-like VPS (k3s) setup | `docs/PRODUCTION.md`, `deploy/helm/urumi/values-prod.yaml`, `deploy/k3s/cluster-issuer.yaml` |
| System design and trade-offs | `docs/ARCHITECTURE.md` |
| Threat model | `docs/THREAT_MODEL.md` |
| Upgrades, rollback, backups | `docs/OPERATIONS.md` |
| Definition-of-done demo | `docs/DEMO_CHECKLIST.md` (manual), `scripts/verify_dod.py` (automated, all checks PASS) |
| Demo video outline | `docs/DEMO_VIDEO.md` |
| Source | `services/gateway` (gateway + chat agent), `services/woocommerce-mcp`, `services/demo-mcp`, `frontend` |
| Helm chart | `deploy/helm/urumi` (`values-local.yaml`, `values-prod.yaml`) |

Learning notes written while building, one per milestone: `docs/01-kubernetes-foundations.md`,
`02-helm-chart.md`, `03-mcp-and-tool-routing.md`, `04-store-on-kubernetes.md`, `05-gateway-and-access-control.md`,
`06-dashboard.md`, `07-chat-agent.md`, `08-hardening.md`.

## Local setup (Kind)

Prerequisites: Docker Desktop, `kubectl`, Helm 4, Kind, Python 3.12, Node 22. Tested with Docker 29.7, Kubernetes
1.37 (Kind 0.33), Helm 4.3.

```sh
# 0. Hostnames (once; needs your password)
echo "127.0.0.1 store.urumi.test app.urumi.test" | sudo tee -a /etc/hosts

# 1. Cluster + ingress controller (ingress published on 127.0.0.1:8880/8843 — 80/443 were taken on the dev Mac)
kind create cluster --config kind-config.yaml
helm repo add traefik https://traefik.github.io/charts && helm repo update
helm upgrade --install traefik traefik/traefik --version 41.7.0 -n traefik --create-namespace \
  -f deploy/kind/traefik-values.yaml --wait

# 2. Images (built locally, loaded into the Kind node)
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.lock pytest
for s in gateway woocommerce-mcp demo-mcp; do docker build -f services/$s/Dockerfile -t urumi-$s:dev .; done
docker build -f frontend/Dockerfile -t urumi-dashboard:dev .
kind load docker-image urumi-gateway:dev urumi-woocommerce-mcp:dev urumi-demo-mcp:dev urumi-dashboard:dev --name urumi
# (if docker build hangs on FROM: sh scripts/load-base-images.sh — Docker Desktop pull workaround)

# 3. LLM key for the assistant (Gemini; input hidden) — optional, the rest works without it
kubectl create namespace urumi
read -s "GEMINI_KEY?Paste Gemini API key: " && kubectl -n urumi create secret generic urumi-llm \
  --from-literal=api-key="$GEMINI_KEY" && unset GEMINI_KEY

# 4. Everything else: store, MCP servers, gateway, Postgres, dashboard
helm upgrade --install urumi deploy/helm/urumi -n urumi -f deploy/helm/urumi/values-local.yaml \
  --wait --wait-for-jobs --timeout 15m

# 5. Verify
.venv/bin/python scripts/verify_dod.py --preflight
```

### Local domain approach

`store.urumi.test` and `app.urumi.test` are mapped to `127.0.0.1` in `/etc/hosts`. `.test` is a reserved TLD
(RFC 6761), so these names can never collide with real domains. Kind publishes the node's ports 80/443 on
`127.0.0.1:8880`/`8843` (`kind-config.yaml`; 80/443 were in use on the development Mac and binding to 127.0.0.1 keeps
the cluster private to the laptop), and Traefik routes by `Host`. WordPress's site URL and the gateway's allowed
hosts/origins are built from the same Helm values (`ingress.publicPort`, `store.host`, `gateway.host`), so changing
the domain or port is a values change. Alternatives considered: `*.nip.io` (needs internet DNS) and `*.localhost`
(not resolved by every MCP client).

| URL | What |
|---|---|
| `http://store.urumi.test:8880` | Storefront; `/wp-admin` for WooCommerce admin |
| `http://app.urumi.test:8880` | Dashboard (sign in with an API key) |
| `http://app.urumi.test:8880/mcp` | The team MCP endpoint (`Authorization: Bearer <key>`) |

Credentials (printed in **your** terminal only):

```sh
kubectl -n urumi get secret urumi-gateway -o jsonpath='{.data.bootstrap-admin-key}' | base64 -d; echo      # first admin key
kubectl -n urumi get secret urumi-store -o jsonpath='{.data.wordpress-admin-password}' | base64 -d; echo   # wp-admin, user admin
```

## Using it

- **Place an order:** storefront → add a product → Checkout → *Cash on delivery*. It appears in wp-admin →
  WooCommerce → Orders.
- **Admin:** sign in to the dashboard with the admin key → *MCP servers* (status, add/edit/disable/remove; the
  WooCommerce server is pre-registered) → *Team access* (invite members; each key is shown once; revoke) → *Audit log*.
- **Connect an external MCP client** with only the gateway URL and a member key:
  - Claude Code: `bash scripts/connect-claude-code.sh` (key at a hidden prompt), then a new Claude Code session in
    this folder; or `claude mcp add --transport http urumi http://app.urumi.test:8880/mcp --header "Authorization: Bearer urm_…"`.
  - MCP Inspector: `npx @modelcontextprotocol/inspector` → Streamable HTTP, URL as above, header `Authorization: Bearer urm_…`.
  - Cursor: snippet on the dashboard's *Keys & clients* page.
- **Chat agent:** dashboard → *AI assistant* (e.g. "Create a product called Blue Mug priced at 299", "Show today's
  orders"). Calls run as the signed-in user and appear in the audit log as *assistant*.

## Production (k3s VPS)

Same chart with `values-prod.yaml`: real DNS and TLS (cert-manager), k3s' Traefik in `kube-system`, `local-path`
storage, pre-created Secrets, GHCR images with immutable tags (built by `.github/workflows/ci.yml` on version tags).
Step-by-step: `docs/PRODUCTION.md`. Upgrades/rollback/backups: `docs/OPERATIONS.md` (rollback demonstrated:
revisions 8 ↔ 10).

## Tests and verification

```sh
.venv/bin/python -m pytest services/gateway/tests -q     # 29 end-to-end tests: real processes, fake LLM
.venv/bin/python scripts/verify_dod.py                    # live definition of done + hardening checks against the cluster
helm lint deploy/helm/urumi -f deploy/helm/urumi/values-prod.yaml
```

## Definition of done — status

All verified live on the local cluster (`scripts/verify_dod.py`):

- Storefront order with Cash on delivery, visible in WooCommerce admin.
- User A: assistant creates a product → on the storefront → bought → "today's orders" returns it.
- User B: external MCP client sees all enabled servers' tools, queries orders, one task across two upstreams.
- Revoke B → next call 401. Disable an upstream → tools disappear for chat and clients.
- Kill an upstream Pod → self-heals; scaled to 0 → dashboard *Unreachable*, others keep working (0.3 s).
- Audit log attributes every call (user, server, tool, time, duration, outcome) and admin action.

## Stand-out items attempted

| Item | What | Where |
|---|---|---|
| Security hardening | SSRF guard for admin-entered URLs (metadata/loopback/private blocked, HTTPS only, no redirects); **deny-by-default NetworkPolicies in both directions** with per-component allows — the gateway cannot reach metadata/private ranges, an MCP server only its backend (verified from inside the Pods); non-root, read-only root FS, dropped capabilities, no service-account tokens (no Kubernetes API access to minimise); upstream credentials encrypted at rest (Fernet); hashed keys with pepper; CSP on the dashboard; DNS-rebinding (Host) checks | `docs/08-hardening.md`, `templates/networkpolicy-default-deny.yaml`, `services/gateway/app/upstream_url.py`, `security.py`, `docs/THREAT_MODEL.md` |
| Tool permissions | Per-role: admins mark tools *admins only* (default `update_order_status`); hidden from members' tool lists (MCP + assistant), refused and audited if called by name | `docs/08-hardening.md`, dashboard → MCP servers → Edit |
| Scaling (partial) | Per-user rate limits (60 tool calls/min, 10 assistant messages/min); stateless gateway ready for replicas (limits are per replica) | `services/gateway/app/ratelimit.py`, `gateway.rateLimits` |
| Upgrades and rollback | Documented and demonstrated `helm rollback` (8 ↔ 10), backup/restore commands verified, zero-downtime rollouts (`preStop`, 150/150 requests during a rollout) | `docs/OPERATIONS.md` |
| Production-like deployment | `values-prod.yaml` (TLS, k3s ingress namespace, local-path, existing Secrets, pull secrets), server-side validated; CI building multi-arch images | `docs/PRODUCTION.md`, `.github/workflows/ci.yml` |
| Observability (partial) | Per-upstream live status with latency and failure reason; audit log with durations and errors | dashboard *MCP servers*, *Audit log* |

Not attempted: multi-store provisioning, OAuth on the gateway, many-tools search, hosted MCP servers, metrics.

## Repository map

```text
services/gateway/         FastAPI: /api (dashboard API), /mcp (aggregated MCP endpoint), chat agent, tests
services/woocommerce-mcp/ our WooCommerce MCP server (5 tools, Streamable HTTP)
services/demo-mcp/        store-info and shipping MCP servers (one image)
frontend/                 React + Vite dashboard (nginx in production)
deploy/helm/urumi/        the Helm chart; values.yaml, values-local.yaml, values-prod.yaml
deploy/kind/              Traefik values for Kind        deploy/k3s/  cert-manager ClusterIssuer
scripts/                  verify_dod.py, mcp_call.py, connect-claude-code.sh, load-base-images.sh
docs/                     design, threat model, operations, production, demo, learning notes
```
