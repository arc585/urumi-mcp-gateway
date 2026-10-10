# 02 — Helm chart and values

A Helm chart is a set of templates for Kubernetes resources plus a `values.yaml` of defaults. Helm renders the
templates with the values into ordinary YAML, applies it, and records a **release**: a named, numbered install it
can upgrade or roll back later (`helm history urumi -n urumi`).

In this project one chart, `deploy/helm/urumi`, holds the whole platform. Templates are grouped by component
(`store-*.yaml`, `woocommerce-mcp.yaml`, `demo-mcp.yaml`, `gateway.yaml`, `postgres.yaml`, `dashboard.yaml`,
`networkpolicy-*.yaml`, `ingress-tls.yaml`); `_helpers.tpl` holds the shared naming, labels, and Ingress
annotations. `values-local.yaml` and `values-prod.yaml` override only environment-specific choices such as
hostnames, ports, TLS, storage classes, image locations, and which Secrets to use. They are two configurations of
the same chart, not two deployments.

Read `templates/store-wordpress.yaml` top to bottom:

- `replicas`: how many Pods are wanted (1 here: the WordPress disk is ReadWriteOnce, see `04-store-on-kubernetes.md`).
- `selector` and the matching Pod labels: how the Deployment recognises its own Pods. They never change after install.
- `containerPort`, named `http`: the port the app listens on; Services and probes refer to it by name.
- `readinessProbe` / `livenessProbe`: how Kubernetes decides to send traffic, or to restart the container.
- `resources`: `requests` reserve capacity for scheduling; `limits` cap what the container may use.
- `securityContext`: non-root where the image allows it, no privilege escalation, Linux capabilities dropped.

Release discipline: `Chart.yaml` `appVersion` must equal the image tags in `values-prod.yaml`, and CI only builds
images from a git tag `v<appVersion>` (it fails otherwise). `helm lint` and `helm template` run in CI for both
values files.
