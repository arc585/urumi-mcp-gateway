# WooCommerce MCP server

Our own MCP server (no third-party WooCommerce MCP plugin), written with the official MCP Python SDK
(`mcp==2.3.0`, `MCPServer`). Source: `app/server.py`.

## Tools

| Tool | WooCommerce REST call | Notes |
|---|---|---|
| `list_products(search, status, limit)` | `GET /wc/v3/products` | Newest first; returns id, name, price, status, stock, public URL |
| `create_product(name, price, description, sku, status)` | `POST /wc/v3/products` | Simple product; price validated as a decimal with ≤ 2 places; `publish` shows it on the storefront |
| `list_orders(on_date, status, limit)` | `GET /wc/v3/orders` | `on_date` = `today` / `yesterday` / `YYYY-MM-DD` in the store timezone (Asia/Kolkata), converted to UTC bounds with `dates_are_gmt=true` |
| `get_order(order_id)` | `GET /wc/v3/orders/{id}` | Line items, customer name and city only (no email, phone, or street) |
| `update_order_status(order_id, status)` | `GET` then `PUT /wc/v3/orders/{id}` | Returns `previous_status` and new `status` |

Argument limits (e.g. `limit` 1–100, allowed statuses) are part of each tool's JSON Schema, so clients and the
LLM see them, and the SDK rejects bad arguments before our code runs. Anticipated failures raise `ToolError`, whose
message reaches the caller (`is_error: true`), e.g. `WooCommerce returned HTTP 404: Invalid ID.` Any other exception
would only show "Error executing tool …".

## How it talks to the client (gateway): MCP over Streamable HTTP

- Endpoint `POST /mcp` on the ClusterIP Service `urumi-woocommerce-mcp:8000`. No Ingress: unreachable from outside.
- **Stateless** (`stateless_http=True`) with **JSON responses** (`json_response=True`): every request stands alone,
  so any replica can answer and a restart loses nothing. Our only client, the gateway, opens a short connection per
  call. Trade-off: no server-initiated messages (progress, sampling), which these tools do not need.
- **DNS-rebinding protection:** only `Host` values naming our Service (or localhost for `kubectl port-forward`) are
  accepted; anything else gets `421 Misdirected Request`.
- **NetworkPolicy:** only Pods labelled as the gateway may connect.

## How it talks to the store: WooCommerce REST API v3, and why

- **Why REST:** it is WooCommerce's documented, versioned interface for products and orders. It applies the same
  validation, hooks, and stock/email logic as wp-admin, and keeps this service independent of WordPress internals.
  Rejected alternatives: direct SQL (bypasses WooCommerce logic and couples us to its schema), the Store API (for
  shoppers' carts, not admin operations), WP-CLI (needs to run inside the WordPress Pod).
- **Address:** `http://urumi-wordpress` (in-cluster Service). The public URL is used only to rewrite returned links
  (`STORE_PUBLIC_URL`).
- **Credentials:** a REST key pair from the Secret `urumi-store`, injected as environment variables. The setup Job
  registers it for a dedicated `urumi-mcp` user with the `shop_manager` role, not the administrator.
- **HTTPS quirk:** WooCommerce accepts Basic-auth REST keys only on HTTPS. In-cluster traffic is plain HTTP, so the
  server sends `X-Forwarded-Proto: https`, as a TLS-terminating proxy would (verified: 401 without it, 200 with it).
  Side effect: WordPress then builds `https://` links, which `_public_url()` rewrites onto the real storefront URL.
- **Timeouts:** each REST call is limited to `WOOCOMMERCE_TIMEOUT_SECONDS` (15 s) and fails with a clear message.

## Configuration (environment, set by the Helm chart)

`WOOCOMMERCE_URL`, `WOOCOMMERCE_CONSUMER_KEY`, `WOOCOMMERCE_CONSUMER_SECRET` (from the Secret),
`WOOCOMMERCE_TIMEOUT_SECONDS`, `STORE_PUBLIC_URL`, `STORE_TIMEZONE`, `MCP_ALLOWED_HOSTS`.

## Tests

```sh
.venv/bin/python -m pytest services/woocommerce-mcp/tests -q
```

`tests/fake_woocommerce.py` is a fake WooCommerce REST API with the real quirks (Basic auth only when the request
claims HTTPS, internal `https://` permalinks, naive-UTC `date_created_gmt`, `{code, message}` errors) and a
`/_last` endpoint that returns the most recent request. The tests start it and the MCP server as real processes
and check the five tools' schemas, the auth and `X-Forwarded-Proto` headers, public-URL rewriting, the UTC
bounds sent for `on_date=today`, data minimisation in `get_order`, readable errors (404, timeout, bad input),
and the Host-header check.

## Run it locally against the cluster's store

```sh
kubectl -n urumi port-forward svc/urumi-wordpress 18080:80 &
export WOOCOMMERCE_URL=http://127.0.0.1:18080
export WOOCOMMERCE_CONSUMER_KEY=$(kubectl -n urumi get secret urumi-store -o jsonpath='{.data.woocommerce-consumer-key}' | base64 -d)
export WOOCOMMERCE_CONSUMER_SECRET=$(kubectl -n urumi get secret urumi-store -o jsonpath='{.data.woocommerce-consumer-secret}' | base64 -d)
cd services/woocommerce-mcp && ../../.venv/bin/uvicorn app.server:app --port 8001
# another terminal, from the project root:
.venv/bin/python scripts/mcp_call.py http://localhost:8001/mcp
.venv/bin/python scripts/mcp_call.py http://localhost:8001/mcp list_orders '{"on_date":"today"}'
```
