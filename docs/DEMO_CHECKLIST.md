# Definition-of-done demo checklist

The brief's must-test flow, with two different users, as exact steps. Use it to rehearse and to record the demo
video. Each step says **do**, **expect**, and what to **point at**.

Automated equivalent (same checks via API/MCP/kubectl, PASS/FAIL per step, cleans up after itself):

```sh
.venv/bin/python scripts/verify_dod.py            # ~4 min (waits out the Gemini free-tier rate limit)
.venv/bin/python scripts/verify_dod.py --preflight
```

## 0. Before you start (5 min)

| | Do | Expect |
|---|---|---|
| a | Docker Desktop running; `docker start urumi-control-plane` if the cluster was stopped | |
| b | `kubectl -n urumi delete pod --field-selector=status.phase==Failed` | Clears Pods left by a backup retry (the nightly CronJob retries once; a failed first attempt stays for its log and would fail the next check) |
| c | `.venv/bin/python scripts/verify_dod.py --preflight` | 5/5 PASS (Pods ready, storefront, dashboard, `/mcp` rejects no-key, 3 upstreams Connected) |
| d | Admin key (prints in **your** terminal only): `kubectl -n urumi get secret urumi-gateway -o jsonpath='{.data.bootstrap-admin-key}' \| base64 -d; echo` | `urm_…` |
| e | WordPress admin password: `kubectl -n urumi get secret urumi-store -o jsonpath='{.data.wordpress-admin-password}' \| base64 -d; echo` | |
| f | Windows: **A** = normal browser window (dashboard), **Shopper** = private window (storefront), **B** = Claude Code desktop, plus a terminal | |

Gemini free tier allows only a few requests per minute: leave ~30–60 s between assistant prompts.

## 1. Store: a shopper orders, the owner sees it

| | Do | Expect / point at |
|---|---|---|
| 1.1 | Shopper window → `http://store.urumi.test:8880` → Shop → **Urumi Coffee Mug** → Add to cart → Checkout | Storefront served from Kubernetes via Ingress |
| 1.2 | Fill any address → **Cash on delivery** → Place order | "Order received", order number N1 |
| 1.3 | A window → `http://store.urumi.test:8880/wp-admin` (user `admin`) → WooCommerce → Orders | Order N1, *Processing* |

## 2. Admin sets up the team

| | Do | Expect / point at |
|---|---|---|
| 2.1 | A window → `http://app.urumi.test:8880` → sign in with the **admin key** | Overview: 3/3 servers Connected, 8 tools |
| 2.2 | **MCP servers** | WooCommerce (pre-registered), Store Info, Shipping Info; tools prefixed `woocommerce__…`; 🔒 on Shipping = credential stored encrypted, never shown |
| 2.3 | **Team access** → invite **User A** (`a@demo.test`, Member) | Green banner with A's key, shown once → copy it |
| 2.4 | Invite **User B** (`b@demo.test`, Member) | Copy B's key |

## 3. User A: the assistant

| | Do | Expect / point at |
|---|---|---|
| 3.1 | Sign out → sign in with **A's key** | Member view: no Team page, no server controls |
| 3.2 | **AI assistant** → "Create a product called "Blue Ceramic Mug" priced at 299." | Tool chip `woocommerce__create_product` (expand: arguments); reply confirms |
| 3.3 | Shopper window → refresh Shop | **Blue Ceramic Mug, ₹299** is listed |
| 3.4 | Shopper buys it (COD) | Order N2 |
| 3.5 | A → assistant: "Show today's orders." | Chip `woocommerce__list_orders {on_date: today}`; reply lists N2 (and N1 if today) |

## 4. User B: external MCP client (Claude Code)

| | Do | Expect / point at |
|---|---|---|
| 4.1 | Terminal: `bash scripts/connect-claude-code.sh` → paste **B's key** at the hidden prompt | `urumi: http://app.urumi.test:8880/mcp (HTTP) - ✔ Connected` |
| 4.2 | Claude Code desktop: **new session** in folder `urumi-mcp-gateway` → "What tools do you have from urumi?" | 7 tools: `mcp__urumi__woocommerce__…`, `…store-info__…`, `…shipping-info__…` (`update_order_status` is admin-only, so members don't see it; the admin sees 8) |
| 4.3 | "List today's WooCommerce orders." (approve the tool) | Today's orders incl. N2 |
| 4.4 | "For the newest order today, estimate shipping to its city." | Calls `woocommerce__get_order` **and** `shipping-info__estimate_shipping` — one prompt, two upstreams |

B never needed the shipping server's token or the WooCommerce key — only the gateway URL and B's own key.
Alternative client: MCP Inspector (`npx @modelcontextprotocol/inspector`, Streamable HTTP, header
`Authorization: Bearer <B's key>`); setup snippets are on the dashboard's **Keys & clients** page.

## 5. Revoke User B

| | Do | Expect / point at |
|---|---|---|
| 5.1 | Admin → Team access → User B → **Revoke all keys** | B: 0 active keys |
| 5.2 | Claude Code: ask B's session anything that needs a tool (or `claude mcp list`) | Rejected: 401 / "Failed to connect" |

## 6. Disable an upstream

| | Do | Expect / point at |
|---|---|---|
| 6.1 | Admin → MCP servers → **Shipping Info → Disable** | Badge *Disabled*; its tool chips vanish |
| 6.2 | A → assistant: "Estimate shipping to Pune for an order of 500." | No shipping tool available; the assistant says it cannot estimate |
| 6.3 | (External client view) a fresh `tools/list` no longer contains `shipping-info__…` | |
| 6.4 | Admin → **Enable** Shipping Info | Tools back |

## 7. Kill an upstream Pod

| | Do | Expect / point at |
|---|---|---|
| 7.1 | Terminal: `kubectl -n urumi delete pod -l app.kubernetes.io/component=store-info-mcp` then `kubectl -n urumi get pods -w` | A new Pod appears within seconds — the Deployment self-heals (Ctrl-C to stop watching) |
| 7.2 | Sustained outage: `kubectl -n urumi scale deploy/urumi-shipping-info-mcp --replicas=0` | |
| 7.3 | Dashboard → MCP servers (refreshes every 10 s) | Shipping Info **Unreachable** — "could not connect…"/"timed out"; others Connected |
| 7.4 | A → assistant: "What is the returns policy?" and "Show today's orders." | Both work: other upstreams unaffected |
| 7.5 | `kubectl -n urumi scale deploy/urumi-shipping-info-mcp --replicas=1` | Back to Connected |

## 8. Audit log

| | Do | Expect / point at |
|---|---|---|
| 8.1 | Admin → **Audit log → Tool calls** | A's calls *via assistant*, B's *via MCP client*, each with server, tool, time, duration, result; failures with reason (disabled / unreachable) |
| 8.2 | Filter by user **User B** | Only B's calls |
| 8.3 | **Admin actions** tab | Invites of A and B, B's keys revoked, Shipping Info disabled/enabled — all by the admin |

## 9. Hardening (stand-out items, optional in the video)

| | Do | Expect / point at |
|---|---|---|
| 9.1 | Admin → MCP servers → WooCommerce → **Edit** → tick *admins only* on `create_product` → Save | 🔒 on the chip; Admin actions: `upstream.tool_policy` |
| 9.2 | B's (or A's) client: list tools again; call `woocommerce__create_product` by name | Gone from the list; direct call → "restricted to admins" (audited) |
| 9.3 | Untick it again (A needs it for §3) | |
| 9.4 | `kubectl -n urumi get networkpolicy` | `default-deny` + per-component allows |
| 9.5 | `kubectl -n urumi exec deploy/urumi-gateway -- python -c "import socket; s=socket.socket(); s.settimeout(3); s.connect(('169.254.169.254',80))"` | TimeoutError: the gateway cannot reach cloud metadata even if the SSRF check were bypassed |
| 9.6 | Rate limits: 60 tool calls/min and 10 assistant messages/min per user (`gateway.rateLimits`) | Exceeding → "rate limit exceeded … retry in Ns", audited |

## After a rehearsal (reset)

- Shipping Info enabled and scaled to 1 (step 6.4, 7.5).
- To re-run with the same people: Team access → User B → **Keys → Issue new key**, then `bash scripts/connect-claude-code.sh`.
- Or invite new users (emails must be unique) and deactivate the old ones.
- Test products can be trashed in wp-admin → Products; test orders can be cancelled there or via `woocommerce__update_order_status`.
