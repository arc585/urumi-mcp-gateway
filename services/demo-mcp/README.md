# Demo MCP upstreams

Two small MCP servers (one image, `urumi-demo-mcp`) make the "at least three upstreams" requirement
reproducible without third-party accounts:

| Upstream id | Module | Tools | Credential |
|---|---|---|---|
| `store-info` | `app.server:app` | `lookup_store_info(topic)`, `list_store_info_topics()` | none |
| `shipping-info` | `app.shipping:app` | `estimate_shipping(destination_city, order_total)` | `Authorization: Bearer <token>` (`REQUIRED_BEARER_TOKEN`) |

- Both speak stateless Streamable HTTP at `/mcp`, with `/health/live` and `/health/ready`.
- The shipping token comes from a Kubernetes Secret. The gateway stores it encrypted and attaches it on its
  server-to-server calls; members and their MCP clients never see it. Calling the server without it → 401.
- `DEMO_DELAY_SECONDS` makes every tool slow, to demonstrate that one slow upstream times out without affecting others.
- Cross-upstream prompt: "Estimate delivery for today's orders" → `woocommerce__list_orders` / `get_order`
  (city, total) + `shipping-info__estimate_shipping`.
