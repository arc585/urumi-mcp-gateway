# Operations: upgrades, rollback, backups, rotation

Everything here works the same locally (`-f values-local.yaml`) and on the VPS (`-f values-prod.yaml`).

## How Helm upgrades and rollbacks work

- `helm upgrade` renders the chart with the values and applies the difference; each upgrade is a numbered
  **revision** (`helm history urumi -n urumi`).
- `helm rollback urumi <N>` re-applies revision N's rendered manifests **as a new revision** (history is never
  rewritten).
- Kubernetes then rolls the changed workloads: Deployments create new Pods before removing old ones (a 5 s
  `preStop` pause keeps in-flight requests safe); WordPress uses `Recreate` (its disk attaches to one node);
  StatefulSets replace their Pod and re-attach its PVC.

What a rollback **does not** undo:

| Not rolled back | Why / what to do |
|---|---|
| Data in PVCs (orders, products, users, keys, audit) | Volumes are not part of a release. Take a backup **before** risky upgrades. |
| Database schema changes | WooCommerce/WordPress migrate their DB on upgrade; downgrading code over a migrated DB can break. Restore the backup instead. |
| Code behind a reused image tag | If two revisions both say `:dev`, both run whatever `:dev` is now. Production uses a new immutable tag per build (`0.7.0`, `0.7.1`), so rollback restores the old code. |
| Secrets you created (`existingSecret`, `urumi-llm`) | Not managed by Helm. Generated Secrets are kept on purpose (`resource-policy: keep`). |

### Demonstrated on the local cluster

```text
$ diff <(helm get manifest urumi -n urumi --revision 8) <(helm get manifest urumi -n urumi --revision 9)
<               value: "gemini-2.5-flash"
>               value: "gemini-3.5-flash"
>             - name: LLM_FALLBACK_MODELS
>               value: "gemini-3.1-flash-lite"

$ helm rollback urumi 8 -n urumi --wait      → revision 11 "Rollback to 8": gateway env LLM_MODEL=gemini-2.5-flash, no fallback
  storefront 200, products and orders untouched
$ helm rollback urumi 10 -n urumi --wait     → revision 12 "Rollback to 10": back to gemini-3.5-flash + fallback
```

## Upgrade procedures

Always: `helm template … | less` (review), back up (below), upgrade, watch, verify
(`.venv/bin/python scripts/verify_dod.py --preflight`), roll back if needed.

| Component | How to upgrade | Watch | Roll back |
|---|---|---|---|
| Gateway / MCP servers / dashboard | Build and push a new tag (CI on `git tag vX.Y.Z`), set `*.image.tag`, `helm upgrade` | `kubectl -n urumi rollout status deploy/urumi-gateway` | `helm rollback urumi <rev>` (restores the old tag) |
| WordPress core | Bump `store.wordpress.image.tag` (e.g. `7.1.4-php8.4-apache`), `helm upgrade`. The image updates core files on the volume if newer. | `kubectl -n urumi logs deploy/urumi-wordpress` | Restore the volume/DB backup (core files and DB are migrated) |
| WooCommerce / Storefront | Bump `store.setup.woocommerceVersion` / `storefrontVersion`, `helm upgrade`: the setup Job installs exactly that version (`--force`) and runs `wp wc update`. They live on the WordPress volume, not in an image, so the pin is what makes installs reproducible. | `kubectl -n urumi logs job/urumi-store-setup`, wp-admin → WooCommerce → Status | Set the previous pin and upgrade again; restore the DB backup if WooCommerce migrated its tables |
| MariaDB | Minor versions: bump `store.mariadb.image.tag` (it upgrades the data files on start); major: dump → restore into a new volume | `kubectl -n urumi logs urumi-mariadb-0` | Restore dump |
| Postgres | Minor: bump `postgres.image.tag`. **Major (17→18) cannot reuse the data directory:** `pg_dump` → fresh volume → restore | `kubectl -n urumi logs urumi-postgres-0` | Restore dump |
| Chart only (values/templates) | `helm upgrade`; the setup Job re-runs (idempotent) | `helm history`, `kubectl get pods -w` | `helm rollback` |

Rebuilt the same `:dev` tag locally? Kubernetes cannot see the change: `kubectl -n urumi rollout restart deploy/<name>`.

## Backups (verified on the local cluster)

```sh
D=$(date +%F)
# Store database (orders, products, WordPress settings) — consistent snapshot of InnoDB tables
kubectl -n urumi exec urumi-mariadb-0 -- sh -c 'mariadb-dump -uroot -p"$MARIADB_ROOT_PASSWORD" --single-transaction wordpress' > store-$D.sql
# Gateway database (users, key HASHES, ENCRYPTED upstream credentials, audit log)
kubectl -n urumi exec urumi-postgres-0 -- pg_dump -U gateway gateway > gateway-$D.sql
# WordPress content (plugins, themes, uploads)
kubectl -n urumi exec deploy/urumi-wordpress -- tar czf - -C /var/www/html wp-content > wp-content-$D.tgz
```

Treat backups as secrets: the store dump has customer data, and the gateway dump is useless **without** the
Secret `urumi-gateway(-credentials)` (pepper and Fernet key) — back that Secret up separately and securely, or the
restored hashes and ciphertexts cannot be used.

Restore (example, store DB): `kubectl -n urumi exec -i urumi-mariadb-0 -- sh -c 'mariadb -uroot -p"$MARIADB_ROOT_PASSWORD" wordpress' < store-YYYY-MM-DD.sql`.

## Rotating secrets

| Secret | Rotate by | Effect |
|---|---|---|
| LLM key (`urumi-llm`) | delete + create the Secret, `kubectl -n urumi rollout restart deploy/urumi-gateway` | none for users |
| A member's API key | dashboard: revoke key, generate/issue a new one | only that key stops working, immediately |
| Upstream credential | dashboard: Edit server → new credential value | used on the next call |
| WooCommerce REST key | change the two keys in the store Secret, `helm upgrade` (the setup Job re-registers them), restart `urumi-woocommerce-mcp` | |
| `api-key-pepper` | change + restart gateway | **invalidates every member key** (all hashes change) — re-issue keys |
| `upstream-encryption-key` | not supported in place (would need re-encryption with the old and new key, e.g. MultiFernet) | documented limit |

## Everyday troubleshooting

```sh
kubectl -n urumi get pods                       # anything not Running/Ready?
kubectl -n urumi describe pod <pod>             # events: image pulls, probe failures, OOMKilled, scheduling
kubectl -n urumi logs deploy/urumi-gateway      # gateway: auth failures, upstream errors, DB waits
kubectl -n urumi logs job/urumi-store-setup     # store setup
kubectl -n urumi get events --sort-by=.lastTimestamp | tail
.venv/bin/python scripts/verify_dod.py --preflight
```

| Symptom | Likely cause |
|---|---|
| Dashboard shows an upstream Unreachable | its Pod is down (`get pods`), or its NetworkPolicy blocks the gateway |
| Chat answers 503 | Secret `urumi-llm` missing, or the gateway was not restarted after creating it |
| Chat answers 502 with HTTP 429/402 | provider quota/billing (the provider's message is shown) |
| `docker build` hangs on `FROM` (this Mac) | Docker Desktop pull bug: `sh scripts/load-base-images.sh` |
| Certificate not READY (VPS) | DNS not pointing at the VPS yet, or port 80 closed (HTTP-01 challenge) |
