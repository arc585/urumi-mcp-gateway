# Demo video outline (~15 minutes)

Follows the brief's six required sections. The live flow is `docs/DEMO_CHECKLIST.md`; run
`.venv/bin/python scripts/verify_dod.py --preflight` just before recording. Leave 30–60 s between assistant prompts
(Gemini free tier). Never show a key or password on screen: read them in a terminal off-camera.

## 1. System design and implementation (≈4 min, mostly live)

- 30 s: the diagram in `docs/ARCHITECTURE.md` — store, our WooCommerce MCP server, gateway (+ dashboard, chat), two
  demo MCP servers; "nothing talks to an upstream except the gateway".
- Live definition-of-done flow with two users (checklist §1–§8): storefront order → wp-admin; admin invites A and B;
  A's assistant creates a product → storefront → A buys it → "today's orders"; B in Claude Code lists tools, queries
  orders, one prompt across WooCommerce + shipping; revoke B; disable Shipping; kill a Pod; audit log.

## 2. Kubernetes (≈3 min)

```sh
kubectl get ns                                        # urumi, traefik (k3s: kube-system)
kubectl -n urumi get deploy,statefulset,job,svc,ingress,pvc,networkpolicy
kubectl -n urumi get secrets                          # names only; values never shown
kubectl -n urumi describe pod urumi-mariadb-0 | sed -n '/Limits/,/Environment/p'    # requests/limits, probes
helm template urumi deploy/helm/urumi -n urumi -f deploy/helm/urumi/values-local.yaml -s templates/gateway.yaml | less
```

Say: what is public (store, dashboard, `/api`, `/mcp` via Ingress) vs internal (MCP servers, databases: ClusterIP
only), enforced by **no Ingress + NetworkPolicies** (show one policy; mention the verified "random Pod BLOCKED").
Volumes: MariaDB/Postgres StatefulSets + WordPress PVC; deleting a DB Pod kept the data.

## 3. MCP internals (≈3 min)

- `tools/list`: concurrent discovery, `<upstream>__<tool>` naming and why the split is unambiguous.
- One call from Claude Code to the store and back: Ingress → gateway auth (hash lookup) → split name → decrypt
  credential → Streamable HTTP to `urumi-woocommerce-mcp` → REST `/wp-json/wc/v3/orders` with the store key →
  MariaDB → result → audit row (`docs/03-mcp-and-tool-routing.md` has the drawing).
- Collisions, timeouts (connect 5 s, discovery 5 s, call 30 s), dead upstream: show the Unreachable badge and that
  other tools answered in under a second; cached failures don't slow listings.

## 4. Security posture (≈2 min)

- Keys: shown once, HMAC + pepper in Postgres (show `api_keys` columns: prefix + hash only); upstream credentials
  Fernet-encrypted (show `gAAAAA…`), never returned (`has_credential`).
- Member vs admin: member UI has no admin controls, and the API returns 403 even from dev tools.
- Malicious/compromised upstream: SSRF guard (try `https://169.254.169.254` in the add-server form), untrusted tool
  data in the chat, least privilege, audit, disable instantly. Point to `docs/THREAT_MODEL.md` §1–§4.

## 5. Chat agent (≈1.5 min)

- Expand a tool chip (arguments, duration); audit shows `via assistant` under the signed-in user.
- Explain the loop: model → tool_calls → `call_tool_as(user, …)` → labelled untrusted results → answer; max 6
  rounds; the LLM key lives only in Secret `urumi-llm`; fallback model on quota errors.

## 6. Local → VPS production story (≈1.5 min)

- Side-by-side `values-local.yaml` vs `values-prod.yaml`: hosts, TLS/cert-manager, `kube-system` ingress namespace,
  `local-path`, `existingSecret`s, GHCR immutable tags (table in `docs/PRODUCTION.md`).
- Upgrade/rollback: `helm history urumi -n urumi`; `diff` of two revisions' manifests; `helm rollback` creates a new
  revision; what rollback does not undo (data, schema, reused tags) — `docs/OPERATIONS.md`.
- If deployed: open `https://app.<domain>` and show the certificate; otherwise show the server-side dry-run of the
  prod render.

## Closing (15 s)

What I'd do next: confirm-before-write in chat driven by MCP tool annotations (`readOnlyHint`/`destructiveHint`), a
shared rate-limit store for several gateway replicas, Alembic migrations run by a pre-upgrade Job, Prometheus
metrics, multi-store provisioning.
