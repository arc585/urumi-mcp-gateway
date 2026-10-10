# 01 — Kubernetes foundations (Milestone 1)

## The words, in plain language

| Term | What it is | Where you saw it |
|---|---|---|
| **Image** | A frozen package: an app plus everything it needs. | `traefik/whoami:v1.11`, `kindest/node:v1.37.0` |
| **Container** | One running copy of an image. | Every Pod runs one or more. |
| **Cluster** | Machines Kubernetes manages as one computer. You declare *desired state*; Kubernetes keeps reality matching it. | `kind get clusters` → `urumi` |
| **Control plane** | The cluster's brain: `kube-apiserver` (what `kubectl` talks to), `etcd` (stored desired state), `kube-scheduler` (places Pods on nodes), `kube-controller-manager` (loops that fix drift). | Pods in `kube-system` |
| **Node** | A machine that runs Pods. With Kind, a node is a Docker container on your Mac. | `urumi-control-plane` (control plane *and* worker) |
| **Pod** | The smallest thing Kubernetes runs: one or more containers sharing an IP. Pods are disposable. | `hello-6c4c8c84f-…` |
| **Deployment** | "Keep N copies of this Pod running." Manages a ReplicaSet, which creates/replaces Pods. | `deployment/hello` |
| **Service** | A stable name + virtual IP in front of changing Pods. Other apps call `http://hello`, not a Pod IP. | `service/hello` |
| **EndpointSlice** | The live list of Pod IPs behind a Service, updated automatically. | `10.244.0.6,10.244.0.7` |
| **Ingress** | An HTTP routing *rule* from outside into a Service. | `hello.urumi.test → hello:80` |
| **Ingress controller** | The proxy that implements Ingress rules. We use Traefik (the k3s default). | `traefik` namespace |
| **Namespace** | A folder inside the cluster for grouping and isolating resources. | `kube-system`, `traefik`, later `urumi` |
| **StorageClass** | How the cluster creates disks for PersistentVolumeClaims. | `standard (default)` → `rancher.io/local-path` |
| **NetworkPolicy** | A firewall rule for Pods. | `only-friends` test |
| **Helm release** | A named, versioned install of a chart. | `helm list -A`, `helm history traefik -n traefik` |

## How traffic reaches a Pod on this laptop

```text
curl / browser  →  127.0.0.1:8880 (Mac)
  →  Kind node container port 80          (kind-config.yaml extraPortMappings)
  →  Traefik Pod via hostPort 80           (deploy/kind/traefik-values.yaml)
  →  Ingress rule picks a Service by Host  (e.g. hello.urumi.test)
  →  Service → EndpointSlice → a Pod IP
```

Host ports 80/443 on this Mac are used by another project, so the cluster is published on
**127.0.0.1:8880** (HTTP) and **:8843** (HTTPS), reachable only from this machine. Local URLs therefore
look like `http://store.urumi.test:8880`. On the VPS, Traefik listens on the normal 80/443.

## Setup commands (run from the project folder)

```sh
# 1. Cluster: one node, ports 8880/8843 published, labelled ingress-ready=true
kind create cluster --config kind-config.yaml

# 2. Ingress controller (Traefik) installed with Helm
helm repo add traefik https://traefik.github.io/charts && helm repo update
helm upgrade --install traefik traefik/traefik --version 41.7.0 \
  -n traefik --create-namespace -f deploy/kind/traefik-values.yaml --wait
```

## Inspecting the cluster

```sh
kind get clusters                       # urumi
kubectl config current-context          # kind-urumi  (which cluster kubectl is talking to)
kubectl get nodes -o wide               # 1 node, Ready, v1.37.0, arm64
kubectl get namespaces
kubectl get pods -A -o wide             # control-plane components run as Pods in kube-system
kubectl get storageclass                # standard (default) → local-path provisioner
kubectl describe node urumi-control-plane   # capacity, labels, what runs there
helm list -A                            # releases: traefik
helm history traefik -n traefik         # revision 1 superseded, 2 deployed
kubectl get ingressclass                # traefik (default)
curl -i http://127.0.0.1:8880/          # 404 from Traefik = reachable, no route matched
```

## What we observed (the self-healing exercise)

1. `kubectl create deployment hello --replicas=2` → a Deployment, a ReplicaSet, two Pods.
2. `kubectl expose` → a Service; its EndpointSlice listed both Pod IPs.
3. `kubectl create ingress` → Traefik routed `Host: hello.urumi.test` to the Service.
4. `kubectl delete pod <one>` → within ~1 second the ReplicaSet created a replacement Pod with a **new IP**;
   the EndpointSlice updated itself; callers using the Service name noticed nothing.
5. A NetworkPolicy allowing only `role=friend` Pods: an unlabelled Pod timed out, a labelled Pod got through.
   So Kind's network plugin (kindnet) **enforces** NetworkPolicies, which we rely on to keep MCP servers internal.
   Lesson: once a Pod is selected by a policy, *everything* not allowed is blocked — including Traefik, so
   policies for public apps must allow the `traefik` namespace.
6. `kubectl delete namespace playground` removed everything inside it.

## Troubleshooting note

The first `kind create cluster` hung while pulling the node image. Docker Desktop's image pulls were stuck
(even `docker pull busybox` hung) while normal internet worked. Restarting Docker Desktop fixed it
(`docker desktop restart`, or `docker desktop start` after it stopped). Containers without a restart
policy in other projects do not come back automatically after such a restart.

The pull hang came back later and survived Docker Desktop restarts, while the Kind node's own containerd could
still pull. Workaround: `sh scripts/load-base-images.sh` fetches the base images through the Kind node and loads
them into Docker, so `docker build` never needs Docker Desktop's pull path.

Helm lesson from the same session: the Traefik chart v41 ignores `service.type`; the setting is
`service.spec.type`. Always check `helm show values <chart> --version <v>` for the version you install.

## Interview check

- Why do apps call a **Service name** instead of a Pod IP? (Pod IPs change on every replacement.)
- What recreated the deleted Pod? (The ReplicaSet controller, owned by the Deployment, reconciling desired = 2.)
- What is the difference between an Ingress and an ingress controller?
- Why is Kind fine locally but not for production? (Single Docker-hosted node, no real disks or public IP.)
- Why Traefik instead of ingress-nginx? (ingress-nginx was retired in 2026; k3s ships Traefik, so local == prod.)
