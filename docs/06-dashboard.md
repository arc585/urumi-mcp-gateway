# 06 — The dashboard (Milestone 5)

A React (Vite) single-page app, built into static files and served by unprivileged nginx at
`http://app.urumi.test:8880/` (prod: `https://app.<domain>/`). It talks only to the gateway's `/api/v1`.

## How it is wired

```text
Browser ── http://app.urumi.test:8880/ ──► Traefik
             ├─ /api/*, /mcp* ─► Ingress urumi-gateway   ─► Service urumi-gateway   (FastAPI)
             └─ /*           ─► Ingress urumi-dashboard ─► Service urumi-dashboard (nginx, static files)
```

- **One host, path-based routing.** Two Ingress objects share `app.urumi.test`; the longest matching prefix wins,
  so `/api` and `/mcp` reach the gateway and everything else reaches nginx. Same origin ⇒ no CORS for the dashboard.
- **Stateless and secret-free.** The dashboard Pod holds no keys and calls nothing; the browser calls `/api`.
  It runs as uid 101 with a read-only root filesystem (writable `/tmp` only) and a NetworkPolicy that admits only
  the ingress controller.
- **Build:** `node:22-alpine` runs `npm ci && npm run build`; the result is copied into
  `nginxinc/nginx-unprivileged:1.28-alpine` (multi-stage Dockerfile, ~82 MB, no Node in the final image).

## Sign-in and the API key in the browser

- Members sign in with their own API key; the app verifies it with `GET /api/v1/me` before keeping it.
- The key is held in **sessionStorage**: per tab, cleared when the tab closes (not shared across tabs or kept
  for weeks like localStorage).
- Any `401` from the API — key revoked, account deactivated — ends the session immediately with an explanation.
- Risk and mitigation: any script running on the page could read sessionStorage, so nginx sends a strict
  **Content-Security-Policy** (`script-src 'self'`, `connect-src 'self'`, `frame-ancestors 'none'`, no inline
  scripts or styles), plus `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer`.
  nginx gotcha found while testing: an `add_header` inside a `location` silently drops all server-level
  `add_header`s, so the security headers live in a snippet included by every location.

## What each role sees

| Page | Admin | Member |
|---|---|---|
| Overview | servers connected/tools, live upstream health, recent calls (all users), gateway URL | same, own calls |
| MCP servers | status, latency, tools, URL, 🔒 credential marker; **add / edit / enable-disable / remove**; credential write-only | status and tools only (no URLs, no controls) |
| Team access | invite (key shown once), per-user keys (issue/revoke), revoke all, make admin/member, deactivate | *not shown* |
| Keys & clients | own keys (generate/revoke) + Claude Code / Cursor / Inspector snippets | same |
| Audit log | tool calls of everyone (filter by user/server/outcome, live) + **Admin actions** tab | own tool calls |
| AI assistant | chat (completed in Milestone 6) | same |

Hiding pages is only tidiness. The gateway enforces roles: from the member's own browser session,
`GET /api/v1/users` → **403**, `PATCH /api/v1/upstreams/store-info` → **403**, and `/api/v1/upstreams` returns only
`id, name, enabled`.

Health on the Overview and MCP servers pages refreshes every 10 s, so stopping an upstream Pod shows
**Unreachable** without reloading. The audit table refreshes every 5 s while "Live" is ticked.

## Build and deploy

```sh
cd frontend && npm ci && npm run build && cd ..          # optional local check
docker build -f frontend/Dockerfile -t urumi-dashboard:dev .
kind load docker-image urumi-dashboard:dev --name urumi
helm upgrade urumi deploy/helm/urumi -n urumi -f deploy/helm/urumi/values-local.yaml --wait --wait-for-jobs
kubectl -n urumi rollout restart deploy/urumi-dashboard   # only when rebuilding the same :dev tag
```

If `docker build` hangs on `FROM …` (Docker Desktop pull bug on this Mac): `sh scripts/load-base-images.sh`, then
build again. It fetches the official base images through the Kind node and loads them into Docker.

## Verified in the browser (built-in browser pane, throwaway test accounts, deactivated afterwards)

| Check | Result |
|---|---|
| `/` → dashboard, `/api/v1/me` → 401, `/mcp` → 401 | routing correct |
| Response headers | CSP, nosniff, no-referrer, cache-control present |
| Admin overview | 3/3 connected, 8 tools, live health, recent calls |
| Disable Store Info in the UI | badge Disabled, tools gone, toast; re-enable restores; admin action logged |
| Edit Shipping Info | credential shown only as "Stored — leave blank to keep" |
| Add `http://169.254.169.254/…` / `https://169.254.169.254/…` | rejected: "must use HTTPS" / "private, loopback, link-local, or reserved address" |
| Team → User B's keys | prefix, name, last used, status only |
| Member: nav and Servers page | no Team page, no admin buttons, no URLs |
| Member calls admin APIs from dev tools | 403, 403 |
| Revoke member while signed in | page signs out: "Your session ended: this key was revoked…" |

UI bugs found and fixed: error banner lost its red style (CSS override) and was off-screen when scrolled (now
sticky); errors persisted across pages and sign-out (now cleared); emails were capitalised; code snippets broke
mid-URL.

## Interview check

- Why one host with path routing? What decides whether `/api/v1/me` goes to nginx or the gateway?
- Where is authorization enforced, and how did we prove a member cannot bypass the UI?
- Why sessionStorage, what is the remaining risk, and what does the CSP do about it?
- What does the dashboard Pod know or store? (Nothing secret.)
