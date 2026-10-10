# 08 — Security hardening (stand-out items)

Three additions after the definition of done passed: per-role tool permissions, deny-by-default
NetworkPolicies with egress control, and per-user rate limits.

## 1. Tool permissions (per role)

An admin marks individual tools **admins only** (dashboard → MCP servers → Edit → *Tool permissions*, or
`PATCH /api/v1/upstreams/{id}` with `admin_only_tools`). For members such a tool is:

- **hidden** from `tools/list` (external MCP clients), from the assistant's tool list, and from the dashboard's
  server status;
- **refused** if called by name anyway: `'woocommerce__update_order_status' is restricted to admins`, audited as a
  failed call.

| Piece | Where |
|---|---|
| Table `admin_only_tools (upstream_id, tool_name)` — no row = every role | `services/gateway/app/models.py` |
| Filter in `list_tools_for`, check in `call_tool_as` (one place for MCP *and* chat) | `services/gateway/app/gateway_mcp.py` |
| API + admin event `upstream.tool_policy` | `services/gateway/app/api.py` |
| Default seeded once: `woocommerce: [update_order_status]` (`gateway.toolPolicies.adminOnly`) | `values.yaml`, `main.py` |

Why `create_product` stays open by default: the definition of done has a **member** (user A) create a product
through the assistant. An admin can restrict it with one click (shown in `DEMO_CHECKLIST.md` §9). A new table was
used instead of a new column because the gateway creates missing tables at start-up but has no migrations.

Verified live: member sees 7 tools, admin 8; the member's direct call is refused and audited; the member's
assistant answered "None of the currently available tools support changing an order status".

## 2. NetworkPolicies that deny by default, including egress

Before: each component had an *ingress* policy (who may connect to it). Now (`templates/networkpolicy-default-deny.yaml`):

1. `urumi-default-deny` selects every Pod and denies **all ingress and egress**.
2. `urumi-allow-dns`: every Pod may reach CoreDNS on port 53.
3. Explicit egress per component; ingress policies stay as before.

| Pod | May connect to |
|---|---|
| gateway | Postgres:5432, the three MCP servers:8000, **public internet on 443 only** (LLM, external MCP servers) |
| WooCommerce MCP | WordPress:80 only |
| WordPress, setup Job | MariaDB:3306, public internet on 80/443 (WordPress.org downloads/updates) |
| demo MCPs, dashboard, MariaDB, Postgres | DNS only |

"Public internet" = `0.0.0.0/0` except `networkPolicies.privateCidrs` (10/8, 172.16/12, 192.168/16, 169.254/16
incl. cloud metadata, 100.64/10, 127/8).

Verified from inside the Pods:

```text
gateway → urumi-postgres:5432 OPEN, MCP servers:8000 OPEN, generativelanguage.googleapis.com:443 OPEN
gateway → urumi-wordpress:80, urumi-mariadb:3306, 169.254.169.254:80, 10.96.0.1:443 (Kubernetes API), 1.1.1.1:80  BLOCKED
woocommerce-mcp → urumi-wordpress:80 OPEN; mariadb, postgres, gateway, 1.1.1.1:443  BLOCKED
```

This closes the SSRF "check-then-connect" gap: even if a hostile upstream URL slipped past the application check
(e.g. by DNS rebinding), the gateway's packets to private ranges and metadata are dropped by the network.
It also limits a compromised MCP server to the one thing it needs.

Notes: Kubernetes evaluates egress rules on the destination **Pod** after Service translation, so rules name Pods,
not Service IPs. Health probes come from the kubelet on the node and keep working. On k3s the same policies apply
(its embedded network-policy controller); DNS Pods carry the same `k8s-app: kube-dns` label. RBAC: none of our
Pods talk to the Kubernetes API, and none mount a service-account token (`automountServiceAccountToken: false`),
so there is no API permission to minimise — and egress to the API server is blocked anyway.

## 3. Per-user rate limits

Token bucket per user (`services/gateway/app/ratelimit.py`): **60 tool calls/min** (MCP clients and the assistant
together) and **10 assistant messages/min** (`gateway.rateLimits`). A bucket allows a burst up to the limit and
refills continuously. Over the limit:

- tool call → `is_error` "rate limit exceeded: too many tool calls, retry in Ns" (audited);
- chat → HTTP 429 with `Retry-After`.

Verified live: 64 rapid calls → 62 allowed (60 + refill), 2 refused. Limitation: buckets live in each gateway
replica's memory; with N replicas the effective limit is up to N×; a shared store (Redis) would make it global.

## Tests

`services/gateway/tests`: seeded admin-only tool hidden from members; admin-only tool hidden, refused, and audited,
admins unaffected, members cannot change policies; the assistant only offers a member their tools; per-user tool
and chat limits (independent buckets, `Retry-After`). 29 tests in total. `scripts/verify_dod.py` adds live checks
for the tool permission and the gateway's egress.
