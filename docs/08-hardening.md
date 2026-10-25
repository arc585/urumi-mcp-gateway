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

## 4. One slow upstream must not exhaust the database pool (0.8.1)

`call_tool_as` used to keep its database session open across the upstream call. A session that has run a query
holds one pooled connection until it commits, and the pool has 15 connections (SQLAlchemy's default 5 + 10
overflow). Fifteen callers waiting on one slow upstream therefore pinned every connection, and the next request
of any kind, including `authenticate()` and the readiness probe, blocked until those calls timed out: one slow
upstream could take the gateway out of its Service. The call now uses one short session for the policy checks,
none during the upstream call, and another for the audit row; the dashboard's status probe got the same change.
Regression test: 20 concurrent calls into a slow upstream while a healthy call and a dashboard request must
answer in under 1.5 s (they waited 2.9 s before the fix).

## 5. Production TLS details (0.8.1)

Two gaps only appear with `ingress.tls.enabled`, which Kind never exercises:

- cert-manager's HTTP-01 solver Pod runs in the `urumi` namespace and was selected by `default-deny`, so Traefik
  could never reach the challenge and no certificate would ever be issued. `urumi-allow-acme-solver` admits the
  ingress controller to Pods labelled `acme.cert-manager.io/http01-solver` on port 8089.
- Traefik keeps serving plain HTTP for a host that also has TLS, so a key sent to `http://app.<domain>/mcp`
  travelled in cleartext. Two Traefik Middlewares (`templates/ingress-tls.yaml`), attached to every Ingress,
  redirect HTTP to HTTPS (308) and add `Strict-Transport-Security`.

## 6. Pod Security Admission (namespace labels)

Kubernetes enforces the Pod Security Standards per namespace through three labels. `urumi` is labelled
`enforce=baseline` (no privileged Pods, host namespaces, hostPath, or added capabilities) and `warn=restricted` /
`audit=restricted` (non-root, all capabilities dropped, seccomp set). Every workload in the chart meets
`restricted` except WordPress, whose official image starts Apache as root before dropping to www-data; it draws
a warning on every apply, not a rejection. The labels are applied when the namespace is created (README,
`docs/PRODUCTION.md`), not by the chart, because Helm does not own the release namespace. Applied on the local
cluster: a server-side dry run of the whole chart warns only for the `wordpress` container (capabilities not dropped,
not non-root), and nothing is rejected under `baseline`.

## 7. Scheduled backups

`backups.enabled` (on in both values files) dumps Postgres and MariaDB and archives `wp-content` onto a dedicated
PVC, keeping `backups.keepDays` of history: nightly (CronJob) and before every `helm upgrade`/`rollback` (hook Job),
with a mount-only init Job on first install so the volume binds and `helm --wait` can finish on
WaitForFirstConsumer storage classes (`docs/OPERATIONS.md`). The backup Pod can reach only the two databases,
runs as the database images' own users with every capability dropped, and mounts the WordPress volume read-only.

## 8. Request body limit

`gateway.maxRequestBodyBytes` (1 MiB) is enforced by the gateway itself: an ASGI middleware reads the body up to
the limit and answers 413 beyond it (tool arguments and chat messages are a few KB). It was first done with a
Traefik `buffering` Middleware on the Ingress, which broke external MCP clients: buffering holds a response until
it is complete, and Claude Code keeps a long-lived GET stream open on `/mcp` for server messages, so the client
dropped the server after its first call (found during the demo rehearsal; the Python client never opens that
stream, which is why the automated checks passed). Lesson: proxy middlewares that buffer are incompatible with
streaming endpoints. Tested: a 2 MiB POST → 413, normal requests unaffected.

The deeper cause, found the same day: the SDK answers a GET on `/mcp` by opening a server-sent-events stream and
holding it open forever with nothing on it, even in stateless mode where no server-initiated message can ever
arrive. Clients such as Claude Code open that stream and keep it; a silent connection is exactly what proxies and
port mappings eventually cut, and the client treated the cut as the server going away and dropped its tools
mid-session (every gateway restart did the same). The gateway now serves that GET itself: an
event stream carrying only a keep-alive comment every 15 s, so a client that insists on holding the stream never
holds a silent one, while the key check still applies. (A 405, which the specification also allows, was tried
first; some clients treat it as a failure.) Tested.

## 9. Audit retention and token usage

Tool-call audit rows older than `gateway.auditRetentionDays` (90) are deleted hourly by the gateway itself; admin
events are kept. The assistant returns each message's token usage (`usage` in the response, shown next to the
model name in the UI, and logged with the user id), so cost per user is visible without a metrics stack.

## 10. Image scanning

CI scans every pushed image with Trivy (report only, HIGH and CRITICAL, unfixed ignored). The first scan of 0.8.2
found 3 HIGH in each Python image (Debian base) and 68 findings in the dashboard image, all Alpine OS packages
in the older `nginx-unprivileged:1.28-alpine` base with fixes available; 0.8.3 moves to `1.30-alpine`. Base-image
bumps are the routine response: the application code is not where these CVEs live.

## Tests

`services/gateway/tests`: seeded admin-only tool hidden from members; admin-only tool hidden, refused, and audited,
admins unaffected, members cannot change policies; the assistant only offers a member their tools; per-user tool
and chat limits (independent buckets, `Retry-After`). 45 tests in total (end-to-end plus unit tests for the token bucket and the SSRF check, a restart/re-seed test, and
audit pruning). `services/woocommerce-mcp/tests`: 13 tests against a fake WooCommerce REST API (REST key +
`X-Forwarded-Proto`, link rewriting, store-local day bounds for "today", data minimisation, readable errors, Host
check). `scripts/verify_dod.py` adds live checks
for the tool permission and the gateway's egress.
