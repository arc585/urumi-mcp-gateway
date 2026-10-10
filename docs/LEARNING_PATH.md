# Kubernetes learning path

Notes written milestone by milestone while building (read in order):

| # | Note | Covers |
|---|---|---|
| 1 | `01-kubernetes-foundations.md` | cluster, node, Pod, Deployment, Service, Ingress, namespace, NetworkPolicy, Kind, Traefik |
| 2 | `02-helm-chart.md`, `04-store-on-kubernetes.md` | Helm, StatefulSet, PVC/StorageClass, Secrets, probes, Jobs/hooks |
| 3 | `03-mcp-and-tool-routing.md` | MCP, Streamable HTTP, our WooCommerce MCP server |
| 4 | `05-gateway-and-access-control.md` | aggregation, routing, keys, encryption, isolation, audit |
| 5 | `06-dashboard.md` | static app on Kubernetes, path routing, CSP |
| 6 | `07-chat-agent.md` | LLM tool loop, prompt injection, provider quotas |
| 7 | `DEMO_CHECKLIST.md` | the definition of done, step by step |
| 8 | `PRODUCTION.md`, `OPERATIONS.md` | k3s, TLS, existing Secrets, upgrades, rollback, backups |

The original plan follows.

This project teaches Kubernetes by building the assignment. Each lesson answers: what problem does this resource solve, where does it appear in this system, and how can I inspect it?

## Lesson 1 — containers and a cluster

- A **container image** is a packaged app and its dependencies.
- A **container** is one running copy of that image.
- A **Kubernetes cluster** is the control plane plus worker machines that schedule and supervise containers.
- **Kind** creates a small Kubernetes cluster using Docker containers as its nodes. It is local practice, not a production hosting service.
- `kubectl` is the command-line client that asks the cluster what exists or requests changes.

## Lesson 2 — workload and network resources

- **Pod:** one or more tightly coupled containers sharing a network and lifecycle.
- **Deployment:** keeps a chosen number of interchangeable Pods running; if a Pod dies, Kubernetes creates another.
- **Service:** stable DNS name and virtual address for the current Pods behind it. App-to-app traffic uses Services, not Pod IPs.
- **Ingress:** HTTP routing from outside the cluster to Services. Our design exposes the storefront and dashboard/gateway; internal MCP services have no public Ingress.

## Lesson 3 — configuration, secrets, and health

- **ConfigMap:** non-secret settings such as a service URL.
- **Secret:** credentials mounted or injected into a workload. It is not automatically encrypted in every cluster, so production needs an explicit secret-management strategy.
- **Readiness probe:** tells Kubernetes whether a Pod should receive traffic yet.
- **Liveness probe:** tells Kubernetes whether to restart a stuck process.
- **Requests and limits:** a request is the amount reserved for scheduling; a limit caps consumption.

## Lesson 4 — data and storage

- **PersistentVolumeClaim (PVC):** a workload's request for durable storage.
- A database Pod can be replaced; its data should live on a PVC, not only in the container filesystem.
- WordPress uploads also need persistence. We will inspect the PVCs before calling the store stateful.

## Lesson 5 — Helm

A **Helm chart** is a reusable package of Kubernetes resource templates. `values.yaml` supplies defaults; local and production values override details like hostnames and storage classes. Helm renders the templates and tracks a release so the same application can be upgraded or rolled back.

## Our first cluster exercise

Before the first deployment, we will create a local Kind cluster and look at its nodes and namespaces. Then we will deploy a tiny web workload and inspect its Pod, Deployment, and Service. Only after those are visible will we add the store.

## Walkthrough questions

1. If a Pod IP changes, what keeps another component from needing a code change?
2. If the database Pod restarts, where does its data come from?
3. What does an unready Pod signal to Kubernetes?
4. Which endpoints are reachable from a laptop, and which only from inside the cluster?
5. What changes in the production Helm values, and what stays the same?
