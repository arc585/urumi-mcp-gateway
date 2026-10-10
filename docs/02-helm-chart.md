# 02 — Helm chart and values

A Helm chart is a set of templates for Kubernetes resources. In this project one chart, `deploy/helm/urumi`, holds the whole platform; templates are grouped by component (`store-*.yaml` now, gateway/MCP/dashboard later). `values.yaml` supplies defaults. `values-local.yaml` and `values-prod.yaml` override only environment-specific choices such as image location and hostname.

Helm renders those templates into regular Kubernetes YAML and sends the result to the cluster. Helm also records a **release**: the named installation it can update or roll back later.

Try to read `templates/store-wordpress.yaml` top to bottom:

- `replicas` means the number of desired gateway Pods.
- `selector` and matching Pod labels let the Deployment identify its Pods.
- `containerPort` documents the port the app listens on.
- probes define how Kubernetes checks health.
- `resources` sets scheduling requests and runtime limits.
- `securityContext` runs as a non-root user and removes Linux capabilities the app does not need.

The local and production values are two configurations of the same chart. They do not define separate application deployments.
