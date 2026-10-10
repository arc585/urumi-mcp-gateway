# 04 — The store on Kubernetes (Milestone 2)

WordPress + WooCommerce + MariaDB, installed by one Helm chart (`deploy/helm/urumi`) into the `urumi` namespace.

## What runs, and why each piece is that kind of resource

```text
Browser ── http://store.urumi.test:8880 ──► Traefik ──► Ingress urumi-wordpress
                                                         │
                                         Service urumi-wordpress (ClusterIP :80)
                                                         │
                         Deployment urumi-wordpress (1 Pod, strategy Recreate)
                           └─ volume: PVC urumi-wordpress (2Gi) → /var/www/html (core, plugins, uploads)
                                                         │  WORDPRESS_DB_HOST=urumi-mariadb:3306
                         Headless Service urumi-mariadb (clusterIP: None)
                                                         │   NetworkPolicy: only WordPress + setup Job
                         StatefulSet urumi-mariadb (Pod urumi-mariadb-0)
                           └─ volume: PVC data-urumi-mariadb-0 (2Gi) → /var/lib/mysql

Job urumi-store-setup (Helm post-install/post-upgrade hook, WP-CLI) ── same PVC + DB
Secret urumi-store: DB passwords, WP admin password, WooCommerce REST key pair
ConfigMap urumi-store-setup: the setup scripts the Job runs
```

| Resource | Why this kind |
|---|---|
| **StatefulSet** for MariaDB | Stable Pod name (`urumi-mariadb-0`) and its *own* PVC that follows it. After `kubectl delete pod`, the replacement had a new IP but the same name and disk. |
| **Deployment** for WordPress | Web tier; Pods are interchangeable. `strategy: Recreate` because its ReadWriteOnce disk can be attached to one node at a time — a rolling update could deadlock waiting for the disk. |
| **PersistentVolumeClaim** | "I need 2Gi". The default StorageClass (`standard` → local-path on Kind, `local-path` on k3s) created a matching PersistentVolume (a directory on the node). |
| **Headless Service** | `clusterIP: None` gives the database a DNS name without a load-balancing virtual IP — normal for StatefulSets. |
| **Secret** | Generated once by Helm (`randAlphaNum`); `lookup` keeps the same values on `helm upgrade`; `helm.sh/resource-policy: keep` keeps it after uninstall, because the database files were initialised with those passwords. Production uses `store.existingSecret` instead. |
| **ConfigMap** | Non-secret files: `files/store-setup.sh`, `files/ensure-api-key.php`. |
| **Job (Helm hook)** | Runs once to completion after install/upgrade: installs WordPress, Storefront, WooCommerce, sets INR + India, disables "coming soon", enables **Cash on delivery**, creates shop/cart/checkout pages and a sample product, and registers the MCP REST key. Every step checks state first, so re-running is safe. |
| **NetworkPolicy** | Only Pods labelled as WordPress or the setup Job may open MariaDB's port 3306. |

### Probes, requests, limits

- MariaDB readiness: `healthcheck.sh --connect --innodb_initialized`; liveness: `healthcheck.sh --connect`.
- WordPress readiness: `GET /wp-login.php` (runs PHP and touches the DB); liveness: `GET /wp-includes/images/blank.gif`
  (Apache only). A database outage marks WordPress *unready* (out of the Service) instead of restart-looping it.
- Requests/limits per container are in `values.yaml` (`store.*.resources`). A request is reserved for scheduling;
  exceeding the memory limit gets the container killed (OOMKilled).

### The MCP server's WooCommerce key (least privilege)

Instead of a human creating a REST key in wp-admin and copying it around, Helm generates `ck_…`/`cs_…` into the
Secret and the setup Job registers it (hashed, like WooCommerce's own UI) for a dedicated `urumi-mcp` user with the
`shop_manager` role — enough for products and orders, not a full administrator. Rotating = change the Secret,
re-run the Job.

**HTTPS quirk (verified):** WooCommerce accepts key-based Basic auth only when it thinks the request is HTTPS.
Inside the cluster we speak plain HTTP to `http://urumi-wordpress`. Result: Basic auth → **401**; the same request
with `X-Forwarded-Proto: https` → **200** (the official WordPress image trusts that header, as it would from a
TLS-terminating proxy). The MCP server sends that header. Trade-off: in-cluster traffic is unencrypted; it never
leaves the node network, and NetworkPolicies limit who can reach WordPress internally.

## Install and inspect

```sh
kubectl create namespace urumi
helm template urumi deploy/helm/urumi -n urumi -f deploy/helm/urumi/values-local.yaml | less   # read what will be created
helm template urumi deploy/helm/urumi -n urumi -f deploy/helm/urumi/values-local.yaml \
  | kubectl apply --dry-run=server -n urumi -f -       # API server validates every resource, creates nothing
helm upgrade --install urumi deploy/helm/urumi -n urumi -f deploy/helm/urumi/values-local.yaml \
  --wait --wait-for-jobs --timeout 15m

helm list -n urumi
kubectl -n urumi get pods,svc,ingress,pvc,networkpolicy
kubectl get pv                                        # the volumes the PVCs are bound to
kubectl -n urumi describe pod urumi-mariadb-0         # probes, mounts, events
kubectl -n urumi logs job/urumi-store-setup           # what the setup Job did
kubectl -n urumi logs deploy/urumi-wordpress --tail=20
```

What we saw: both PVCs `Bound` (2Gi, `standard`), Pods `Running 1/1`, Job `Completed`, release `deployed`
(revision 1). Through the Ingress: `/` 200, `/shop/` 200 listing "Urumi Coffee Mug", `/cart/` 200, `/checkout/` 302
(empty cart → cart), `/wp-admin/` 302 (→ login), `/wp-json/` 200.

## Verifications we ran

1. **Checkout:** through the WooCommerce Store API (what the checkout page itself uses): add mug → checkout with
   `payment_method: cod` → order **#11**, status `processing`.
2. **Persistence:** deleted `urumi-mariadb-0` and the WordPress Pod. New Pods (new IPs) re-attached the same PVCs;
   order 11 still in MariaDB (`wp_posts`, type `shop_order`), plugin and product still there.
3. **NetworkPolicy:** an unlabelled Pod → `BLOCKED` on `urumi-mariadb:3306`; a Pod carrying WordPress's labels →
   `OPEN`. Lesson: policies trust labels, so the real boundary is *who may create Pods in the namespace* (RBAC).

## Your turn (browser)

1. `http://store.urumi.test:8880` → Shop → add the mug → Checkout → Cash on delivery → Place order.
2. Get the admin password in **your** terminal:
   `kubectl -n urumi get secret urumi-store -o jsonpath='{.data.wordpress-admin-password}' | base64 -d; echo`
3. `http://store.urumi.test:8880/wp-admin` → user `admin` → WooCommerce → Orders → your order is listed.

## Known limits (documented, not hidden)

- No mail server: WordPress/WooCommerce emails fail silently (`sendmail: connection refused` in the Job log).
- WordPress runs Apache as root-then-www-data (official image); MariaDB runs fully non-root (uid 999).
- Single replica of each; scaling WordPress needs ReadWriteMany storage or object storage for uploads.

## Interview check

- StatefulSet vs Deployment — which for the database, and what did deleting its Pod show?
- What happens to data on `helm uninstall`? (PVCs and the Secret are kept on purpose; `kubectl delete pvc` destroys data.)
- Why must the generated passwords stay stable across upgrades, and how does `lookup` do that?
- Why does the WordPress liveness probe *not* check the database?
- Why can't the MCP server's key do everything an admin can?
- What does a NetworkPolicy actually trust, and what else is needed to make it a real boundary?
