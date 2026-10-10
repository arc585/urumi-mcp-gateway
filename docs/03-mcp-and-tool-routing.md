# 03 — MCP, the WooCommerce MCP server, and tool routing (Milestone 3)

## MCP in plain language

The **Model Context Protocol** is a standard way for AI applications to discover and call tools.

- A **server** advertises **tools**. Each tool has a **name** (`list_orders`), a **description** (what a model reads
  to decide when to use it), and an **input schema** (JSON Schema for its arguments, e.g. `limit: integer 1–100`).
  Our Python SDK builds the schema from type hints such as `Annotated[int, Field(ge=1, le=100)]`.
- A **client** (Claude Code, Cursor, MCP Inspector, or our gateway) talks to it in three steps:
  1. `initialize` — agree on protocol version and capabilities;
  2. `tools/list` — "what can you do?";
  3. `tools/call` — "run `get_order` with `{"order_id": 12}`" → result content, or a result with `is_error: true`.
- Messages are **JSON-RPC 2.0**. **Streamable HTTP** carries them over plain HTTP: the client POSTs to one URL (`/mcp`);
  the server answers with JSON or streams messages back (Server-Sent Events). Because it is ordinary HTTP it goes
  through Kubernetes Services and Ingresses like any web app. (The other standard transport, stdio, means launching
  the server as a local subprocess — fine on a laptop, useless across a cluster.)

## One tool call, end to end (as it runs today)

```text
caller (gateway Pod; in M3 a test Pod labelled as the gateway)
  │ POST http://urumi-woocommerce-mcp:8000/mcp   {"method":"tools/call","params":{"name":"get_order","arguments":{"order_id":12}}}
  ▼
NetworkPolicy urumi-woocommerce-mcp: source Pod has component=gateway? else dropped
  ▼
Service urumi-woocommerce-mcp → MCP server Pod (uvicorn, Starlette, MCPServer)
  │ Host header check (DNS-rebinding protection) → 421 if not our Service name
  │ arguments validated against the tool's JSON Schema
  │ get_order(order_id=12)
  │   GET http://urumi-wordpress/wp-json/wc/v3/orders/12
  │   Basic auth = REST key from Secret urumi-store, + X-Forwarded-Proto: https
  ▼
NetworkPolicy urumi-wordpress: source is the MCP server (or Traefik)? else dropped
  ▼
WordPress/WooCommerce Pod → reads MariaDB (urumi-mariadb-0) → JSON order
  ▲
MCP server trims it (no email/phone/street), converts times to Asia/Kolkata, returns
{"content":[{"type":"text",...}], "structuredContent":{...}, "isError":false}
```

Failures stay readable on the way back: WooCommerce 404 → `WooCommerce returned HTTP 404: Invalid ID.`; slow store →
`WooCommerce did not respond within 15 seconds.`; bad arguments → schema validation error. These come back as
`is_error: true` results, not crashes, so the gateway (M4) can pass them on and audit them as failures.

Service-specific details (tools, REST mapping, why REST, the HTTPS quirk): `services/woocommerce-mcp/README.md`.

## Building and shipping the image

```sh
docker build -f services/woocommerce-mcp/Dockerfile -t urumi-woocommerce-mcp:dev .
kind load docker-image urumi-woocommerce-mcp:dev --name urumi   # copy into the Kind node's image store
helm upgrade urumi deploy/helm/urumi -n urumi -f deploy/helm/urumi/values-local.yaml --wait --wait-for-jobs
```

- `requirements.lock` pins exact versions (what we tested is what ships); `.dockerignore` keeps `.venv`, `.git`
  and `.env` out of the build.
- Kind nodes have their own image store, so local images are *loaded*, not pulled (`imagePullPolicy: IfNotPresent`).
  On the VPS the image comes from a registry (`values-prod.yaml`).
- The container runs as uid 10001 with a read-only root filesystem, no Linux capabilities, and a writable `/tmp`.

## Verifications we ran

| Check | Result |
|---|---|
| Local server + `scripts/mcp_call.py` (list tools) | 5 tools with schemas |
| `list_orders on_date=today` | orders #12 and #11 with IST times |
| `get_order 999` / bad price / bad date / `limit 500` | clear `is_error` messages |
| `create_product` (draft) and `update_order_status 11 → completed` | worked; draft deleted afterwards |
| Random Pod → MCP server :8000 | **BLOCKED** |
| Random Pod → WordPress :80 (bypass attempt) | **BLOCKED** |
| Gateway-labelled Pod → MCP server | tools listed, today's orders returned |
| Forged `Host: evil.example.com` | **421 Misdirected Request** |
| Storefront through Traefik after adding the WordPress policy | 200 |

Bugs found and fixed along the way: tools raised `ValueError` (clients would only see "Error executing tool");
`https://` product links caused by `X-Forwarded-Proto`; version labels on Pod templates restarted MariaDB on every
chart bump (now Pod templates carry only stable selector labels — revision 4 kept identical Pods).

## Inspecting it yourself

```sh
kubectl -n urumi get deploy,svc,networkpolicy -l app.kubernetes.io/component=woocommerce-mcp
kubectl -n urumi describe deploy urumi-woocommerce-mcp      # env shows secretKeyRef, not secret values
kubectl -n urumi logs deploy/urumi-woocommerce-mcp          # POST /mcp, probes, rejected Host headers
helm history urumi -n urumi
# Developer access without the gateway (needs cluster-admin rights; port-forward bypasses NetworkPolicies):
kubectl -n urumi port-forward svc/urumi-woocommerce-mcp 8001:8000
.venv/bin/python scripts/mcp_call.py http://localhost:8001/mcp
```

## Tool naming in the gateway (Milestone 4)

The gateway will publish each upstream tool as `<upstream-id>__<tool>` — e.g. `woocommerce__list_orders`,
`shipping-info__estimate_shipping` — so two servers can both have a `search` tool without colliding, and the prefix
tells the router which upstream to call.

## Interview check

- Walk a `tools/call` from client to MariaDB and back, naming every Kubernetes object on the way.
- Why Streamable HTTP and not stdio? Why stateless?
- Why the REST API instead of SQL? What is the `X-Forwarded-Proto` trade-off?
- What enforces "only the gateway can call the MCP server"? (ClusterIP + no Ingress + NetworkPolicy; and what
  `kubectl port-forward` means for that.)
- Why does `ToolError` matter for clear failures?
