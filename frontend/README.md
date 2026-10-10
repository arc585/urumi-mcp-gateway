# Dashboard

React 19 + Vite single-page app for admins and members: MCP server health and management, team and API keys,
client setup, audit logs, and the assistant. It calls only the gateway API (`/api/v1`) on its own origin; upstream
credentials never reach the browser. Explanation and verification: `docs/06-dashboard.md`.

```text
src/api.js              fetch wrapper, sessionStorage key handling
src/App.jsx             session, role-based navigation, health polling
src/components.jsx      shared UI pieces (panels, badges, modal, one-time key reveal)
src/pages/*.jsx         Overview, Servers, Team, Keys, Audit, Assistant
nginx.conf              static serving, /healthz, caching
security-headers.conf   CSP and other headers (included in every location)
```

## Develop locally against the cluster's gateway

```sh
npm ci
npm run dev          # http://localhost:5173 ; /api is proxied to http://app.urumi.test:8880
npm run build        # production files in dist/
```

## Container image

`../frontend/Dockerfile` (build context = repo root): Node builds `dist/`, then unprivileged nginx serves it on
port 8080. In Kubernetes the Ingress sends `/` here and `/api`, `/mcp` to the gateway.
