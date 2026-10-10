# Runbook — where to run what, and the final user journeys

This file grows with each milestone. ✅ = works now, ⏳ = built in a later milestone.

## Where commands run

Everything local runs **on your Mac**. There is no SSH and no "logging into" the cluster: `kubectl` and `helm`
talk to the Kind cluster through the context `kind-urumi`, which `kind create cluster` set up for you.

| Where | What you use it for |
|---|---|
| **A terminal in the project folder** (`cd /Users/arnuv/Desktop/saarthi/urumi-mcp-gateway`) | `kind`, `kubectl`, `helm`, `docker build`. Paths like `deploy/helm/urumi` are relative to this folder. |
| **Any terminal, once** | `sudo` edit of `/etc/hosts` (needs your Mac password). |
| **Browser** | Storefront, WordPress admin, the gateway dashboard and chat. |
| **An MCP client** (Claude Code, Cursor, or MCP Inspector) | User B's journey: connect with only the gateway URL and a key. |
| **On the VPS over SSH** (Milestone 8 only) | Installing k3s; after that you can drive it with `kubectl`/`helm` from the Mac too. |

Sanity check before any `kubectl`/`helm` command — make sure you are pointed at the right cluster:

```sh
kubectl config current-context   # must print kind-urumi locally
```

## Who runs what

- **Claude (me)** runs the build/deploy commands: `docker build`, `kind load`, `helm install/upgrade`, tests, and the
  inspection commands, and shows you the output.
- **You** run anything that needs your password or reveals a secret, so secrets never pass through chat:
  the `/etc/hosts` edit, reading generated passwords/keys out of Secrets, creating the LLM API-key Secret, and the
  browser / MCP-client parts of the demo.

## One-time local setup (you)

```sh
# Lets the browser and MCP clients resolve the local hostnames to the Kind cluster.
echo "127.0.0.1 store.urumi.test app.urumi.test" | sudo tee -a /etc/hosts
```

Local URLs carry `:8880` because ports 80/443 on this Mac are used by another project (see `kind-config.yaml`).

## The final user journeys (definition of done)

| # | Persona | Where | URL / command | Status |
|---|---|---|---|---|
| 1 | **Shopper** | Browser | `http://store.urumi.test:8880` → add to cart → checkout with Cash on delivery | ✅ |
| 2 | **Store owner** | Browser | `http://store.urumi.test:8880/wp-admin` → WooCommerce → Orders (user `admin`, password from the Secret below) | ✅ |
| 3 | **Gateway admin** | Browser | `http://app.urumi.test:8880` → paste admin key → MCP servers (status, add/edit/disable/remove), Team access (invite A and B, issue/revoke keys), Audit log | ✅ |
| 4 | **Member A** | Browser | Dashboard with A's key → AI assistant: "create a product Blue Mug for 299", buy it on the storefront, then "show today's orders" | ✅ |
| 5 | **Member B** | Claude Code / MCP Inspector | URL `http://app.urumi.test:8880/mcp` + header `Authorization: Bearer <B's key>` → list tools, query orders, one prompt using WooCommerce + shipping tools | ✅ (API/CLI) |
| 6 | **Admin revokes B** | Browser, then B's client | Team access → Revoke B → B's next call gets 401 | ✅ |
| 7 | **Admin disables an upstream** | Browser | MCP servers → Disable → its tools vanish from chat and from B's tool list | ✅ |
| 8 | **Operator kills an upstream** | Terminal + browser | `kubectl -n urumi delete pod -l app.kubernetes.io/component=shipping-info-mcp` (or scale to 0) → other tools still work, dashboard shows Unreachable | ✅ |
| 9 | **Auditor** | Browser | Audit log shows every call above with the right user, server, tool, time, duration, outcome | ✅ |

The exact click-by-click script lives in `docs/DEMO_CHECKLIST.md`; `scripts/verify_dod.py` runs the same checks automatically.

## Reading secrets (you, in your own terminal)

These print a credential to *your* terminal only. Don't paste the output into chat or files.

```sh
# WordPress admin password (user: admin)
kubectl -n urumi get secret urumi-store -o jsonpath='{.data.wordpress-admin-password}' | base64 -d; echo

# Gateway bootstrap admin key (first dashboard login, or MCP clients as admin)
kubectl -n urumi get secret urumi-gateway -o jsonpath='{.data.bootstrap-admin-key}' | base64 -d; echo
```

## Everyday inspection (anyone)

```sh
kubectl -n urumi get pods,svc,ingress,pvc          # what is running, how it is exposed, its disks
kubectl -n urumi describe pod <name>               # events: scheduling, image pulls, probe failures
kubectl -n urumi logs deploy/urumi-wordpress       # application logs
kubectl -n urumi logs job/urumi-store-setup        # store setup Job output
helm list -n urumi && helm history urumi -n urumi  # releases and revisions (for rollback)
helm template urumi deploy/helm/urumi -n urumi -f deploy/helm/urumi/values-local.yaml | less   # rendered YAML
```

## Start / stop between sessions

```sh
docker stop urumi-control-plane    # pause the whole cluster (data on PVCs is kept)
docker start urumi-control-plane   # resume; Pods come back on their own
kind delete cluster --name urumi   # destroy everything, including volumes (start over from setup)
```
