# Helm chart `urumi`

One chart for the whole platform: the WooCommerce store (WordPress + MariaDB + setup Job), our WooCommerce MCP
server, two demo MCP servers, the gateway with its Postgres, and the dashboard. `values.yaml` holds the defaults;
`values-local.yaml` (Kind) and `values-prod.yaml` (k3s VPS) override only what differs per environment. No
application secrets belong in any values file: the chart generates them once (`lookup` keeps them stable across
upgrades), or production points at Secrets you created (`*.existingSecret`).

| Template | Creates |
|---|---|
| `store-*.yaml`, `files/` | WordPress Deployment + PVC + Ingress, MariaDB StatefulSet, generated Secret, setup Job (WP-CLI; WooCommerce and Storefront versions pinned in values) |
| `woocommerce-mcp.yaml`, `demo-mcp.yaml` | Internal MCP servers: Deployment + ClusterIP Service + ingress NetworkPolicy (gateway only) |
| `gateway.yaml`, `gateway-secret.yaml`, `postgres.yaml` | Gateway Deployment/Service/Ingress (`/api`, `/mcp`), generated Secret, Postgres StatefulSet |
| `dashboard.yaml` | nginx Deployment/Service/Ingress (`/`) on the gateway host |
| `networkpolicy-default-deny.yaml`, `store-networkpolicy.yaml` | Deny-by-default in both directions plus per-component allows; ACME solver allow when TLS is on |
| `ingress-tls.yaml` | Traefik Middlewares for TLS deployments: HTTP→HTTPS redirect and HSTS |
| `backup.yaml` | Nightly CronJob + PVC: `pg_dump`, `mariadb-dump`, `wp-content` archive, pruned after `backups.keepDays` |
| `_helpers.tpl` | Names, labels, public URLs, Secret lookup, shared Ingress annotations |

Release discipline: `Chart.yaml` `appVersion` == the image tags in `values-prod.yaml` == the git tag CI builds
from (`v0.8.1` → `:0.8.1`). CI fails if any of the three disagree.

```sh
helm lint deploy/helm/urumi -f deploy/helm/urumi/values-prod.yaml
helm template urumi deploy/helm/urumi -n urumi -f deploy/helm/urumi/values-local.yaml | less
helm upgrade --install urumi deploy/helm/urumi -n urumi -f deploy/helm/urumi/values-local.yaml --wait --wait-for-jobs
```
